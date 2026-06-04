from __future__ import annotations

import os
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import paramiko


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _q(value: str) -> str:
    return shlex.quote(str(value))


def parse_datetime_to_ms(value: str) -> int:
    """
    Supports:
    - 2023-01-05 00:00:00 -0700
    - 2023-01-05T00:00:00-07:00
    - 2026-01-01 00:00:00 UTC
    """
    value = value.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ]

    dt: Optional[datetime] = None

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            break
        except ValueError:
            pass

    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid datetime format: {value}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return int(dt.timestamp() * 1000)


class RangeBlockMigrationService:
    """
    Custom date-range Prometheus block transfer.

    Important:
    - Does NOT use /api/v1/admin/tsdb/snapshot.
    - Selects only TSDB blocks overlapping lm1_data_start/lm1_data_end.
    - Copies selected blocks from LM1 to LM2 receive directory.
    - Merges received blocks into the actual LM2 Prometheus data path.
    """

    def _connect(self, machine: Any) -> paramiko.SSHClient:
        host = _get(machine, "host")
        user = _get(machine, "user")
        password = _get(machine, "ssh_password") or None
        key_path = _get(machine, "ssh_key_path") or None

        if not host or not user:
            raise ValueError("Machine host/user is missing")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        client.connect(
            hostname=host,
            username=user,
            password=password,
            key_filename=key_path if key_path else None,
            look_for_keys=True,
            allow_agent=True,
            timeout=20,
        )
        return client

    def _run(self, machine: Any, command: str, timeout: int = 3600) -> Dict[str, Any]:
        client = self._connect(machine)
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()

            if code != 0:
                raise RuntimeError(
                    f"SSH command failed with exit code {code}\n\nSTDOUT:\n{out}\n\nSTDERR:\n{err}"
                )

            return {"exit_code": code, "stdout": out, "stderr": err}
        finally:
            client.close()

    def create_lm1_range_manifest(self, config: Any) -> Dict[str, Any]:
        """
        Runs on LM1.
        Creates a manifest of LM1 TSDB blocks overlapping selected custom range.
        """
        source = _get(config, "source")
        source_prom_path = _get(config, "source_prom_path")
        start_text = _get(config, "lm1_data_start")
        end_text = _get(config, "lm1_data_end")

        if not source_prom_path:
            raise ValueError("source_prom_path is required")
        if not start_text or not end_text:
            raise ValueError("lm1_data_start and lm1_data_end are required")

        start_ms = parse_datetime_to_ms(start_text)
        end_ms = parse_datetime_to_ms(end_text)

        if end_ms <= start_ms:
            raise ValueError("lm1_data_end must be after lm1_data_start")

        manifest_dir = "$HOME/lm1-range-transfer"
        manifest_tsv = "$HOME/lm1-range-transfer/lm1_selected_blocks.tsv"
        block_list = "$HOME/lm1-range-transfer/lm1_selected_block_ids.txt"

        script = f"""
set -euo pipefail

PROM_PATH={_q(source_prom_path)}
START_MS={start_ms}
END_MS={end_ms}
MANIFEST_DIR={manifest_dir}
MANIFEST_TSV={manifest_tsv}
BLOCK_LIST={block_list}

mkdir -p "$MANIFEST_DIR"
rm -f "$MANIFEST_TSV" "$BLOCK_LIST"

echo "=== Create LM1 custom-range block manifest ==="
echo "PROM_PATH=$PROM_PATH"
echo "START_MS=$START_MS"
echo "END_MS=$END_MS"
echo "Start:"
date -d "@$((START_MS/1000))" -u
echo "End:"
date -d "@$((END_MS/1000))" -u

python3 - "$PROM_PATH" "$START_MS" "$END_MS" "$MANIFEST_TSV" "$BLOCK_LIST" <<'PY'
import os
import sys
import json
import glob
from datetime import datetime, timezone

prom_path = sys.argv[1]
start_ms = int(sys.argv[2])
end_ms = int(sys.argv[3])
manifest_tsv = sys.argv[4]
block_list = sys.argv[5]

rows = []

for meta in glob.glob(os.path.join(prom_path, "01*", "meta.json")):
    block_dir = os.path.dirname(meta)
    block_id = os.path.basename(block_dir)

    with open(meta, "r", encoding="utf-8") as f:
        data = json.load(f)

    min_time = int(data["minTime"])
    max_time = int(data["maxTime"])

    # overlap rule
    if max_time > start_ms and min_time < end_ms:
        min_iso = datetime.fromtimestamp(min_time / 1000, tz=timezone.utc).isoformat()
        max_iso = datetime.fromtimestamp(max_time / 1000, tz=timezone.utc).isoformat()
        rows.append((block_id, min_time, max_time, min_iso, max_iso))

rows.sort(key=lambda x: x[1])

with open(manifest_tsv, "w", encoding="utf-8") as mf, open(block_list, "w", encoding="utf-8") as bf:
    for block_id, min_time, max_time, min_iso, max_iso in rows:
        mf.write(f"{{block_id}}\\t{{min_time}}\\t{{max_time}}\\t{{min_iso}}\\t{{max_iso}}\\n")
        bf.write(f"{{block_id}}\\n")

print(f"SELECTED_BLOCKS={{len(rows)}}")
PY

echo
echo "=== Selected blocks ==="
cat "$MANIFEST_TSV"

echo
echo "=== Summary ==="
echo "MANIFEST_TSV=$MANIFEST_TSV"
echo "BLOCK_LIST=$BLOCK_LIST"
echo "SELECTED_BLOCKS=$(wc -l < "$BLOCK_LIST" | tr -d ' ')"

if [ ! -s "$BLOCK_LIST" ]; then
  echo "ERROR: No LM1 TSDB blocks overlap the selected custom range."
  exit 30
fi
"""

        result = self._run(source, script, timeout=3600)

        return {
            "ok": True,
            "message": "LM1 custom-range block manifest created",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }

    def transfer_lm1_range_blocks_to_lm2(self, config: Any) -> Dict[str, Any]:
        """
        Runs on LM1.
        Transfers only selected blocks from LM1 to LM2 receive directory.
        Requires passwordless SSH from LM1 to LM2.
        """
        source = _get(config, "source")
        target = _get(config, "target")

        source_prom_path = _get(config, "source_prom_path")
        target_receive_dir = _get(config, "target_receive_dir")

        lm2_user = _get(target, "user")
        lm2_host = _get(target, "host")

        if not source_prom_path:
            raise ValueError("source_prom_path is required")
        if not target_receive_dir:
            raise ValueError("target_receive_dir is required")
        if not lm2_user or not lm2_host:
            raise ValueError("target user/host is required")

        block_list = "$HOME/lm1-range-transfer/lm1_selected_block_ids.txt"

        remote_prepare = f"rm -rf {_q(target_receive_dir)} && mkdir -p {_q(target_receive_dir)}"
        remote_extract = f"tar -C {_q(target_receive_dir)} -xf -"
        remote_verify = (
            f"echo 'Transferred blocks:'; "
            f"find {_q(target_receive_dir)} -maxdepth 1 -type d -name '01*' | wc -l; "
            f"echo 'Transferred size:'; "
            f"du -sh {_q(target_receive_dir)}"
        )

        script = f"""
set -euo pipefail

PROM_PATH={_q(source_prom_path)}
BLOCK_LIST={block_list}
LM2="{lm2_user}@{lm2_host}"

echo "=== Transfer selected LM1 blocks to LM2 ==="
echo "PROM_PATH=$PROM_PATH"
echo "BLOCK_LIST=$BLOCK_LIST"
echo "LM2=$LM2"
echo "TARGET_RECEIVE_DIR={target_receive_dir}"

if [ ! -s "$BLOCK_LIST" ]; then
  echo "ERROR: Block list not found or empty: $BLOCK_LIST"
  echo "Run create_lm1_range_manifest first."
  exit 40
fi

echo "Selected block count:"
wc -l "$BLOCK_LIST"

echo "=== Prepare target receive directory ==="
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$LM2" {_q(remote_prepare)}

echo "=== Transfer only selected blocks ==="
cd "$PROM_PATH"
tar -cf - -T "$BLOCK_LIST" | ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$LM2" {_q(remote_extract)}

echo "=== Verify target receive directory ==="
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$LM2" {_q(remote_verify)}

echo "=== Range block transfer completed ==="
"""

        result = self._run(source, script, timeout=24 * 3600)

        return {
            "ok": True,
            "message": "Selected LM1 range blocks transferred to LM2",
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }

    def merge_received_range_blocks_into_lm2(self, config: Any) -> Dict[str, Any]:
        """
        Runs on LM2.
        Merges received selected LM1 blocks into the actual LM2 Prometheus path.

        IMPORTANT:
        target_prom_path must be the real active LM2 path.
        In your case, this should be /data, not /var/lib/prometheus/metrics2.
        """
        target = _get(config, "target")
        target_prom_path = _get(config, "target_prom_path")
        target_receive_dir = _get(config, "target_receive_dir")

        target_sudo_password = _get(target, "sudo_password") or _get(target, "ssh_password") or ""

        if not target_prom_path:
            raise ValueError("target_prom_path is required")
        if not target_receive_dir:
            raise ValueError("target_receive_dir is required")

        sudo_auth = ""
        if target_sudo_password:
            sudo_auth = f"printf '%s\\n' {_q(target_sudo_password)} | sudo -S -v"
        else:
            sudo_auth = "sudo -n -v"

        script = f"""
set -euo pipefail

TARGET_PATH={_q(target_prom_path)}
RECEIVE_DIR={_q(target_receive_dir)}

echo "=== Authenticate sudo on LM2 ==="
{sudo_auth}

echo "=== Merge received custom-range blocks into LM2 actual Prometheus path ==="
echo "TARGET_PATH=$TARGET_PATH"
echo "RECEIVE_DIR=$RECEIVE_DIR"

if [ ! -d "$TARGET_PATH" ]; then
  echo "ERROR: target Prometheus path does not exist: $TARGET_PATH"
  exit 50
fi

if [ ! -d "$RECEIVE_DIR" ]; then
  echo "ERROR: receive directory does not exist: $RECEIVE_DIR"
  exit 51
fi

find "$RECEIVE_DIR" -maxdepth 1 -type d -name "01*" -printf "%f\\n" | sort > /tmp/range_received_blocks.txt

if [ ! -s /tmp/range_received_blocks.txt ]; then
  echo "ERROR: no received TSDB blocks found in $RECEIVE_DIR"
  exit 52
fi

echo "Received blocks:"
wc -l /tmp/range_received_blocks.txt

echo "=== Stop Prometheus before block merge ==="
sudo systemctl stop prometheus || true
sudo pkill -f '/home/iperf/prometheus/prometheus' || true
sleep 3

echo "=== Safety check: detect overlapping blocks with existing LM2 data ==="
OVERLAP_FILE="/tmp/range_overlap_warnings.txt"
rm -f "$OVERLAP_FILE"
touch "$OVERLAP_FILE"

for rmeta in "$RECEIVE_DIR"/01*/meta.json; do
  [ -f "$rmeta" ] || continue

  rid=$(basename "$(dirname "$rmeta")")
  rmin=$(jq -r '.minTime' "$rmeta")
  rmax=$(jq -r '.maxTime' "$rmeta")

  for tmeta in "$TARGET_PATH"/01*/meta.json; do
    [ -f "$tmeta" ] || continue

    tid=$(basename "$(dirname "$tmeta")")

    # Same block ID is not treated as a dangerous time overlap.
    if [ "$rid" = "$tid" ]; then
      continue
    fi

    tmin=$(sudo jq -r '.minTime' "$tmeta")
    tmax=$(sudo jq -r '.maxTime' "$tmeta")

    if [ "$rmax" -gt "$tmin" ] && [ "$rmin" -lt "$tmax" ]; then
      echo "$rid overlaps existing target block $tid" >> "$OVERLAP_FILE"
    fi
  done
done

if [ -s "$OVERLAP_FILE" ]; then
  echo "ERROR: Selected LM1 blocks overlap existing LM2 blocks."
  echo "This can break Prometheus startup. Merge stopped."
  cat "$OVERLAP_FILE"
  exit 88
fi

echo "No dangerous overlaps found."

echo "=== Copy selected blocks into target path ==="
while read -r id; do
  src="$RECEIVE_DIR/$id"
  dst="$TARGET_PATH/$id"

  if [ ! -d "$src" ]; then
    echo "ERROR: missing received block: $src"
    exit 60
  fi

  if [ -d "$dst" ]; then
    echo "Skipping already-existing block: $id"
  else
    echo "Copying block into LM2 actual path: $id"
    sudo cp -a "$src" "$TARGET_PATH/"
  fi
done < /tmp/range_received_blocks.txt

echo "=== Fix ownership ==="
sudo chown -R prometheus:prometheus "$TARGET_PATH"

echo "=== Start Prometheus ==="
sudo systemctl start prometheus
sleep 15

echo "=== Health check ==="
curl -s http://localhost:9090/-/healthy
echo
curl -s http://localhost:9090/-/ready
echo

echo "=== Prometheus storage path ==="
curl -s http://localhost:9090/api/v1/status/flags | jq -r '.data["storage.tsdb.path"]'

echo "=== Final target range ==="
MIN=$(sudo find "$TARGET_PATH" -maxdepth 2 -name meta.json -exec jq -r '.minTime' {{}} \\; | sort -n | head -1)
MAX=$(sudo find "$TARGET_PATH" -maxdepth 2 -name meta.json -exec jq -r '.maxTime' {{}} \\; | sort -n | tail -1)

echo "Earliest:"
date -d "@$((MIN/1000))" -u
echo "Latest:"
date -d "@$((MAX/1000))" -u
echo "Size:"
sudo du -sh "$TARGET_PATH"

echo "=== Custom-range merge completed ==="
"""

        result = self._run(target, script, timeout=24 * 3600)

        return {
            "ok": True,
            "message": "Received selected LM1 range blocks merged into LM2",
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }