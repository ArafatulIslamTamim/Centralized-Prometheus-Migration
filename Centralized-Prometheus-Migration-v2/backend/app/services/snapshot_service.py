from __future__ import annotations

import json
import os
import posixpath
import stat
import tempfile
import time
from typing import Any, Dict, List, Optional

from app.models.config_models import AppConfig
from app.services.shell_utils import q
from app.services.ssh_runner import SSHRunner
from app.services.time_utils import utc_iso_from_ms, parse_utc_to_ms

LIST_SNAPSHOT_SCRIPT = r'''
import json, sys
from pathlib import Path
base=Path(sys.argv[1])
out=[]
if base.exists():
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        blocks=[]
        for b in child.iterdir():
            meta=b/'meta.json'
            if b.is_dir() and b.name.startswith('01') and meta.exists():
                try:
                    m=json.loads(meta.read_text())
                    blocks.append({'id': b.name, 'minTime': int(m['minTime']), 'maxTime': int(m['maxTime'])})
                except Exception:
                    pass
        item={'id': child.name, 'path': str(child), 'blockCount': len(blocks)}
        if blocks:
            item['minTime']=min(x['minTime'] for x in blocks)
            item['maxTime']=max(x['maxTime'] for x in blocks)
        out.append(item)
print(json.dumps(out))
'''


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise RuntimeError(f"no JSON object found in output: {text[-1000:]}")


def create_source_snapshot(cfg: AppConfig, source: SSHRunner) -> Dict[str, Any]:
    """Create a source snapshot using the first-GUI style safe method.

    The normal source Prometheus service is NOT started. Instead, this starts a
    temporary Prometheus on 127.0.0.1:<port> with scrape_configs: [] and admin API
    enabled, creates a snapshot, and then stops the temporary process.
    """
    port = int(getattr(cfg.prometheus, "source_temp_snapshot_port", 19090) or 19090)
    tsdb = cfg.prometheus.source_tsdb_path.rstrip("/")
    snap_dir = cfg.prometheus.source_snapshot_dir.rstrip("/")
    service = cfg.prometheus.prometheus_service or "prometheus"
    source_user = cfg.source.user

    script = f'''
set -euo pipefail
TSDB={q(tsdb)}
SNAPDIR={q(snap_dir)}
PORT={port}
SERVICE={q(service)}
SSH_USER={q(source_user)}
TMP_BASE="/tmp/prom_migration_snapshot_${{PORT}}"
CFG="$TMP_BASE/prometheus-no-scrape.yml"
LOG="$TMP_BASE/prometheus-temp.log"
PIDFILE="$TMP_BASE/prometheus-temp.pid"
URL="http://127.0.0.1:${{PORT}}"

mkdir -p "$TMP_BASE"
cat > "$CFG" <<'EOF_CFG'
global:
  scrape_interval: 1h
  evaluation_interval: 1h
scrape_configs: []
EOF_CFG
chmod 0644 "$CFG"

if systemctl is-active --quiet "$SERVICE"; then
  echo "Normal source Prometheus service is active. Stop it first; refusing to start temp snapshot server." >&2
  exit 20
fi

if ss -lnt 2>/dev/null | awk '{{print $4}}' | grep -qE "(^|:)${{PORT}}$"; then
  echo "Port $PORT is already listening on source; choose another temporary snapshot port." >&2
  exit 21
fi

if [ ! -d "$TSDB" ]; then
  echo "Source TSDB path missing: $TSDB" >&2
  exit 22
fi

PROM_BIN=""
if [ -x /usr/local/bin/prometheus ]; then
  PROM_BIN=/usr/local/bin/prometheus
elif command -v prometheus >/dev/null 2>&1; then
  PROM_BIN=$(command -v prometheus)
else
  echo "prometheus binary not found on source" >&2
  exit 23
fi

rm -f "$PIDFILE" "$LOG"
nohup "$PROM_BIN" \
  --config.file="$CFG" \
  --storage.tsdb.path="$TSDB" \
  --web.listen-address="127.0.0.1:${{PORT}}" \
  --web.enable-admin-api \
  --storage.tsdb.retention.time=100y \
  > "$LOG" 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"

cleanup() {{
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" >/dev/null 2>&1 || true
    sleep 2
    kill -9 "$(cat "$PIDFILE")" >/dev/null 2>&1 || true
    rm -f "$PIDFILE"
  fi
}}
trap cleanup EXIT

ready=0
for i in $(seq 1 45); do
  if curl -fsS "$URL/-/ready" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$PID" >/dev/null 2>&1; then
    echo "Temporary Prometheus exited early. Log:" >&2
    tail -80 "$LOG" >&2 || true
    exit 24
  fi
  sleep 2
done

if [ "$ready" != "1" ]; then
  echo "Temporary Prometheus did not become ready. Log:" >&2
  tail -120 "$LOG" >&2 || true
  exit 25
fi

RESP=$(curl -fsS -XPOST "$URL/api/v1/admin/tsdb/snapshot?skip_head=false")
echo "$RESP" > "$TMP_BASE/snapshot-response.json"
SNAP=$(python3 - "$TMP_BASE/snapshot-response.json" <<'REMOTE_PY'
import json, sys
with open(sys.argv[1]) as f:
    d=json.load(f)
print((d.get('data') or {{}}).get('name') or d.get('name') or '')
REMOTE_PY
)
if [ -z "$SNAP" ]; then
  echo "Could not parse snapshot name from response: $RESP" >&2
  exit 26
fi

SNAP_PATH="$SNAPDIR/$SNAP"
if [ ! -d "$SNAP_PATH" ]; then
  echo "Snapshot path missing after create: $SNAP_PATH" >&2
  exit 27
fi

# Make only the snapshot copy readable by the SSH user for controller-bridge transfer.
# This does not change original TSDB block folders.
if id "$SSH_USER" >/dev/null 2>&1; then
  chown -R "$SSH_USER:$SSH_USER" "$SNAP_PATH" || chmod -R a+rX "$SNAP_PATH" || true
else
  chmod -R a+rX "$SNAP_PATH" || true
fi

BLOCKS=$(find "$SNAP_PATH" -mindepth 1 -maxdepth 1 -type d -name '01*' | wc -l | tr -d ' ')
BYTES=$(du -sb "$SNAP_PATH" 2>/dev/null | awk '{{print $1}}' || echo 0)
python3 - <<REMOTE_JSON
import json
print(json.dumps({{
  "snapshot_id": "$SNAP",
  "snapshot_path": "$SNAP_PATH",
  "block_count": int("$BLOCKS" or 0),
  "bytes": int("$BYTES" or 0),
  "temp_url": "$URL",
  "note": "Created using temporary no-scrape Prometheus. Normal source Prometheus service was not started."
}}, indent=2))
REMOTE_JSON
'''
    res = source.run_sudo(script, timeout=900)
    if not res.ok:
        raise RuntimeError(f"safe no-scrape snapshot failed: {res.stderr or res.stdout}")
    data = _extract_json_object(res.stdout)
    return {
        "snapshot_id": data["snapshot_id"],
        "raw": data,
        "snapshot_path": data["snapshot_path"],
        "blockCount": data.get("block_count", 0),
        "bytes": data.get("bytes", 0),
        "note": data.get("note"),
    }


