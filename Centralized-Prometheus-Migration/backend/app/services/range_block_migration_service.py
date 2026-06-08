from __future__ import annotations

import shlex
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import paramiko


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _q(value: Any) -> str:
    return shlex.quote(str(value))


def parse_datetime_to_ms(value: str) -> int:
    value = value.strip()

    formats = [
        "%Y-%m-%d %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
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
    Generalized exact-range Prometheus export/import.

    Flow:
      LM1 TSDB path
      -> temporary no-scrape Prometheus if needed
      -> Prometheus API query_range
      -> OpenMetrics file with added migration labels
      -> promtool create-blocks-from openmetrics
      -> transfer generated blocks to LM2
      -> merge into LM2 Prometheus TSDB path.

    Important:
    - This exports only selected samples, not whole original TSDB blocks.
    - OpenMetrics timestamps are written in seconds, not milliseconds.
    - Imported data gets source_env=<source_env_old> plus extra labels.
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
            timeout=25,
        )
        return client

    def _run(self, machine: Any, command: str, timeout: int = 3600) -> Dict[str, Any]:
        client = self._connect(machine)

        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()

            if code != 0:
                raise RuntimeError(
                    f"SSH command failed with exit code {code}\n\nSTDOUT:\n{out}\n\nSTDERR:\n{err}"
                )

            return {
                "exit_code": code,
                "stdout": out,
                "stderr": err,
            }
        finally:
            client.close()

    def create_lm1_range_manifest(self, config: Any) -> Dict[str, Any]:
        source = _get(config, "source")

        start_text = _get(config, "lm1_data_start")
        end_text = _get(config, "lm1_data_end")

        source_env_old = _get(config, "source_env_old", "lm1")
        imported_migration_origin = _get(config, "imported_migration_origin", "legacy")
        imported_source_host = _get(config, "imported_source_host", "") or _get(source, "name", "") or _get(source, "host", "")

        source_prom_path = _get(config, "source_prom_path")
        prom_bin = _get(config, "prom_bin", "/usr/bin/prometheus")
        promtool_bin = _get(config, "promtool_bin", "")
        prom_retention_time = _get(config, "prom_retention_time", "10y")

        source_prometheus_url = str(_get(config, "source_prometheus_url", "http://localhost:9090")).rstrip("/")
        temp_prometheus_url = str(_get(config, "exact_range_temp_prometheus_url", "http://127.0.0.1:9090")).rstrip("/")
        temp_listen_address = _get(config, "exact_range_temp_listen_address", "127.0.0.1:9090")

        prometheus_system_user = _get(config, "prometheus_system_user", "prometheus")

        step_seconds = int(_get(config, "exact_range_step_seconds", 15) or 15)
        max_seconds = int(_get(config, "exact_range_max_seconds", 0) or 0)
        chunk_seconds = int(_get(config, "exact_range_chunk_seconds", 3600) or 3600)
        match_selector = _get(config, "exact_range_match_selector", '{__name__!=""}')

        sudo_password = _get(source, "sudo_password") or _get(source, "ssh_password") or ""

        if not start_text or not end_text:
            raise ValueError("lm1_data_start and lm1_data_end are required")

        if not source_prom_path:
            raise ValueError("source_prom_path is required")

        start_ms = parse_datetime_to_ms(start_text)
        end_ms = parse_datetime_to_ms(end_text)

        if end_ms <= start_ms:
            raise ValueError("lm1_data_end must be after lm1_data_start")

        if step_seconds <= 0:
            raise ValueError("exact_range_step_seconds must be positive")

        if chunk_seconds <= 0:
            raise ValueError("exact_range_chunk_seconds must be positive")

        sudo_auth = (
            f"printf '%s\\n' {_q(sudo_password)} | sudo -S -v"
            if sudo_password
            else "sudo -n -v"
        )

        script = r"""
set -euo pipefail

START_S=__START_S__
END_S=__END_S__
STEP_SECONDS=__STEP_SECONDS__
MAX_SECONDS=__MAX_SECONDS__
CHUNK_SECONDS=__CHUNK_SECONDS__

SOURCE_ENV_OLD=__SOURCE_ENV_OLD__
IMPORTED_MIGRATION_ORIGIN=__IMPORTED_MIGRATION_ORIGIN__
IMPORTED_SOURCE_HOST=__IMPORTED_SOURCE_HOST__

LM1_PROM_PATH=__LM1_PROM_PATH__
PROM_BIN=__PROM_BIN__
PROMTOOL_BIN=__PROMTOOL_BIN__
PROM_RETENTION_TIME=__PROM_RETENTION_TIME__

SOURCE_PROMETHEUS_URL=__SOURCE_PROMETHEUS_URL__
TEMP_PROMETHEUS_URL=__TEMP_PROMETHEUS_URL__
TEMP_LISTEN_ADDRESS=__TEMP_LISTEN_ADDRESS__
PROMETHEUS_SYSTEM_USER=__PROMETHEUS_SYSTEM_USER__
EXACT_RANGE_MATCH_SELECTOR=__EXACT_RANGE_MATCH_SELECTOR__

WORK_DIR="$HOME/lm1-exact-range-transfer"
OM_FILE="$WORK_DIR/lm1_exact_range.openmetrics"
BLOCK_DIR="$WORK_DIR/blocks"
BLOCK_LIST="$WORK_DIR/lm1_exact_block_ids.txt"
MANIFEST_TSV="$WORK_DIR/lm1_exact_blocks.tsv"
TEMP_STARTED_FILE="$WORK_DIR/temp_prometheus_started.flag"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$BLOCK_DIR"

DURATION=$((END_S - START_S))

if [ "$DURATION" -le 0 ]; then
  echo "ERROR: invalid exact range"
  exit 71
fi

if [ "$MAX_SECONDS" -gt 0 ] && [ "$DURATION" -gt "$MAX_SECONDS" ]; then
  echo "ERROR: selected range exceeds exact_range_max_seconds=$MAX_SECONDS."
  echo "Set exact_range_max_seconds=0 for unlimited range."
  exit 72
fi

if [ "$DURATION" -gt 86400 ]; then
  echo "WARNING: selected range is greater than 24h."
  echo "This is allowed, but it can take a long time."
  echo "Chunk size: $CHUNK_SECONDS seconds"
fi

if [ ! -d "$LM1_PROM_PATH" ]; then
  echo "ERROR: source Prometheus path does not exist: $LM1_PROM_PATH"
  exit 73
fi

if [ -n "$PROMTOOL_BIN" ] && [ -x "$PROMTOOL_BIN" ] && "$PROMTOOL_BIN" tsdb --help >/dev/null 2>&1; then
  PROMTOOL="$PROMTOOL_BIN"
elif command -v promtool >/dev/null 2>&1 && "$(command -v promtool)" tsdb --help >/dev/null 2>&1; then
  PROMTOOL="$(command -v promtool)"
elif [ -x "$(dirname "$PROM_BIN")/promtool" ] && "$(dirname "$PROM_BIN")/promtool" tsdb --help >/dev/null 2>&1; then
  PROMTOOL="$(dirname "$PROM_BIN")/promtool"
elif [ -x /home/testhouse/promtool-new ] && /home/testhouse/promtool-new tsdb --help >/dev/null 2>&1; then
  PROMTOOL=/home/testhouse/promtool-new
elif [ -x /usr/local/bin/promtool-new ] && /usr/local/bin/promtool-new tsdb --help >/dev/null 2>&1; then
  PROMTOOL=/usr/local/bin/promtool-new
elif [ -x /usr/local/bin/promtool ] && /usr/local/bin/promtool tsdb --help >/dev/null 2>&1; then
  PROMTOOL=/usr/local/bin/promtool
elif [ -x /usr/bin/promtool ] && /usr/bin/promtool tsdb --help >/dev/null 2>&1; then
  PROMTOOL=/usr/bin/promtool
else
  echo "ERROR: compatible promtool with tsdb support not found."
  echo "Current system promtool may be too old."
  echo "Set Promtool binary path in GUI, for example: /home/testhouse/promtool-new"
  exit 74
fi

if [ ! -x "$PROM_BIN" ]; then
  if command -v prometheus >/dev/null 2>&1; then
    PROM_BIN="$(command -v prometheus)"
  else
    echo "ERROR: Prometheus binary is not executable: $PROM_BIN"
    exit 75
  fi
fi

cleanup_temp_prom() {
  if [ -f "$TEMP_STARTED_FILE" ]; then
    echo "=== Stop temporary LM1 Prometheus ==="
    pid=$(cat "$TEMP_STARTED_FILE" || true)
    if [ -n "${pid:-}" ]; then
      sudo kill "$pid" || true
      sleep 2
    fi
  fi
}
trap cleanup_temp_prom EXIT

echo "=== Exact LM1 range export ==="
echo "Start UTC: $(date -u -d "@$START_S" '+%Y-%m-%dT%H:%M:%SZ')"
echo "End UTC:   $(date -u -d "@$END_S" '+%Y-%m-%dT%H:%M:%SZ')"
echo "Duration:  $DURATION seconds"
echo "Step:      $STEP_SECONDS seconds"
echo "Chunk:     $CHUNK_SECONDS seconds"
echo "Max:       $MAX_SECONDS seconds (0 means unlimited)"
echo "Selector:  $EXACT_RANGE_MATCH_SELECTOR"
echo "Add label: source_env=$SOURCE_ENV_OLD"
echo "Add label: migration_origin=$IMPORTED_MIGRATION_ORIGIN"
echo "Add label: source_host=$IMPORTED_SOURCE_HOST"
echo "TSDB path: $LM1_PROM_PATH"
echo "Work dir:  $WORK_DIR"
echo "Promtool:  $PROMTOOL"
echo

PROM_API_URL="$SOURCE_PROMETHEUS_URL"

if curl -fsS "$SOURCE_PROMETHEUS_URL/-/ready" >/dev/null 2>&1; then
  echo "LM1 Prometheus is already running and ready at $SOURCE_PROMETHEUS_URL."
else
  echo "LM1 Prometheus is not ready at $SOURCE_PROMETHEUS_URL."
  echo "Starting temporary no-scrape Prometheus at $TEMP_PROMETHEUS_URL."
  __SUDO_AUTH__

  cat > /tmp/prometheus-exact-range-export.yml <<'PROMEOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s
scrape_configs: []
PROMEOF

  sudo -u "$PROMETHEUS_SYSTEM_USER" nohup "$PROM_BIN" \
    --config.file=/tmp/prometheus-exact-range-export.yml \
    --storage.tsdb.path="$LM1_PROM_PATH" \
    --web.listen-address="$TEMP_LISTEN_ADDRESS" \
    --storage.tsdb.retention.time="$PROM_RETENTION_TIME" \
    > /tmp/prometheus-exact-range-export.log 2>&1 &

  echo $! > "$TEMP_STARTED_FILE"
  PROM_API_URL="$TEMP_PROMETHEUS_URL"

  for attempt in $(seq 1 60); do
    if curl -fsS "$PROM_API_URL/-/ready" >/dev/null 2>&1; then
      echo "Temporary LM1 Prometheus is ready."
      break
    fi
    sleep 1
  done

  curl -fsS "$PROM_API_URL/-/ready" >/dev/null
fi

python3 - \
  "$START_S" \
  "$END_S" \
  "$STEP_SECONDS" \
  "$SOURCE_ENV_OLD" \
  "$IMPORTED_MIGRATION_ORIGIN" \
  "$IMPORTED_SOURCE_HOST" \
  "$OM_FILE" \
  "$CHUNK_SECONDS" \
  "$PROM_API_URL" \
  "$EXACT_RANGE_MATCH_SELECTOR" \
  <<'INNER_PY'
import json
import math
import re
import sys
import time
import urllib.parse
import urllib.request

start_s = int(sys.argv[1])
end_s = int(sys.argv[2])
step = int(sys.argv[3])
source_env = sys.argv[4]
migration_origin = sys.argv[5]
source_host = sys.argv[6]
om_file = sys.argv[7]
chunk_seconds = int(sys.argv[8])
base = sys.argv[9].rstrip("/")
match_selector = sys.argv[10]

name_re = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
label_name_re = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def get_json(path, params, timeout=300, retries=3):
    url = base + path + "?" + urllib.parse.urlencode(params, doseq=True)
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = r.read().decode("utf-8", errors="replace")

            data = json.loads(body)

            if data.get("status") != "success":
                raise RuntimeError(f"Prometheus API error for {path}: {data}")

            return data["data"]
        except Exception as exc:
            last_error = exc
            print(f"API retry {attempt}/{retries} failed for {path}: {exc}", flush=True)
            time.sleep(2)

    raise RuntimeError(f"Prometheus API failed after {retries} retries: {last_error}")


def esc(value):
    return str(value).replace("\\", r"\\").replace("\n", r"\n").replace('"', r'\"')


def selector_from_labels(labels):
    metric = labels.get("__name__", "")

    if not name_re.match(metric):
        raise ValueError(f"Bad metric name: {metric}")

    parts = []

    for k in sorted(labels):
        if k == "__name__":
            continue
        if not label_name_re.match(k):
            continue

        parts.append(f'{k}="{esc(labels[k])}"')

    return metric + "{" + ",".join(parts) + "}" if parts else metric


def output_labels(labels):
    out = {}

    for k, v in labels.items():
        if k == "__name__":
            continue
        if not label_name_re.match(k):
            continue

        # Force imported labels to avoid collisions and support cleanup.
        if k in ("source_env", "migration_origin", "source_host"):
            continue

        out[k] = v

    out["source_env"] = source_env
    out["migration_origin"] = migration_origin

    if source_host:
        out["source_host"] = source_host

    return "{" + ",".join(f'{k}="{esc(out[k])}"' for k in sorted(out)) + "}"


def valid_number(value):
    try:
        number = float(value)
    except Exception:
        return False
    return math.isfinite(number)


def timestamp_text(ts_float):
    # promtool OpenMetrics timestamp must be seconds, not milliseconds.
    if ts_float.is_integer():
        return str(int(ts_float))
    return f"{ts_float:.3f}".rstrip("0").rstrip(".")


print("Getting series list from source Prometheus...")
series = get_json(
    "/api/v1/series",
    {
        "match[]": match_selector,
        "start": start_s,
        "end": end_s,
    },
    timeout=600,
    retries=5,
)

print(f"SERIES_FOUND={len(series)}", flush=True)

if len(series) == 0:
    raise SystemExit("ERROR: No series found in the selected time range.")

seen_type = set()
written = 0
series_with_samples = 0

with open(om_file, "w", encoding="utf-8") as f:
    for idx, labels in enumerate(series, 1):
        metric = labels.get("__name__", "")

        if not name_re.match(metric):
            continue

        selector = selector_from_labels(labels)
        wrote_this_series = False
        seen_timestamps = set()

        chunk_start = start_s

        while chunk_start <= end_s:
            chunk_end = min(chunk_start + chunk_seconds, end_s)

            data = get_json(
                "/api/v1/query_range",
                {
                    "query": selector,
                    "start": chunk_start,
                    "end": chunk_end,
                    "step": step,
                },
                timeout=300,
                retries=3,
            )

            for result in data.get("result", []):
                result_labels = result.get("metric", labels)
                label_text = output_labels(result_labels)

                for ts, value in result.get("values", []):
                    if value in ("StaleNaN", "staleNaN") or not valid_number(value):
                        continue

                    ts_float = float(ts)
                    ts_ms = int(ts_float * 1000)

                    if ts_ms < start_s * 1000 or ts_ms > end_s * 1000:
                        continue

                    if ts_ms in seen_timestamps:
                        continue

                    seen_timestamps.add(ts_ms)

                    if metric not in seen_type:
                        # OpenMetrics uses "unknown", not "untyped".
                        f.write(f"# TYPE {metric} unknown\n")
                        seen_type.add(metric)

                    f.write(f"{metric}{label_text} {value} {timestamp_text(ts_float)}\n")
                    written += 1
                    wrote_this_series = True

            if chunk_end >= end_s:
                break

            chunk_start = chunk_end + step

        if wrote_this_series:
            series_with_samples += 1

        if idx % 50 == 0:
            print(
                f"PROGRESS series={idx}/{len(series)} "
                f"series_with_samples={series_with_samples} samples={written}",
                flush=True,
            )
            time.sleep(0.01)

    f.write("# EOF\n")

print(f"SERIES_WITH_SAMPLES={series_with_samples}", flush=True)
print(f"SAMPLES_WRITTEN={written}", flush=True)

if written == 0:
    raise SystemExit("ERROR: No samples found in the selected exact range.")
INNER_PY

echo
echo "=== OpenMetrics file size ==="
wc -l "$OM_FILE"
du -h "$OM_FILE"

echo
echo "=== Create exact-range TSDB blocks from OpenMetrics ==="
"$PROMTOOL" tsdb create-blocks-from openmetrics "$OM_FILE" "$BLOCK_DIR"

echo
echo "=== Generated exact-range blocks ==="
find "$BLOCK_DIR" -maxdepth 1 -type d -name '01*' -printf '%f\n' | sort > "$BLOCK_LIST"

if [ ! -s "$BLOCK_LIST" ]; then
  echo "ERROR: promtool did not create any block."
  exit 76
fi

: > "$MANIFEST_TSV"

while read -r id; do
  meta="$BLOCK_DIR/$id/meta.json"
  min_ms=$(jq -r '.minTime' "$meta")
  max_ms=$(jq -r '.maxTime' "$meta")
  min_iso=$(date -u -d "@$((min_ms/1000))" '+%Y-%m-%dT%H:%M:%SZ')
  max_iso=$(date -u -d "@$((max_ms/1000))" '+%Y-%m-%dT%H:%M:%SZ')
  printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$min_ms" "$max_ms" "$min_iso" "$max_iso" >> "$MANIFEST_TSV"
done < "$BLOCK_LIST"

cat "$MANIFEST_TSV"

echo
echo "=== Summary ==="
echo "EXACT_RANGE_MODE=api_openmetrics_promtool"
echo "BLOCK_DIR=$BLOCK_DIR"
echo "BLOCK_LIST=$BLOCK_LIST"
echo "MANIFEST_TSV=$MANIFEST_TSV"
echo "GENERATED_BLOCKS=$(wc -l < "$BLOCK_LIST" | tr -d ' ')"
echo "Done. Now run Range transfer."
"""

        script = (
            script.replace("__START_S__", str(start_ms // 1000))
            .replace("__END_S__", str(end_ms // 1000))
            .replace("__STEP_SECONDS__", str(step_seconds))
            .replace("__MAX_SECONDS__", str(max_seconds))
            .replace("__CHUNK_SECONDS__", str(chunk_seconds))
            .replace("__SOURCE_ENV_OLD__", _q(source_env_old))
            .replace("__IMPORTED_MIGRATION_ORIGIN__", _q(imported_migration_origin))
            .replace("__IMPORTED_SOURCE_HOST__", _q(imported_source_host))
            .replace("__LM1_PROM_PATH__", _q(source_prom_path))
            .replace("__PROM_BIN__", _q(prom_bin))
            .replace("__PROMTOOL_BIN__", _q(promtool_bin))
            .replace("__PROM_RETENTION_TIME__", _q(prom_retention_time))
            .replace("__SOURCE_PROMETHEUS_URL__", _q(source_prometheus_url))
            .replace("__TEMP_PROMETHEUS_URL__", _q(temp_prometheus_url))
            .replace("__TEMP_LISTEN_ADDRESS__", _q(temp_listen_address))
            .replace("__PROMETHEUS_SYSTEM_USER__", _q(prometheus_system_user))
            .replace("__EXACT_RANGE_MATCH_SELECTOR__", _q(match_selector))
            .replace("__SUDO_AUTH__", sudo_auth)
        )

        result = self._run(source, script, timeout=7 * 24 * 3600)

        return {
            "ok": True,
            "message": "Exact source range exported and TSDB blocks created",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "step_seconds": step_seconds,
            "chunk_seconds": chunk_seconds,
            "max_seconds": max_seconds,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }

    def transfer_lm1_range_blocks_to_lm2(self, config: Any) -> Dict[str, Any]:
        source = _get(config, "source")
        target = _get(config, "target")
        target_receive_dir = _get(config, "target_receive_dir")

        target_user = _get(target, "user")
        target_host = _get(target, "host")

        if not target_receive_dir:
            raise ValueError("target_receive_dir is required")

        if not target_user or not target_host:
            raise ValueError("target user/host is required")

        remote_prepare = r"""
set -euo pipefail
RECEIVE_DIR=__RECEIVE_DIR__

case "$RECEIVE_DIR" in
  ""|"/"|"/home"|"/var"|"/var/lib"|"/var/lib/prometheus"|"/data"|"/opt")
    echo "ERROR: unsafe target_receive_dir: $RECEIVE_DIR"
    exit 90
    ;;
esac

rm -rf "$RECEIVE_DIR"
mkdir -p "$RECEIVE_DIR"
""".replace("__RECEIVE_DIR__", _q(target_receive_dir))

        remote_extract = f"tar -C {_q(target_receive_dir)} -xf -"
        remote_verify = (
            f"echo 'Transferred exact-range blocks:'; "
            f"find {_q(target_receive_dir)} -maxdepth 1 -type d -name '01*' | wc -l; "
            f"echo 'Transferred size:'; du -sh {_q(target_receive_dir)}"
        )

        script = r"""
set -euo pipefail

WORK_DIR="$HOME/lm1-exact-range-transfer"
BLOCK_DIR="$WORK_DIR/blocks"
BLOCK_LIST="$WORK_DIR/lm1_exact_block_ids.txt"
TARGET=__TARGET__

if [ ! -d "$BLOCK_DIR" ]; then
  echo "ERROR: exact block directory not found: $BLOCK_DIR"
  echo "Run Range manifest first."
  exit 80
fi

if [ ! -s "$BLOCK_LIST" ]; then
  echo "ERROR: exact block list not found or empty: $BLOCK_LIST"
  echo "Run Range manifest first."
  exit 81
fi

echo "=== Transfer exact range blocks to target ==="
echo "BLOCK_DIR=$BLOCK_DIR"
echo "BLOCK_LIST=$BLOCK_LIST"
echo "TARGET=$TARGET"
echo "TARGET_RECEIVE_DIR=__TARGET_RECEIVE_DIR_TEXT__"
echo

echo "Exact generated block count:"
wc -l "$BLOCK_LIST"

echo "=== Prepare target receive directory ==="
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET" __REMOTE_PREPARE__

echo "=== Transfer exact generated blocks ==="
cd "$BLOCK_DIR"
tar -cf - -T "$BLOCK_LIST" | ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET" __REMOTE_EXTRACT__

echo "=== Verify target receive directory ==="
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$TARGET" __REMOTE_VERIFY__

echo "=== Exact range transfer completed ==="
"""

        script = (
            script.replace("__TARGET__", _q(f"{target_user}@{target_host}"))
            .replace("__TARGET_RECEIVE_DIR_TEXT__", str(target_receive_dir))
            .replace("__REMOTE_PREPARE__", _q(remote_prepare))
            .replace("__REMOTE_EXTRACT__", _q(remote_extract))
            .replace("__REMOTE_VERIFY__", _q(remote_verify))
        )

        result = self._run(source, script, timeout=7 * 24 * 3600)

        return {
            "ok": True,
            "message": "Exact range blocks transferred to target",
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }

    def merge_received_range_blocks_into_lm2(self, config: Any) -> Dict[str, Any]:
        target = _get(config, "target")

        target_prom_path = _get(config, "target_prom_path")
        target_receive_dir = _get(config, "target_receive_dir")
        source_env_old = _get(config, "source_env_old", "lm1")
        imported_migration_origin = _get(config, "imported_migration_origin", "legacy")

        start_text = _get(config, "lm1_data_start")
        end_text = _get(config, "lm1_data_end")

        target_prometheus_url = str(_get(config, "target_prometheus_url", "http://localhost:9090")).rstrip("/")
        prometheus_service_name = _get(config, "prometheus_service_name", "prometheus")
        prometheus_system_user = _get(config, "prometheus_system_user", "prometheus")
        analyze_after_merge = bool(_get(config, "exact_range_analyze_after_merge", False))

        target_sudo_password = _get(target, "sudo_password") or _get(target, "ssh_password") or ""

        if not target_prom_path:
            raise ValueError("target_prom_path is required")

        if not target_receive_dir:
            raise ValueError("target_receive_dir is required")

        if not start_text or not end_text:
            raise ValueError("lm1_data_start and lm1_data_end are required")

        start_ms = parse_datetime_to_ms(start_text)
        end_ms = parse_datetime_to_ms(end_text)

        if end_ms <= start_ms:
            raise ValueError("lm1_data_end must be after lm1_data_start")

        sudo_auth = (
            f"printf '%s\\n' {_q(target_sudo_password)} | sudo -S -v"
            if target_sudo_password
            else "sudo -n -v"
        )

        script = r"""
set -euo pipefail

TARGET_PATH=__TARGET_PATH__
RECEIVE_DIR=__RECEIVE_DIR__
SOURCE_ENV_OLD=__SOURCE_ENV_OLD__
IMPORTED_MIGRATION_ORIGIN=__IMPORTED_MIGRATION_ORIGIN__

START_TS=__START_TS__
END_TS=__END_TS__
RANGE_SEC=$((END_TS - START_TS))

TARGET_PROMETHEUS_URL=__TARGET_PROMETHEUS_URL__
PROMETHEUS_SERVICE_NAME=__PROMETHEUS_SERVICE_NAME__
PROMETHEUS_SYSTEM_USER=__PROMETHEUS_SYSTEM_USER__
ANALYZE_AFTER_MERGE=__ANALYZE_AFTER_MERGE__

restart_prometheus() {
  echo "=== Safety restart target Prometheus ==="
  sudo systemctl start "$PROMETHEUS_SERVICE_NAME" || true
}
trap restart_prometheus EXIT

echo "=== Authenticate sudo on target ==="
__SUDO_AUTH__

echo "=== Merge received exact-range blocks into target Prometheus path ==="
echo "TARGET_PATH=$TARGET_PATH"
echo "RECEIVE_DIR=$RECEIVE_DIR"
echo "source_env=$SOURCE_ENV_OLD"
echo "migration_origin=$IMPORTED_MIGRATION_ORIGIN"
echo "Range UTC: $(date -u -d "@$START_TS" '+%Y-%m-%dT%H:%M:%SZ') to $(date -u -d "@$END_TS" '+%Y-%m-%dT%H:%M:%SZ')"
echo

if [ ! -d "$TARGET_PATH" ]; then
  echo "ERROR: target Prometheus path does not exist: $TARGET_PATH"
  exit 50
fi

if [ ! -d "$RECEIVE_DIR" ]; then
  echo "ERROR: receive directory does not exist: $RECEIVE_DIR"
  exit 51
fi

if curl -fsS "$TARGET_PROMETHEUS_URL/-/ready" >/dev/null 2>&1; then
  ACTIVE_PATH=$(curl -s "$TARGET_PROMETHEUS_URL/api/v1/status/flags" | jq -r '.data["storage.tsdb.path"] // empty' || true)

  if [ -n "$ACTIVE_PATH" ]; then
    TARGET_ABS=$(sudo readlink -f "$TARGET_PATH")

    if [ "${ACTIVE_PATH#/}" != "$ACTIVE_PATH" ]; then
      ACTIVE_ABS=$(sudo readlink -f "$ACTIVE_PATH")
    else
      PROM_PID=$(pgrep -f '[p]rometheus' | head -1 || true)

      if [ -n "$PROM_PID" ]; then
        PROM_CWD=$(sudo readlink -f "/proc/$PROM_PID/cwd")
        ACTIVE_ABS=$(sudo readlink -f "$PROM_CWD/$ACTIVE_PATH")
      else
        ACTIVE_ABS=$(sudo readlink -f "$ACTIVE_PATH")
      fi
    fi

    echo "Configured target_prom_path: $TARGET_PATH"
    echo "Active Prometheus path:      $ACTIVE_PATH"
    echo "Resolved configured path:    $TARGET_ABS"
    echo "Resolved active path:        $ACTIVE_ABS"

    if [ "$ACTIVE_ABS" != "$TARGET_ABS" ]; then
      echo "ERROR: target_prom_path does not match active Prometheus storage path."
      exit 87
    fi
  fi
else
  echo "WARNING: Target Prometheus is not ready before merge. Continuing with configured TARGET_PATH."
fi

find "$RECEIVE_DIR" -maxdepth 1 -type d -name "01*" -printf "%f\n" | sort > /tmp/range_received_blocks.txt

if [ ! -s /tmp/range_received_blocks.txt ]; then
  echo "ERROR: no received TSDB blocks found in $RECEIVE_DIR"
  exit 52
fi

echo "Received block count:"
wc -l /tmp/range_received_blocks.txt

echo
echo "=== Stop Prometheus before block merge ==="
sudo systemctl stop "$PROMETHEUS_SERVICE_NAME" || true
sleep 4
sudo ss -lntp | grep ':9090' || echo "Target Prometheus is stopped."

echo
echo "=== Safety check: detect overlapping blocks ==="
OVERLAP_FILE="/tmp/range_overlap_warnings.txt"
rm -f "$OVERLAP_FILE"
touch "$OVERLAP_FILE"

for rmeta in "$RECEIVE_DIR"/01*/meta.json; do
  [ -f "$rmeta" ] || continue

  rid=$(basename "$(dirname "$rmeta")")
  rmin=$(jq -r '.minTime' "$rmeta")
  rmax=$(jq -r '.maxTime' "$rmeta")
  rmin_iso=$(date -u -d "@$((rmin/1000))" '+%Y-%m-%dT%H:%M:%SZ')
  rmax_iso=$(date -u -d "@$((rmax/1000))" '+%Y-%m-%dT%H:%M:%SZ')

  echo "Received block $rid: $rmin_iso to $rmax_iso"

  # Reject obviously broken future timestamp blocks.
  # 4102444800000 = 2100-01-01T00:00:00Z in ms
  if [ "$rmin" -gt 4102444800000 ] || [ "$rmax" -gt 4102444800000 ]; then
    echo "ERROR: block $rid has impossible future timestamp."
    echo "This usually means OpenMetrics timestamps were written in milliseconds."
    exit 89
  fi

  for tmeta in "$TARGET_PATH"/01*/meta.json; do
    [ -f "$tmeta" ] || continue

    tid=$(basename "$(dirname "$tmeta")")
    [ "$rid" = "$tid" ] && continue

    tmin=$(sudo jq -r '.minTime' "$tmeta")
    tmax=$(sudo jq -r '.maxTime' "$tmeta")

    if [ "$rmax" -gt "$tmin" ] && [ "$rmin" -lt "$tmax" ]; then
      tmin_iso=$(date -u -d "@$((tmin/1000))" '+%Y-%m-%dT%H:%M:%SZ')
      tmax_iso=$(date -u -d "@$((tmax/1000))" '+%Y-%m-%dT%H:%M:%SZ')
      echo "$rid ($rmin_iso..$rmax_iso) overlaps target $tid ($tmin_iso..$tmax_iso)" >> "$OVERLAP_FILE"
    fi
  done
done

if [ -s "$OVERLAP_FILE" ]; then
  echo
  echo "ERROR: Cannot merge because TSDB block time ranges overlap."
  echo "Overlaps found:"
  cat "$OVERLAP_FILE"
  echo
  echo "Fix: choose a non-overlapping range, delete old imported range first,"
  echo "or keep imported data in a separate Prometheus datasource."
  exit 88
fi

echo "No overlapping TSDB blocks found. Safe to copy."

echo
echo "=== Copy exact-range blocks into target path ==="
while read -r id; do
  src="$RECEIVE_DIR/$id"
  dst="$TARGET_PATH/$id"

  if [ ! -d "$src" ]; then
    echo "ERROR: missing received block: $src"
    exit 60
  fi

  if [ -d "$dst" ]; then
    echo "Block already exists, skipping: $id"
  else
    echo "Copying block: $id"
    sudo cp -a "$src" "$TARGET_PATH/"
  fi
done < /tmp/range_received_blocks.txt

echo
echo "=== Fix ownership only for copied blocks ==="
while read -r id; do
  if [ -d "$TARGET_PATH/$id" ]; then
    echo "Fixing ownership for block: $id"
    sudo chown -R "$PROMETHEUS_SYSTEM_USER:$PROMETHEUS_SYSTEM_USER" "$TARGET_PATH/$id"
  fi
done < /tmp/range_received_blocks.txt

if [ "$ANALYZE_AFTER_MERGE" = "true" ]; then
  if command -v promtool >/dev/null 2>&1 && promtool tsdb --help >/dev/null 2>&1; then
    echo
    echo "=== Analyze target TSDB after copy ==="
    sudo -u "$PROMETHEUS_SYSTEM_USER" promtool tsdb analyze "$TARGET_PATH" || true
  fi
else
  echo
  echo "=== Skip full promtool analyze for faster merge ==="
fi

echo
echo "=== Start Prometheus ==="
sudo systemctl start "$PROMETHEUS_SERVICE_NAME"

for attempt in $(seq 1 60); do
  if curl -fsS "$TARGET_PROMETHEUS_URL/-/ready" >/dev/null 2>&1; then
    echo "Prometheus is ready."
    break
  fi
  sleep 1
done

curl -fsS "$TARGET_PROMETHEUS_URL/-/healthy"
echo
curl -fsS "$TARGET_PROMETHEUS_URL/-/ready"
echo

echo
echo "=== Verify imported exact-range series ==="
IMPORTED_SELECTOR=$(printf '{source_env="%s",migration_origin="%s"}' "$SOURCE_ENV_OLD" "$IMPORTED_MIGRATION_ORIGIN")
echo "Selector: $IMPORTED_SELECTOR"

SERIES_COUNT=$(curl -sG "$TARGET_PROMETHEUS_URL/api/v1/series" \
  --data-urlencode "match[]=$IMPORTED_SELECTOR" \
  --data-urlencode "start=$START_TS" \
  --data-urlencode "end=$END_TS" \
  | jq -r '.data | length')

echo "IMPORTED_SERIES_COUNT=$SERIES_COUNT"

if [ "$SERIES_COUNT" = "0" ]; then
  echo "WARNING: Merge copied blocks, but no imported series were found in selected range."
  echo "Check selected time range and labels."
fi

echo
echo "=== Current imported label values ==="
curl -s "$TARGET_PROMETHEUS_URL/api/v1/label/source_env/values" | jq . || true
curl -s "$TARGET_PROMETHEUS_URL/api/v1/label/migration_origin/values" | jq . || true
curl -s "$TARGET_PROMETHEUS_URL/api/v1/label/source_host/values" | jq . || true

echo
echo "=== Exact custom-range merge completed ==="
trap - EXIT
"""

        script = (
            script.replace("__TARGET_PATH__", _q(target_prom_path))
            .replace("__RECEIVE_DIR__", _q(target_receive_dir))
            .replace("__SOURCE_ENV_OLD__", _q(source_env_old))
            .replace("__IMPORTED_MIGRATION_ORIGIN__", _q(imported_migration_origin))
            .replace("__START_TS__", str(start_ms // 1000))
            .replace("__END_TS__", str(end_ms // 1000))
            .replace("__TARGET_PROMETHEUS_URL__", _q(target_prometheus_url))
            .replace("__PROMETHEUS_SERVICE_NAME__", _q(prometheus_service_name))
            .replace("__PROMETHEUS_SYSTEM_USER__", _q(prometheus_system_user))
            .replace("__ANALYZE_AFTER_MERGE__", "true" if analyze_after_merge else "false")
            .replace("__SUDO_AUTH__", sudo_auth)
        )

        result = self._run(target, script, timeout=7 * 24 * 3600)

        return {
            "ok": True,
            "message": "Received exact range blocks merged into target",
            "start_ms": start_ms,
            "end_ms": end_ms,
            "stdout": result["stdout"],
            "stderr": result["stderr"],
        }