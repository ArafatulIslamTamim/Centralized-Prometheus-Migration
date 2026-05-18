from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.migration_models import CommandResult, MigrationConfig
from app.services.proof_service import ProofService
from app.services.ssh_runner import SSHRunner, check_port, sudo_prefix


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MigrationService:
    def __init__(self, proof_service: ProofService):
        self.proof_service = proof_service

    def _result(
        self,
        config: MigrationConfig,
        migration_id: str,
        stage: str,
        title: str,
        started_at: datetime,
        ok: bool,
        output: str,
        proof: dict[str, Any] | None = None,
    ) -> CommandResult:
        result = CommandResult(
            ok=ok,
            title=title,
            output=output,
            started_at=started_at,
            finished_at=_now(),
            migration_id=migration_id,
            proof=proof or {},
        )
        self.proof_service.save_result(result, config, stage)
        return result

    def precheck(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        output_parts: list[str] = []
        ok = True
        proof: dict[str, Any] = {"checks": {}}

        output_parts.append("=== Local backend network check ===")
        for label, host, port in [
            (f"{config.source.name} SSH", config.source.host, 22),
            (f"{config.target.name} SSH", config.target.host, 22),
            (f"{config.target.name} Grafana", config.target.host, 3000),
        ]:
            reachable = check_port(host, port)
            proof["checks"][label] = reachable
            output_parts.append(f"{label}: {'PASS' if reachable else 'FAIL'} ({host}:{port})")
            ok = ok and reachable

        source_script = f"""
set -e
hostname
printf '\n=== Source identity ===\n'
echo "Expected hostname: {config.source.expected_hostname or 'not configured'}"
printf '\n=== Source Prometheus path ===\n'
if [ -d {config.source_prom_path!r} ]; then ls -ld {config.source_prom_path!r}; else echo 'MISSING: {config.source_prom_path}'; exit 11; fi
printf '\n=== Source Prometheus binary ===\n'
if [ -x {config.prom_bin!r} ]; then {config.prom_bin!r} --version | head -3; else echo 'MISSING or not executable: {config.prom_bin}'; exit 12; fi
printf '\n=== Source TSDB blocks ===\n'
find {config.source_prom_path!r} -maxdepth 1 -type d -name '01*' -printf '%f\n' 2>/dev/null | sort || true
printf '\n=== Source monitoring ports ===\n'
ss -lntp | grep -E '9090|9100|9256|3000' || true
"""
        target_script = f"""
set -e
hostname
printf '\n=== Target identity ===\n'
echo "Expected hostname: {config.target.expected_hostname or 'not configured'}"
printf '\n=== Target Prometheus health ===\n'
curl -s http://localhost:9090/-/healthy || true
curl -s http://localhost:9090/-/ready || true
printf '\n=== Target Prometheus path ===\n'
if [ -d {config.target_prom_path!r} ]; then ls -ld {config.target_prom_path!r}; else echo 'MISSING: {config.target_prom_path}'; exit 21; fi
printf '\n=== Target receive and backup directories ===\n'
mkdir -p {config.target_receive_dir!r} {config.target_backup_dir!r}
ls -ld {config.target_receive_dir!r} {config.target_backup_dir!r}
printf '\n=== Target disk ===\n'
du -sh {config.target_prom_path!r} 2>/dev/null || true
df -h
printf '\n=== Target source_env labels ===\n'
curl -s http://localhost:9090/api/v1/label/source_env/values | jq . || true
"""
        try:
            with SSHRunner(config.source) as src:
                res = src.run_bash(source_script, timeout=120)
                output_parts.append("\n=== Source precheck output ===\n" + res.combined)
                ok = ok and res.exit_code == 0
        except Exception as e:
            output_parts.append(f"\nSource precheck error: {e}")
            ok = False

        try:
            with SSHRunner(config.target) as tgt:
                res = tgt.run_bash(target_script, timeout=120)
                output_parts.append("\n=== Target precheck output ===\n" + res.combined)
                ok = ok and res.exit_code == 0
        except Exception as e:
            output_parts.append(f"\nTarget precheck error: {e}")
            ok = False

        return self._result(config, migration_id, "precheck", "Pre-checks", started_at, ok, "\n".join(output_parts), proof)

    def create_lm2_backup(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        sudo_pass = config.target.sudo_password or config.target.ssh_password or ""
        sudo_auth = sudo_prefix(sudo_pass)
        script = f"""
set -euo pipefail
TARGET_PROM_PATH={config.target_prom_path!r}
TARGET_BACKUP_DIR={config.target_backup_dir!r}

echo "=== Authenticate sudo on target ==="
{sudo_auth}

echo "=== Create target backup directory ==="
mkdir -p "$TARGET_BACKUP_DIR"
ls -ld "$TARGET_BACKUP_DIR"

echo "=== Estimate target Prometheus data size ==="
sudo du -sh "$TARGET_PROM_PATH" || true

echo "=== Create rollback backup before cleanup or merge ==="
BACKUP_FILE="$TARGET_BACKUP_DIR/lm2-prometheus-backup-before-migration-$(date +%Y%m%d-%H%M%S).tar.gz"
sudo tar -C /var/lib -czf "$BACKUP_FILE" "$(basename "$TARGET_PROM_PATH")"
sudo chown "$USER":"$USER" "$BACKUP_FILE" || true
ls -lh "$BACKUP_FILE"
echo "BACKUP_FILE=$BACKUP_FILE"
"""
        try:
            with SSHRunner(config.target) as tgt:
                res = tgt.run_bash(script, timeout=7200)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"Backup error: {e}"
        return self._result(config, migration_id, "lm2_backup", "LM2 Rollback Backup", started_at, ok, output)

    def lm1_create_snapshot(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        sudo_pass = config.source.sudo_password or config.source.ssh_password or ""
        sudo_auth = sudo_prefix(sudo_pass)
        script = f"""
set -euo pipefail
SOURCE_ENV_OLD={config.source_env_old!r}
LM1_PROM_PATH={config.source_prom_path!r}
PROM_BIN={config.prom_bin!r}
PROM_RETENTION_TIME={config.prom_retention_time!r}
LM1_DATA_START={config.lm1_data_start!r}
LM1_DATA_END={config.lm1_data_end!r}
START_TS=$(date -d "$LM1_DATA_START" +%s)
END_TS=$(date -d "$LM1_DATA_END" +%s)
RANGE_SEC=$((END_TS - START_TS))
PROOF_DIR="$HOME/lm1-migration-proof"
mkdir -p "$PROOF_DIR"

echo "=== Migration parameters ==="
echo "SOURCE_ENV_OLD=$SOURCE_ENV_OLD"
echo "LM1_PROM_PATH=$LM1_PROM_PATH"
echo "LM1 data range: $LM1_DATA_START to $LM1_DATA_END"
echo "RANGE_SEC=$RANGE_SEC"

if [ ! -d "$LM1_PROM_PATH" ]; then
  echo "ERROR: Source Prometheus data path does not exist: $LM1_PROM_PATH"
  exit 10
fi

if [ ! -x "$PROM_BIN" ]; then
  echo "ERROR: Prometheus binary not found or not executable: $PROM_BIN"
  exit 11
fi

echo "=== Authenticate sudo on source ==="
{sudo_auth}

echo "=== Stop normal source monitoring services ==="
sudo systemctl stop prometheus || true
sudo systemctl stop node_exporter || true
sudo systemctl stop process_exporter || true
sudo systemctl stop grafana-server || true
sudo ss -lntp | grep -E "9090|9100|9256|3000" || echo "Source monitoring ports are stopped."

echo "=== Create temporary no-scrape Prometheus config ==="
cat > /tmp/prometheus-snapshot.yml <<'PROMEOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s
scrape_configs: []
PROMEOF

echo "=== Start temporary no-scrape Prometheus on source ==="
sudo -u prometheus nohup "$PROM_BIN" \
  --config.file=/tmp/prometheus-snapshot.yml \
  --storage.tsdb.path="$LM1_PROM_PATH" \
  --web.listen-address=127.0.0.1:9090 \
  --web.enable-admin-api \
  --storage.tsdb.retention.time="$PROM_RETENTION_TIME" \
  > /tmp/prometheus-snapshot.log 2>&1 &

sleep 5
curl http://localhost:9090/-/healthy
curl http://localhost:9090/-/ready

echo "=== Count source samples before snapshot ==="

OLD_SELECTOR=$(printf '{{source_env="%s"}}' "$SOURCE_ENV_OLD")

FILTERED_TOTAL_QUERY="sum(count_over_time(up$OLD_SELECTOR[${{RANGE_SEC}}s]))"
FILTERED_GROUP_QUERY="sum by (job, instance, source_env) (count_over_time(up$OLD_SELECTOR[${{RANGE_SEC}}s]))"
DEBUG_GROUP_QUERY="sum by (job, instance, source_env) (count_over_time(up[${{RANGE_SEC}}s]))"

echo "Filtered total query:"
echo "$FILTERED_TOTAL_QUERY"

TOTAL_COUNT=$(curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=$FILTERED_TOTAL_QUERY" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[0].value[1] // "0"')

echo "$TOTAL_COUNT" | tee "$PROOF_DIR/lm1_up_total_before_snapshot.txt"

if [ "$TOTAL_COUNT" = "0" ]; then
  echo "ERROR: No source samples found for source_env=$SOURCE_ENV_OLD in the selected time range."
  echo "SOURCE_ENV_OLD=$SOURCE_ENV_OLD"
  echo "LM1_DATA_START=$LM1_DATA_START"
  echo "LM1_DATA_END=$LM1_DATA_END"
  echo "START_TS=$START_TS"
  echo "END_TS=$END_TS"
  echo "RANGE_SEC=$RANGE_SEC"
  echo
  echo "Debug: source_env label values:"
  curl -s http://localhost:9090/api/v1/label/source_env/values | jq
  echo
  echo "Debug: up samples without source_env filter:"
  curl -sG http://localhost:9090/api/v1/query \
    --data-urlencode "query=$DEBUG_GROUP_QUERY" \
    --data-urlencode "time=${{END_TS}}" \
    | jq '.data.result[]? | {{job: .metric.job, instance: .metric.instance, source_env: (.metric.source_env // "MISSING"), samples: .value[1]}}'
  exit 20
fi

curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=$FILTERED_GROUP_QUERY" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[]? | [(.metric.job // "unknown_job"), (.metric.instance // "unknown_instance"), (.metric.source_env // "unknown_source"), .value[1]] | @tsv' \
  | sort \
  | tee "$PROOF_DIR/lm1_up_by_job_instance_before_snapshot.tsv"

echo "=== Create source Prometheus TSDB snapshot ==="
SNAPSHOT_NAME=$(curl -s -XPOST "http://localhost:9090/api/v1/admin/tsdb/snapshot?skip_head=false" | jq -r '.data.name')
if [ -z "$SNAPSHOT_NAME" ] || [ "$SNAPSHOT_NAME" = "null" ]; then
  echo "ERROR: Snapshot creation failed. Check that --web.enable-admin-api is active."
  exit 12
fi

echo "SNAPSHOT_NAME=$SNAPSHOT_NAME" | tee "$PROOF_DIR/snapshot_name.txt"
echo "Snapshot path: $LM1_PROM_PATH/snapshots/$SNAPSHOT_NAME"
sudo ls -lh "$LM1_PROM_PATH/snapshots/$SNAPSHOT_NAME" | head

echo "=== Stop temporary source Prometheus ==="
TEMP_PROM_PID=$(sudo ss -lntp | grep '127.0.0.1:9090' | grep 'prometheus' | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)

if [ -n "$TEMP_PROM_PID" ]; then
  echo "Stopping temporary Prometheus PID: $TEMP_PROM_PID"
  sudo kill "$TEMP_PROM_PID" || true
  sleep 2
else
  echo "No temporary Prometheus PID found on 127.0.0.1:9090"
fi
sudo ss -lntp | grep 9090 || echo "Temporary source Prometheus stopped."

echo "=== Snapshot creation completed. Transfer can run next. ==="
"""
        try:
            with SSHRunner(config.source) as src:
                res = src.run_bash(script, timeout=3600)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"LM1 snapshot creation error: {e}"
        return self._result(config, migration_id, "lm1_create_snapshot", "Create LM1 Snapshot", started_at, ok, output)

    def lm1_transfer_snapshot(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        sudo_pass = config.source.sudo_password or config.source.ssh_password or ""
        sudo_auth = sudo_prefix(sudo_pass)
        snapshot_assignment = f"SNAPSHOT_NAME={config.snapshot_name!r}" if config.snapshot_name else "SNAPSHOT_NAME=$(cut -d= -f2 $HOME/lm1-migration-proof/snapshot_name.txt)"
        script = f"""
set -euo pipefail
LM1_PROM_PATH={config.source_prom_path!r}
LM2_USER={config.target.user!r}
LM2_IP={config.target.host!r}
LM2_RECEIVE_DIR={config.target_receive_dir!r}
PROOF_DIR="$HOME/lm1-migration-proof"
{snapshot_assignment}

echo "=== Authenticate sudo on source ==="
{sudo_auth}

echo "=== Transfer parameters ==="
echo "SNAPSHOT_NAME=$SNAPSHOT_NAME"
echo "Snapshot path: $LM1_PROM_PATH/snapshots/$SNAPSHOT_NAME"
echo "Target: $LM2_USER@$LM2_IP:$LM2_RECEIVE_DIR"

if [ ! -d "$LM1_PROM_PATH/snapshots/$SNAPSHOT_NAME" ]; then
  echo "ERROR: Snapshot folder not found: $LM1_PROM_PATH/snapshots/$SNAPSHOT_NAME"
  exit 20
fi

echo "=== Transfer source snapshot directly to target ==="
echo "NOTE: This requires passwordless SSH from source to target. Run ssh-copy-id first if needed."
sudo tar -C "$LM1_PROM_PATH/snapshots/$SNAPSHOT_NAME" -cf - . \
  | ssh -o BatchMode=yes "$LM2_USER@$LM2_IP" \
  "rm -rf '$LM2_RECEIVE_DIR' && mkdir -p '$LM2_RECEIVE_DIR' && tar -C '$LM2_RECEIVE_DIR' -xf -"

echo "=== Transfer proof files to target ==="
scp -o BatchMode=yes "$PROOF_DIR/lm1_up_total_before_snapshot.txt" \
    "$PROOF_DIR/lm1_up_by_job_instance_before_snapshot.tsv" \
    "$PROOF_DIR/snapshot_name.txt" \
    "$LM2_USER@$LM2_IP:/tmp/"

echo "=== Snapshot transfer completed ==="
"""
        try:
            with SSHRunner(config.source) as src:
                res = src.run_bash(script, timeout=7200)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"Snapshot transfer error: {e}"
        return self._result(config, migration_id, "lm1_transfer_snapshot", "Transfer Snapshot to LM2", started_at, ok, output)

    def lm2_cleanup_old_source(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        expected = f"DELETE {config.source_env_old} FROM {config.target.name}"
        if (config.cleanup_confirmation or "").strip() != expected:
            return self._result(
                config,
                migration_id,
                "lm2_cleanup_old_source",
                "Optional LM2 Cleanup",
                started_at,
                False,
                f"Confirmation mismatch. Type exactly: {expected}",
            )
        sudo_pass = config.target.sudo_password or config.target.ssh_password or ""
        sudo_auth = sudo_prefix(sudo_pass)
        script = f"""
set -euo pipefail
SOURCE_ENV_OLD={config.source_env_old!r}
OLD_SELECTOR=$(printf '{{source_env="%s"}}' "$SOURCE_ENV_OLD")
LM1_DATA_START={config.lm1_data_start!r}
LM1_DATA_END={config.lm1_data_end!r}
START_TS=$(date -d "$LM1_DATA_START" +%s)
END_TS=$(date -d "$LM1_DATA_END" +%s)
RANGE_SEC=$((END_TS - START_TS))

echo "=== Authenticate sudo on target ==="
{sudo_auth}

echo "=== Check existing source-labelled data on target before cleanup ==="
echo "Selector: $OLD_SELECTOR"
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=sum(count_over_time(up${{OLD_SELECTOR}}[${{RANGE_SEC}}s]))" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[0].value[1] // "0"' \
  | tee /tmp/lm1_count_before_cleanup_on_lm2.txt

echo "=== Delete old source-labelled series from target ==="
DELETE_STATUS=$(curl -s -o /tmp/delete_series_response.txt -w "%{{http_code}}" \
  -X POST http://localhost:9090/api/v1/admin/tsdb/delete_series \
  --data-urlencode "match[]=$OLD_SELECTOR")

echo "delete_series HTTP status: $DELETE_STATUS"
cat /tmp/delete_series_response.txt || true
echo

if [ "$DELETE_STATUS" != "204" ]; then
  echo "ERROR: delete_series failed. Expected HTTP 204."
  echo "Selector was: $OLD_SELECTOR"
  exit 51
fi

echo "=== Clean tombstones ==="
CLEAN_STATUS=$(curl -s -o /tmp/clean_tombstones_response.txt -w "%{{http_code}}" \
  -X POST http://localhost:9090/api/v1/admin/tsdb/clean_tombstones)

echo "clean_tombstones HTTP status: $CLEAN_STATUS"
cat /tmp/clean_tombstones_response.txt || true
echo

if [ "$CLEAN_STATUS" != "204" ]; then
  echo "ERROR: clean_tombstones failed. Expected HTTP 204."
  exit 52
fi

echo "=== Restart target Prometheus ==="
sudo systemctl restart prometheus
sleep 5
curl http://localhost:9090/-/healthy
curl http://localhost:9090/-/ready

echo "=== Verify source-labelled data was removed from target ==="
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=sum(count_over_time(up${{OLD_SELECTOR}}[${{RANGE_SEC}}s]))" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[0].value[1] // "0"' \
  | tee /tmp/lm1_count_after_cleanup_on_lm2.txt
"""
        try:
            with SSHRunner(config.target) as tgt:
                res = tgt.run_bash(script, timeout=1200)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"Cleanup error: {e}"
        return self._result(config, migration_id, "lm2_cleanup_old_source", "Optional LM2 Cleanup", started_at, ok, output)

    def lm2_merge(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        sudo_pass = config.target.sudo_password or config.target.ssh_password or ""
        sudo_auth = sudo_prefix(sudo_pass)
        script = f"""
set -euo pipefail
SOURCE_ENV_OLD={config.source_env_old!r}
SOURCE_ENV_NEW={config.source_env_new!r}
LM2_PROM_PATH={config.target_prom_path!r}
LM2_RECEIVE_DIR={config.target_receive_dir!r}
LM1_DATA_START={config.lm1_data_start!r}
LM1_DATA_END={config.lm1_data_end!r}
START_TS=$(date -d "$LM1_DATA_START" +%s)
END_TS=$(date -d "$LM1_DATA_END" +%s)
RANGE_SEC=$((END_TS - START_TS))

echo "=== Authenticate sudo on target ==="
{sudo_auth}

echo "=== Check received source snapshot blocks ==="
ls -lh "$LM2_RECEIVE_DIR"
find "$LM2_RECEIVE_DIR" -maxdepth 1 -type d -name "01*" -printf "%f\n" | sort > /tmp/lm1_snapshot_blocks.txt
cat /tmp/lm1_snapshot_blocks.txt

echo "=== Stop target Prometheus before merge ==="
sudo systemctl stop prometheus
sudo ss -lntp | grep 9090 || echo "Target Prometheus stopped."

echo "=== Check duplicate TSDB block IDs ==="
sudo find "$LM2_PROM_PATH" -maxdepth 1 -type d -name "01*" -printf "%f\n" | sort > /tmp/lm2_existing_blocks.txt
DUPES=$(comm -12 /tmp/lm1_snapshot_blocks.txt /tmp/lm2_existing_blocks.txt || true)
echo "Duplicate block IDs:"
echo "$DUPES"

echo "=== Copy source TSDB blocks into target Prometheus storage if missing ==="
for block in "$LM2_RECEIVE_DIR"/01*; do
  id=$(basename "$block")
  if [ -d "$LM2_PROM_PATH/$id" ]; then
    echo "Replacing existing source block in LM2 storage: $id"
    sudo rm -rf "$LM2_PROM_PATH/$id"
  fi

  echo "Copying fresh source block: $id"
  sudo cp -a "$block" "$LM2_PROM_PATH/"
done

echo "=== Fix Prometheus ownership ==="
sudo chown -R prometheus:prometheus "$LM2_PROM_PATH"

echo "=== Analyze merged TSDB ==="
sudo -u prometheus promtool tsdb analyze "$LM2_PROM_PATH"

echo "=== Start target Prometheus ==="
sudo systemctl start prometheus
sleep 5
curl http://localhost:9090/-/healthy
curl http://localhost:9090/-/ready

echo "=== Verify source_env values ==="
curl -s http://localhost:9090/api/v1/label/source_env/values | jq

echo "=== Count source samples after merge on target ==="
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=sum(count_over_time(up{{source_env=\"${{SOURCE_ENV_OLD}}\"}}[${{RANGE_SEC}}s]))" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[0].value[1] // "0"' \
  | tee /tmp/lm1_up_total_after_merge_on_lm2.txt

curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=sum by (job, instance) (count_over_time(up{{source_env=\"${{SOURCE_ENV_OLD}}\"}}[${{RANGE_SEC}}s]))" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[]? | [(.metric.job // "unknown_job"), (.metric.instance // "unknown_instance"), .value[1]] | @tsv' \
  | sort \
  | tee /tmp/lm1_up_by_job_instance_after_merge_on_lm2.tsv

echo "=== Compare source sample counts before snapshot vs after merge ==="
if [ -f /tmp/lm1_up_total_before_snapshot.txt ]; then
  diff -u /tmp/lm1_up_total_before_snapshot.txt /tmp/lm1_up_total_after_merge_on_lm2.txt \
    && echo "PASS: Source total up sample count matches." \
    || echo "FAIL: Source total up sample count does not match."
else
  echo "WARNING: /tmp/lm1_up_total_before_snapshot.txt not found. Skipping total comparison."
fi

if [ -f /tmp/lm1_up_by_job_instance_before_snapshot.tsv ]; then
  diff -u /tmp/lm1_up_by_job_instance_before_snapshot.tsv /tmp/lm1_up_by_job_instance_after_merge_on_lm2.tsv \
    && echo "PASS: Source up sample count by job/instance matches." \
    || echo "FAIL: Source up sample count by job/instance does not match."
else
  echo "WARNING: /tmp/lm1_up_by_job_instance_before_snapshot.tsv not found. Skipping job/instance comparison."
fi


echo "NOTE: Merge completed. Strict source sample validation is handled by the Run Validation step."

echo "=== Validate target live/current data ==="
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=up{{source_env=\"${{SOURCE_ENV_NEW}}\"}}" \
  | jq '.data.result[]? | {{job: .metric.job, instance: .metric.instance, source_env: .metric.source_env, value: .value[1]}}'

echo "=== Target merge and validation completed ==="
"""
        try:
            with SSHRunner(config.target) as tgt:
                res = tgt.run_bash(script, timeout=3600)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"LM2 merge error: {e}"
        return self._result(config, migration_id, "lm2_merge", "LM2 Merge", started_at, ok, output)

    def validate(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        script = f"""
set -e
SOURCE_ENV_OLD={config.source_env_old!r}
SOURCE_ENV_NEW={config.source_env_new!r}
LM1_DATA_START={config.lm1_data_start!r}
LM1_DATA_END={config.lm1_data_end!r}
START_TS=$(date -d "$LM1_DATA_START" +%s)
END_TS=$(date -d "$LM1_DATA_END" +%s)
RANGE_SEC=$((END_TS - START_TS))

echo "=== Target Prometheus health ==="
curl http://localhost:9090/-/healthy
curl http://localhost:9090/-/ready

echo "=== source_env label values ==="
curl -s http://localhost:9090/api/v1/label/source_env/values | jq

echo "=== Source imported sample count by job/instance ==="
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=sum by (job, instance) (count_over_time(up{{source_env=\"${{SOURCE_ENV_OLD}}\"}}[${{RANGE_SEC}}s]))" \
  --data-urlencode "time=${{END_TS}}" \
  | jq '.data.result[]? | {{job: .metric.job, instance: .metric.instance, samples: .value[1]}}'



echo "=== Target live target status ==="
curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=up{{source_env=\"${{SOURCE_ENV_NEW}}\"}}" \
  | jq '.data.result[]? | {{job: .metric.job, instance: .metric.instance, value: .value[1]}}'


echo "=== Retry source sample count before final validation ==="

VALIDATE_SELECTOR=$(printf '{{source_env="%s"}}' "$SOURCE_ENV_OLD")
VALIDATE_TOTAL_QUERY="sum(count_over_time(up$VALIDATE_SELECTOR[${{RANGE_SEC}}s]))"
VALIDATE_GROUP_QUERY="sum by (job, instance, source_env) (count_over_time(up$VALIDATE_SELECTOR[${{RANGE_SEC}}s]))"

echo "Validation total query:"
echo "$VALIDATE_TOTAL_QUERY"

VALIDATION_AFTER_COUNT="0"

for attempt in $(seq 1 12); do
  echo "Validation count attempt $attempt/12..."

  VALIDATION_AFTER_COUNT=$(curl -sG http://localhost:9090/api/v1/query \
    --data-urlencode "query=$VALIDATE_TOTAL_QUERY" \
    --data-urlencode "time=${{END_TS}}" \
    | jq -r '.data.result[0].value[1] // "0"')

  echo "$VALIDATION_AFTER_COUNT" | tee /tmp/lm1_up_total_after_merge_on_lm2.txt

  if [ "$VALIDATION_AFTER_COUNT" != "0" ]; then
    break
  fi

  sleep 5
done

curl -sG http://localhost:9090/api/v1/query \
  --data-urlencode "query=$VALIDATE_GROUP_QUERY" \
  --data-urlencode "time=${{END_TS}}" \
  | jq -r '.data.result[]? | [(.metric.job // "unknown_job"), (.metric.instance // "unknown_instance"), (.metric.source_env // "unknown_source"), .value[1]] | @tsv' \
  | sort \
  | tee /tmp/lm1_up_by_job_instance_after_merge_on_lm2.tsv

if ! diff -q /tmp/lm1_up_total_before_snapshot.txt /tmp/lm1_up_total_after_merge_on_lm2.txt >/dev/null; then
  echo "ERROR: Validation failed because source total sample count mismatch."
  exit 41
fi

if ! diff -q /tmp/lm1_up_by_job_instance_before_snapshot.tsv /tmp/lm1_up_by_job_instance_after_merge_on_lm2.tsv >/dev/null; then
  echo "ERROR: Validation failed because source sample count by job/instance mismatch."
  exit 42
fi

echo "PASS: Validation sample counts match after retry."

echo "=== Optional comparison files ==="
if [ -f /tmp/lm1_up_total_before_snapshot.txt ] && [ -f /tmp/lm1_up_total_after_merge_on_lm2.txt ]; then
  diff -u /tmp/lm1_up_total_before_snapshot.txt /tmp/lm1_up_total_after_merge_on_lm2.txt && echo "PASS: total count matches" || echo "FAIL: total count mismatch"
else
  echo "Comparison files are not both present. Run snapshot/transfer and merge first."
fi
"""
        try:
            with SSHRunner(config.target) as tgt:
                res = tgt.run_bash(script, timeout=300)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"Validation error: {e}"
        return self._result(config, migration_id, "validate", "Validation", started_at, ok, output)

    def grafana_check(self, config: MigrationConfig) -> CommandResult:
        migration_id = config.migration_id or self.proof_service.new_migration_id()
        started_at = _now()
        password = config.grafana_password or ""
        script = f"""
set -e
GRAFANA_URL={config.grafana_url!r}
GRAFANA_USER={config.grafana_user!r}
GRAFANA_PASS={password!r}

echo "=== Grafana health ==="
curl -s "$GRAFANA_URL/api/health" -u "$GRAFANA_USER:$GRAFANA_PASS" | jq

echo "=== Grafana datasources ==="
curl -s "$GRAFANA_URL/api/datasources" -u "$GRAFANA_USER:$GRAFANA_PASS" \
  | jq '.[] | {{name, type, url, access, isDefault}}'
"""
        try:
            with SSHRunner(config.target) as tgt:
                res = tgt.run_bash(script, timeout=120)
                ok = res.exit_code == 0
                output = res.combined
        except Exception as e:
            ok = False
            output = f"Grafana check error: {e}"
        return self._result(config, migration_id, "grafana_check", "Grafana Check", started_at, ok, output)