def list_source_snapshots(cfg: AppConfig, source: SSHRunner) -> List[Dict[str, Any]]:
    base = cfg.prometheus.source_snapshot_dir
    cmd = "python3 - " + q(base) + " <<'REMOTE_PY'\n" + LIST_SNAPSHOT_SCRIPT + "\nREMOTE_PY"
    res = source.run(cmd, timeout=180)
    if not res.ok:
        # Try sudo list if snapshots are not readable by SSH user.
        res = source.run_sudo(cmd, timeout=180)
    if not res.ok:
        raise RuntimeError(f"list snapshots failed: {res.stderr or res.stdout}")
    snaps = json.loads(res.stdout.strip() or "[]")
    for s in snaps:
        if s.get("minTime") is not None:
            s["minIso"] = utc_iso_from_ms(int(s["minTime"]))
            s["maxIso"] = utc_iso_from_ms(int(s["maxTime"]))
    return snaps


def _mkdirs(sftp, path: str) -> None:
    parts = []
    p = path
    while p not in ("", "/"):
        parts.append(p)
        p = posixpath.dirname(p)
    for d in reversed(parts):
        try:
            sftp.mkdir(d)
        except IOError:
            pass


def _copy_tree_bridge(src_sftp, dst_sftp, src_path: str, dst_path: str, progress: Dict[str, Any]) -> None:
    _mkdirs(dst_sftp, dst_path)
    for entry in src_sftp.listdir_attr(src_path):
        name = entry.filename
        if name in (".", ".."):
            continue
        sp = posixpath.join(src_path, name)
        dp = posixpath.join(dst_path, name)
        mode = entry.st_mode
        if stat.S_ISDIR(mode):
            _copy_tree_bridge(src_sftp, dst_sftp, sp, dp, progress)
        else:
            _mkdirs(dst_sftp, posixpath.dirname(dp))
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name
            try:
                src_sftp.get(sp, tmp_path)
                dst_sftp.put(tmp_path, dp)
                try:
                    dst_sftp.chmod(dp, mode & 0o777)
                except Exception:
                    pass
                progress["files"] += 1
                progress["bytes"] += int(entry.st_size or 0)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


