from __future__ import annotations

import json
import posixpath
from typing import Any, Dict, List

from app.models.config_models import AppConfig
from app.services.block_service import check_remote_overlaps, classify_selected_blocks, scan_remote_blocks
from app.services.shell_utils import bash_script, q
from app.services.ssh_runner import SSHRunner
from app.services.time_utils import now_id, parse_utc_to_ms, utc_iso_from_ms


def _ids(blocks: List[Dict[str, Any]]) -> List[str]:
    return [b["id"] for b in blocks]


def build_plan(cfg: AppConfig, target: SSHRunner, snapshot_path_on_target: str, start_utc: str, end_utc: str, label: str, mode: str, selection: str = "overlap") -> Dict[str, Any]:
    start_ms = parse_utc_to_ms(start_utc)
    end_ms = parse_utc_to_ms(end_utc)
    if end_ms <= start_ms:
        raise ValueError("end_utc must be later than start_utc")

    source_blocks = scan_remote_blocks(target, snapshot_path_on_target, use_sudo=False)
    target_blocks = scan_remote_blocks(target, cfg.prometheus.target_data_path, use_sudo=True)
    classified = classify_selected_blocks(source_blocks, target_blocks, start_ms, end_ms, selection)

    plan_id = f"{label}_{now_id()}".replace(" ", "_")
    target_plan_dir = posixpath.join(cfg.record_dir_on_target, "plans", plan_id)
    backup_dir = posixpath.join(cfg.record_dir_on_target, "backups", f"replaced_target_blocks_{plan_id}")

    can_execute = True
    reasons = []
    if classified["selectedCount"] == 0:
        can_execute = False
        reasons.append("No source blocks matched the selected UTC range with the chosen selection rule.")
    if mode == "safe_merge" and classified["overlapCount"] > 0:
        can_execute = False
        reasons.append("Safe merge cannot continue because selected source blocks overlap target /data blocks.")
    if mode == "replacement" and classified["targetBackupCount"] == 0 and classified["overlapCount"] > 0:
        can_execute = False
        reasons.append("Internal error: overlap found but no target backup blocks were selected.")

    plan = {
        "planId": plan_id,
        "mode": mode,
        "label": label,
        "createdAtUtc": now_id(),
        "snapshotPathOnTarget": snapshot_path_on_target,
        "targetDataPath": cfg.prometheus.target_data_path,
        "targetPlanDir": target_plan_dir,
        "backupDir": backup_dir,
        "startUtcRequested": start_utc,
        "endUtcRequested": end_utc,
        "blockSelectionRule": selection,
        "startMs": start_ms,
        "endMs": end_ms,
        "startIso": utc_iso_from_ms(start_ms),
        "endIso": utc_iso_from_ms(end_ms),
        "canExecute": can_execute,
        "reasons": reasons,
        **classified,
        "sourceBlockIdsToCopy": _ids(classified["copySourceBlocks"]),
        "targetBlockIdsToBackup": _ids(classified["targetBlocksToBackup"]),
    }

    # Write plan to target as audit artifacts. No data is changed here.
    target.run("mkdir -p " + q(target_plan_dir), timeout=30)
    target.upload_text(posixpath.join(target_plan_dir, "plan.json"), json.dumps(plan, indent=2))
    target.upload_text(posixpath.join(target_plan_dir, "source_blocks_to_copy.txt"), "\n".join(plan["sourceBlockIdsToCopy"]) + ("\n" if plan["sourceBlockIdsToCopy"] else ""))
    target.upload_text(posixpath.join(target_plan_dir, "target_blocks_to_backup.txt"), "\n".join(plan["targetBlockIdsToBackup"]) + ("\n" if plan["targetBlockIdsToBackup"] else ""))
    target.upload_text(posixpath.join(target_plan_dir, "backup_dir.txt"), backup_dir + "\n")
    return plan