def _safe_label(label: str) -> str:
    label = (label or "").strip()
    allowed = []
    for ch in label:
        if ch.isalnum() or ch in ("-", "_", "."):
            allowed.append(ch)
        else:
            allowed.append("-")
    out = "".join(allowed).strip(".-_")
    return out or time.strftime("snapshot-%Y%m%d-%H%M%S", time.gmtime())


def _top_level_block_dirs(sftp, src_root: str) -> List[str]:
    blocks = []
    for entry in sftp.listdir_attr(src_root):
        name = entry.filename
        if not name.startswith("01"):
            continue
        if not stat.S_ISDIR(entry.st_mode):
            continue
        sp = posixpath.join(src_root, name)
        try:
            sftp.stat(posixpath.join(sp, "meta.json"))
        except Exception:
            continue
        blocks.append(name)
    return sorted(blocks)


def _top_level_block_dirs_in_range(
    sftp,
    src_root: str,
    start_utc: Optional[str] = None,
    end_utc: Optional[str] = None,
    selection: str = "overlap",
) -> Dict[str, Any]:
    """Return block IDs selected by time range from a remote TSDB/snapshot root.

    selection='inside' copies only complete blocks fully contained in the
    requested range. This avoids bringing outside-time data, but can miss edge
    partial hours/days if a block crosses the boundary.

    selection='overlap' copies any block that overlaps the requested range.
    This covers the whole range, but may include extra data just before/after
    the requested boundaries because Prometheus blocks are indivisible.
    """
    start_ms = parse_utc_to_ms(start_utc) if start_utc else None
    end_ms = parse_utc_to_ms(end_utc) if end_utc else None
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise RuntimeError("end_utc must be later than start_utc")
    selection = selection or "overlap"
    if selection not in ("overlap", "inside"):
        raise RuntimeError("selection must be overlap or inside")

    all_blocks = []
    selected = []
    skipped = []
    for bid in _top_level_block_dirs(sftp, src_root):
        meta_path = posixpath.join(src_root, bid, "meta.json")
        try:
            with sftp.open(meta_path, "r") as f:
                raw = f.read()
            if isinstance(raw, bytes):
                raw = raw.decode()
            meta = json.loads(raw)
        except Exception:
            skipped.append({"id": bid, "reason": "could not read meta.json"})
            continue
        mn = int(meta["minTime"])
        mx = int(meta["maxTime"])
        item = {"id": bid, "minTime": mn, "maxTime": mx, "minIso": utc_iso_from_ms(mn), "maxIso": utc_iso_from_ms(mx)}
        all_blocks.append(item)

        if start_ms is None or end_ms is None:
            selected.append(item)
        elif selection == "inside":
            if mn >= start_ms and mx <= end_ms:
                selected.append(item)
        else:  # overlap
            if mn < end_ms and mx > start_ms:
                selected.append(item)

    actual_start = min([b["minTime"] for b in selected], default=None)
    actual_end = max([b["maxTime"] for b in selected], default=None)
    return {
        "allBlocks": all_blocks,
        "selectedBlocks": selected,
        "selectedIds": [b["id"] for b in selected],
        "skipped": skipped,
        "requestedStartUtc": start_utc,
        "requestedEndUtc": end_utc,
        "selection": selection,
        "actualStartIso": utc_iso_from_ms(actual_start) if actual_start is not None else None,
        "actualEndIso": utc_iso_from_ms(actual_end) if actual_end is not None else None,
    }


def transfer_source_tsdb_blocks_controller_bridge(cfg: AppConfig, source: SSHRunner, target: SSHRunner, label: str = "", overwrite: bool = False, start_utc: Optional[str] = None, end_utc: Optional[str] = None, selection: str = "overlap") -> Dict[str, Any]:
    staging_id = _safe_label(label)
    if not staging_id.startswith("offline-tsdb-"):
        staging_id = "offline-tsdb-" + staging_id
    dst = posixpath.join(cfg.prometheus.target_staging_root, staging_id)
    exists_check = target.run("test -e " + q(dst), timeout=20)
    if exists_check.ok and not overwrite:
        raise RuntimeError(f"target staging path already exists: {dst}. Choose a different label or manually move it first.")
    if exists_check.ok and overwrite:
        raise RuntimeError(f"refusing to delete existing staging path automatically: {dst}. Manually move/remove it if you are sure.")
    mk = target.run("mkdir -p " + q(dst), timeout=30)
    if not mk.ok:
        raise RuntimeError(f"could not create target staging path: {mk.stderr or mk.stdout}")

    src_client, src_sftp = source.sftp_client()
    dst_client, dst_sftp = target.sftp_client()
    progress = {"files": 0, "bytes": 0, "blocks": 0}
    try:
        picked = _top_level_block_dirs_in_range(src_sftp, cfg.prometheus.source_tsdb_path, start_utc, end_utc, selection)
        block_ids = picked["selectedIds"]
        if not block_ids:
            raise RuntimeError(f"no readable TSDB block dirs selected in source path: {cfg.prometheus.source_tsdb_path}; requested range={start_utc} to {end_utc}; selection={selection}")
        for bid in block_ids:
            _copy_tree_bridge(
                src_sftp,
                dst_sftp,
                posixpath.join(cfg.prometheus.source_tsdb_path, bid),
                posixpath.join(dst, bid),
                progress,
            )
            progress["blocks"] += 1
    finally:
        src_sftp.close(); src_client.close()
        dst_sftp.close(); dst_client.close()
    return {"mode": "controller_sftp_bridge_offline_tsdb_blocks", "snapshot_path_on_target": dst, "blockCount": progress["blocks"], "files": progress["files"], "bytes": progress["bytes"], "source_tsdb_path": cfg.prometheus.source_tsdb_path, "requestedStartUtc": start_utc, "requestedEndUtc": end_utc, "selection": selection, "selectedBlockIds": block_ids, "actualStartIso": picked.get("actualStartIso"), "actualEndIso": picked.get("actualEndIso")}


def transfer_source_tsdb_blocks_target_pull(cfg: AppConfig, source: SSHRunner, target: SSHRunner, label: str = "", overwrite: bool = False, start_utc: Optional[str] = None, end_utc: Optional[str] = None, selection: str = "overlap") -> Dict[str, Any]:
    raise RuntimeError("target_pull_rsync is hidden in this user-friendly build. Use controller bridge or add advanced mode later.")


def transfer_snapshot_controller_bridge(cfg: AppConfig, source: SSHRunner, target: SSHRunner, snapshot_id: str, source_snapshot_path: str, overwrite: bool = False, start_utc: Optional[str] = None, end_utc: Optional[str] = None, selection: str = "overlap") -> Dict[str, Any]:
    dst = posixpath.join(cfg.prometheus.target_staging_root, snapshot_id)
    exists_check = target.run("test -e " + q(dst), timeout=20)
    if exists_check.ok and not overwrite:
        raise RuntimeError(f"target staging path already exists: {dst}. Choose a new snapshot/staging path or manually move it first.")
    if exists_check.ok and overwrite:
        raise RuntimeError(f"refusing to delete existing staging path automatically: {dst}. Manually move/remove it if you are sure.")
    mk = target.run("mkdir -p " + q(dst), timeout=30)
    if not mk.ok:
        raise RuntimeError(f"could not create target staging path: {mk.stderr or mk.stdout}")

    src_client, src_sftp = source.sftp_client()
    dst_client, dst_sftp = target.sftp_client()
    progress = {"files": 0, "bytes": 0, "blocks": 0}
    picked = None
    try:
        if start_utc and end_utc:
            picked = _top_level_block_dirs_in_range(src_sftp, source_snapshot_path, start_utc, end_utc, selection)
            block_ids = picked["selectedIds"]
            if not block_ids:
                raise RuntimeError(f"no snapshot blocks selected for requested range={start_utc} to {end_utc}; selection={selection}")
            for bid in block_ids:
                _copy_tree_bridge(src_sftp, dst_sftp, posixpath.join(source_snapshot_path, bid), posixpath.join(dst, bid), progress)
                progress["blocks"] += 1
        else:
            _copy_tree_bridge(src_sftp, dst_sftp, source_snapshot_path, dst, progress)
            progress["blocks"] = len(_top_level_block_dirs(src_sftp, source_snapshot_path))
    finally:
        src_sftp.close(); src_client.close()
        dst_sftp.close(); dst_client.close()
    out = {"mode": "safe_snapshot_controller_bridge_range_limited" if start_utc and end_utc else "safe_snapshot_controller_bridge", "snapshot_path_on_target": dst, "files": progress["files"], "bytes": progress["bytes"], "blockCount": progress["blocks"], "requestedStartUtc": start_utc, "requestedEndUtc": end_utc, "selection": selection}
    if picked:
        out.update({"selectedBlockIds": picked.get("selectedIds"), "actualStartIso": picked.get("actualStartIso"), "actualEndIso": picked.get("actualEndIso")})
    return out


def transfer_snapshot_target_pull(cfg: AppConfig, source: SSHRunner, target: SSHRunner, snapshot_id: str, source_snapshot_path: str, overwrite: bool = False, start_utc: Optional[str] = None, end_utc: Optional[str] = None, selection: str = "overlap") -> Dict[str, Any]:
    raise RuntimeError("target_pull_rsync is hidden in this user-friendly build. Use controller bridge or add advanced mode later.")