def execute_plan(cfg: AppConfig, target: SSHRunner, plan: Dict[str, Any], confirmation: str) -> Dict[str, Any]:
    mode = plan["mode"]
    if not plan.get("canExecute"):
        raise RuntimeError("plan is not executable: " + "; ".join(plan.get("reasons") or []))
    if mode == "replacement" and confirmation != "YES":
        raise RuntimeError("replacement mode requires exact uppercase confirmation: YES")
    if mode == "safe_merge" and confirmation not in ("", "YES"):
        raise RuntimeError("safe merge confirmation must be empty or YES")

    source_ids = plan.get("sourceBlockIdsToCopy") or []
    backup_ids = plan.get("targetBlockIdsToBackup") or []
    data_path = plan["targetDataPath"]
    snapshot_path = plan["snapshotPathOnTarget"]
    plan_dir = plan["targetPlanDir"]
    backup_dir = plan["backupDir"]
    service = cfg.prometheus.prometheus_service
    owner = cfg.prometheus.prometheus_owner

    src_list = "\n".join(source_ids) + ("\n" if source_ids else "")
    bkp_list = "\n".join(backup_ids) + ("\n" if backup_ids else "")
    target.upload_text(posixpath.join(plan_dir, "execute_source_blocks_to_copy.txt"), src_list)
    target.upload_text(posixpath.join(plan_dir, "execute_target_blocks_to_backup.txt"), bkp_list)

    shell = f'''
set -euo pipefail
cleanup() {{
  rc=$?
  if [ $rc -ne 0 ]; then
    echo "ERROR: execution failed with rc=$rc; attempting to start Prometheus before exit" >&2
    systemctl start {q(service)} || true
  fi
  exit $rc
}}
trap cleanup EXIT

echo "=== Plan ==="
echo "mode={mode}"
echo "data_path={data_path}"
echo "snapshot_path={snapshot_path}"
echo "backup_dir={backup_dir}"
echo

if [ ! -d {q(data_path)} ]; then
  echo "Target data path missing: {data_path}" >&2
  exit 10
fi
if [ ! -d {q(snapshot_path)} ]; then
  echo "Snapshot/staging path missing: {snapshot_path}" >&2
  exit 11
fi

mkdir -p {q(plan_dir)}
if [ {q(mode)} = "replacement" ]; then
  if [ -e {q(backup_dir)} ]; then
    echo "Backup dir already exists; refusing to reuse: {backup_dir}" >&2
    exit 12
  fi
  mkdir -p {q(backup_dir)}
fi

echo "=== Stop Prometheus ==="
systemctl stop {q(service)}

echo "=== Move overlapping target blocks to backup, if replacement ==="
if [ {q(mode)} = "replacement" ]; then
  while IFS= read -r id; do
    [ -z "$id" ] && continue
    if [ ! -d {q(data_path)}/"$id" ]; then
      echo "Target block to backup not found: $id" >&2
      exit 20
    fi
    mv {q(data_path)}/"$id" {q(backup_dir)}/"$id"
    echo "$id" >> {q(plan_dir)}/backed_up_target_blocks.txt
  done < {q(plan_dir)}/execute_target_blocks_to_backup.txt
fi

echo "=== Copy selected source blocks into target data path ==="
while IFS= read -r id; do
  [ -z "$id" ] && continue
  if [ ! -d {q(snapshot_path)}/"$id" ]; then
    echo "Source block not found in staging: $id" >&2
    exit 30
  fi
  if [ -e {q(data_path)}/"$id" ]; then
    echo "Destination block already exists, refusing overwrite: $id" >&2
    exit 31
  fi
  cp -a {q(snapshot_path)}/"$id" {q(data_path)}/"$id"
  echo "$id" >> {q(plan_dir)}/copied_source_blocks.txt
done < {q(plan_dir)}/execute_source_blocks_to_copy.txt

echo "=== Fix ownership ==="
chown -R {q(owner)} {q(data_path)}

echo "=== Start Prometheus ==="
systemctl start {q(service)}
sleep 5
trap - EXIT

echo "=== Status ==="
systemctl is-active {q(service)}
'''
    res = target.run_sudo(shell, timeout=7200)

    # Always try health/ready and overlap check after execution attempt.
    ready = target.run("curl -fsS " + q(cfg.prometheus.target_url_from_target.rstrip("/") + "/-/ready"), timeout=30)
    healthy = target.run("curl -fsS " + q(cfg.prometheus.target_url_from_target.rstrip("/") + "/-/healthy"), timeout=30)
    overlap = None
    try:
        overlap = check_remote_overlaps(target, data_path, use_sudo=True)
    except Exception as e:
        overlap = {"error": str(e)}

    copied_count_res = target.run("test -f " + q(posixpath.join(plan_dir, "copied_source_blocks.txt")) + " && wc -l < " + q(posixpath.join(plan_dir, "copied_source_blocks.txt")) + " || echo 0", timeout=30)
    backed_count_res = target.run("test -f " + q(posixpath.join(plan_dir, "backed_up_target_blocks.txt")) + " && wc -l < " + q(posixpath.join(plan_dir, "backed_up_target_blocks.txt")) + " || echo 0", timeout=30)

    result = {
        "ok": bool(res.ok and ready.ok and healthy.ok and isinstance(overlap, dict) and overlap.get("overlapCount") == 0),
        "mainCommand": res.model_dump(),
        "ready": ready.model_dump(),
        "healthy": healthy.model_dump(),
        "overlapCheck": overlap,
        "copiedCount": int((copied_count_res.stdout or "0").strip() or 0) if copied_count_res.ok else None,
        "backedUpCount": int((backed_count_res.stdout or "0").strip() or 0) if backed_count_res.ok else None,
        "expectedCopyCount": len(source_ids),
        "expectedBackupCount": len(backup_ids),
        "targetPlanDir": plan_dir,
        "backupDir": backup_dir,
    }
    target.upload_text(posixpath.join(plan_dir, "execution_result.json"), json.dumps(result, indent=2))
    return result
