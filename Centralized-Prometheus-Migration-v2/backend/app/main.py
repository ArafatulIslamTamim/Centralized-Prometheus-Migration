from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.models.config_models import (
    AnnotationRangePayload,
    AppConfig,
    BlockPlanPayload,
    ConfigPayload,
    ExecutePlanPayload,
    ImportAnnotationPayload,
    OfflineTsdbTransferPayload,
    RolePayload,
    SnapshotTransferPayload,
)
from app.services.annotation_service import export_annotations, import_annotations, test_grafana, verify_annotations
from app.services.block_service import check_remote_overlaps, scan_remote_blocks
from app.services.config_service import CONFIG_PATH, load_config, save_config
from app.services.merge_service import build_plan, execute_plan
from app.services.proof_service import ProofService
from app.services.shell_utils import q
from app.services.snapshot_service import create_source_snapshot, list_source_snapshots, transfer_snapshot_controller_bridge, transfer_snapshot_target_pull, transfer_source_tsdb_blocks_controller_bridge, transfer_source_tsdb_blocks_target_pull
from app.services.ssh_runner import SSHRunner

app = FastAPI(title="User Friendly Safe Snapshot Prometheus/Grafana Migration GUI", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



def _home_for_user(user: str) -> str:
    return f"/home/{user}" if user else "/tmp"


def _url_with_replaced_host(old_url: str, new_host: str, default_port: int) -> str:
    """Preserve scheme/port/path, but replace stale localhost/old-host with the configured remote host."""
    if not new_host:
        return old_url or ""
    if not old_url:
        return f"http://{new_host}:{default_port}"
    try:
        parsed = urlparse(old_url)
        scheme = parsed.scheme or "http"
        port = parsed.port or default_port
        # Preserve the existing port because target Grafana may be 3002.
        netloc = f"{new_host}:{port}"
        path = parsed.path if parsed.path not in ("", "/") else ""
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception:
        return f"http://{new_host}:{default_port}"


def normalize_config_for_generalized_use(c: AppConfig) -> AppConfig:
    """Repair empty or old demo defaults after the user changes hosts/users.

    This keeps the GUI generalized and prevents stale LM1/LM2 demo values such as
    /home/iperf, 192.168.10.107, or localhost:3002 from being used when the
    controller is running on another PC.
    """
    d = c.model_dump()
    target_user = d.get("target", {}).get("user") or ""
    source_host = d.get("source", {}).get("host") or ""
    target_host = d.get("target", {}).get("host") or ""

    # User-friendly defaults: derive hidden/advanced paths automatically.
    prom = d.get("prometheus", {})
    tsdb = (prom.get("source_tsdb_path") or "/var/lib/prometheus/metrics2").rstrip("/")
    prom["source_snapshot_dir"] = tsdb + "/snapshots"
    prom["source_url_from_source"] = f"http://127.0.0.1:{int(prom.get('source_temp_snapshot_port') or 19090)}"

    old_stage = prom.get("target_staging_root") or ""
    if (not old_stage) or old_stage.startswith("/home/iperf/"):
        prom["target_staging_root"] = f"{_home_for_user(target_user)}/prom_migration/staging"

    old_rec = d.get("record_dir_on_target") or ""
    if (not old_rec) or old_rec.startswith("/home/iperf/"):
        d["record_dir_on_target"] = f"{_home_for_user(target_user)}/prom_migration/records"

    src_g = d.get("grafana", {}).get("source_url_from_controller") or ""
    if (not src_g) or "192.168.10.107" in src_g or "localhost" in src_g or "127.0.0.1" in src_g:
        d["grafana"]["source_url_from_controller"] = _url_with_replaced_host(src_g, source_host, 3000)

    tgt_g = d.get("grafana", {}).get("target_url_from_controller") or ""
    if (not tgt_g) or "192.168.10.104" in tgt_g or "localhost" in tgt_g or "127.0.0.1" in tgt_g:
        d["grafana"]["target_url_from_controller"] = _url_with_replaced_host(tgt_g, target_host, 3000)

    return AppConfig.model_validate(d)


def cfg() -> AppConfig:
    return load_config()


def runner(role: str, c: Optional[AppConfig] = None) -> SSHRunner:
    c = c or cfg()
    if role == "source":
        return SSHRunner("source", c.source, c.ssh_strict_host_key_checking)
    if role == "target":
        return SSHRunner("target", c.target, c.ssh_strict_host_key_checking)
    raise ValueError(role)


def proof(c: Optional[AppConfig] = None) -> ProofService:
    c = c or cfg()
    return ProofService(c.record_dir_on_controller)


def plan_local_path(c: AppConfig, plan_id: str) -> Path:
    base = Path(c.record_dir_on_controller).expanduser().resolve() / "plans"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{plan_id}.json"


def save_plan_local(c: AppConfig, plan: Dict[str, Any]) -> None:
    path = plan_local_path(c, plan["planId"])
    path.write_text(json.dumps(plan, indent=2))


def load_plan_local(c: AppConfig, plan_id: str) -> Dict[str, Any]:
    path = plan_local_path(c, plan_id)
    if not path.exists():
        raise HTTPException(404, f"plan not found on controller: {plan_id}")
    return json.loads(path.read_text())


@app.get("/")
def root():
    return {"ok": True, "name": app.title, "version": app.version}


@app.get("/api/config")
def get_config():
    c = cfg()
    return {"config": c.model_dump(), "safeConfig": c.safe_dict(), "configPath": str(CONFIG_PATH)}


@app.post("/api/config")
def post_config(payload: ConfigPayload):
    fixed = normalize_config_for_generalized_use(payload.config)
    path = save_config(fixed)
    return {"ok": True, "path": path, "config": fixed.model_dump(), "safeConfig": fixed.safe_dict()}


@app.post("/api/ssh/test")
def test_ssh(payload: RolePayload):
    c = cfg()
    r = runner(payload.role, c)
    res = r.run("hostname && whoami && date -u +%Y-%m-%dT%H:%M:%SZ", timeout=30)
    return {"ok": res.ok, "result": res.model_dump()}


@app.post("/api/precheck")
def precheck():
    c = cfg()
    lines = []
    ok_all = True

    def add(name: str, ok: bool, detail: str):
        nonlocal ok_all
        ok_all = ok_all and ok
        lines.append(f"{'PASS' if ok else 'FAIL'} | {name}: {detail}")

    for role in ("source", "target"):
        r = runner(role, c)
        res = r.run("hostname && whoami && command -v python3 && command -v curl", timeout=30)
        add(f"{role} SSH + required tools", res.ok, (res.stdout or res.stderr).strip())

    src = runner("source", c)
    src_service = src.run("systemctl is-active " + q(c.prometheus.prometheus_service) + " || true", timeout=30)
    src_state = (src_service.stdout or src_service.stderr).strip()
    add("source normal Prometheus stopped", src_state != "active", "state=" + (src_state or "unknown") + "; required so no normal scraping starts")

    src_path_cmd = "test -d " + q(c.prometheus.source_tsdb_path) + " && echo tsdb_ok && find " + q(c.prometheus.source_tsdb_path) + " -mindepth 1 -maxdepth 1 -type d -name '01*' | wc -l"
    src_path = src.run_sudo(src_path_cmd, timeout=60)
    add("source TSDB path", src_path.ok, (src_path.stdout or src_path.stderr).strip())

    temp_port = int(c.prometheus.source_temp_snapshot_port or 19090)
    port_check = src.run("ss -lnt 2>/dev/null | awk '{print $4}' | grep -qE '(^|:)" + str(temp_port) + "$' && echo busy || echo free", timeout=30)
    add("source temp snapshot port", port_check.ok and "free" in (port_check.stdout or ""), (port_check.stdout or port_check.stderr).strip() or f"port {temp_port}")

    tgt = runner("target", c)
    tgt_ready = tgt.run("curl -fsS " + q(c.prometheus.target_url_from_target.rstrip("/") + "/-/ready"), timeout=30)
    add("target Prometheus ready", tgt_ready.ok, (tgt_ready.stdout or tgt_ready.stderr).strip())

    tgt_paths = tgt.run("test -d " + q(c.prometheus.target_data_path) + " && echo target_data_ok; mkdir -p " + q(c.prometheus.target_staging_root) + " " + q(c.record_dir_on_target) + " && echo target_dirs_ok", timeout=30)
    add("target data/staging paths", tgt_paths.ok, (tgt_paths.stdout or tgt_paths.stderr).strip())

    if c.grafana.enabled:
        try:
            sg = test_grafana(c, "source")
            add("source Grafana auth", bool(sg["ok"]), str(sg["status_code"]) + " " + sg["body"][:120])
        except Exception as e:
            add("source Grafana auth", False, str(e))
        try:
            tg = test_grafana(c, "target")
            add("target Grafana auth", bool(tg["ok"]), str(tg["status_code"]) + " " + tg["body"][:120])
        except Exception as e:
            add("target Grafana auth", False, str(e))
    else:
        add("Grafana annotations", True, "optional/skipped; enable only when migrating annotations")

    add("transfer method", True, "controller bridge: GUI reads snapshot from source and writes to target staging; no target-to-source SSH needed")

    proof_path = proof(c).write("precheck", {"safeConfig": c.safe_dict(), "lines": lines, "ok": ok_all})
    return {"ok": ok_all, "lines": lines, "proofFile": proof_path}


@app.get("/api/source/tsdb/scan")
def api_source_tsdb_scan():
    c = cfg()
    try:
        source_blocks = scan_remote_blocks(runner("source", c), c.prometheus.source_tsdb_path, use_sudo=False)
        return {"ok": True, "sourceTsdbPath": c.prometheus.source_tsdb_path, "blockCount": len(source_blocks), "sourceBlocks": source_blocks}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/source/tsdb/transfer")
def api_source_tsdb_transfer(payload: OfflineTsdbTransferPayload):
    c = cfg()
    src = runner("source", c)
    tgt = runner("target", c)
    mode = payload.transfer_mode or c.transfer_mode
    try:
        if mode == "target_pull_rsync":
            out = transfer_source_tsdb_blocks_target_pull(c, src, tgt, payload.label, payload.overwrite_staging, payload.start_utc, payload.end_utc, payload.selection)
        else:
            out = transfer_source_tsdb_blocks_controller_bridge(c, src, tgt, payload.label, payload.overwrite_staging, payload.start_utc, payload.end_utc, payload.selection)
        proof_file = proof(c).write("offline_tsdb_transfer", {"safeConfig": c.safe_dict(), "payload": payload.model_dump(), "result": out})
        out["proofFile"] = proof_file
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/snapshot/create")
def api_snapshot_create():
    c = cfg()
    try:
        out = create_source_snapshot(c, runner("source", c))
        proof_file = proof(c).write("snapshot_create", {"safeConfig": c.safe_dict(), "result": out})
        out["proofFile"] = proof_file
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/snapshot/list")
def api_snapshot_list():
    c = cfg()
    try:
        return {"snapshots": list_source_snapshots(c, runner("source", c))}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/snapshot/transfer")
def api_snapshot_transfer(payload: SnapshotTransferPayload):
    c = cfg()
    src = runner("source", c)
    tgt = runner("target", c)
    source_path = payload.snapshot_path or f"{c.prometheus.source_snapshot_dir.rstrip('/')}/{payload.snapshot_id}"
    mode = payload.transfer_mode or c.transfer_mode
    try:
        if mode == "target_pull_rsync":
            out = transfer_snapshot_target_pull(c, src, tgt, payload.snapshot_id, source_path, payload.overwrite_staging, payload.start_utc, payload.end_utc, payload.selection)
        else:
            out = transfer_snapshot_controller_bridge(c, src, tgt, payload.snapshot_id, source_path, payload.overwrite_staging, payload.start_utc, payload.end_utc, payload.selection)
        proof_file = proof(c).write("snapshot_transfer", {"safeConfig": c.safe_dict(), "snapshotId": payload.snapshot_id, "sourcePath": source_path, "requestedStartUtc": payload.start_utc, "requestedEndUtc": payload.end_utc, "selection": payload.selection, "result": out})
        out["proofFile"] = proof_file
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/blocks/scan")
def api_blocks_scan(snapshot_path_on_target: str = Query("")):
    c = cfg()
    try:
        target = runner("target", c)
        source_blocks = scan_remote_blocks(target, snapshot_path_on_target, use_sudo=False) if snapshot_path_on_target else []
        target_blocks = scan_remote_blocks(target, c.prometheus.target_data_path, use_sudo=True)
        target_overlap = check_remote_overlaps(target, c.prometheus.target_data_path, use_sudo=True)
        return {"sourceBlocks": source_blocks, "targetBlocks": target_blocks, "targetOverlapCheck": target_overlap}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/blocks/plan")
def api_blocks_plan(payload: BlockPlanPayload):
    c = cfg()
    try:
        p = build_plan(c, runner("target", c), payload.snapshot_path_on_target, payload.start_utc, payload.end_utc, payload.label, payload.mode, payload.selection)
        save_plan_local(c, p)
        proof_file = proof(c).write("block_plan", {"safeConfig": c.safe_dict(), "plan": p})
        p["controllerPlanFile"] = str(plan_local_path(c, p["planId"]))
        p["proofFile"] = proof_file
        return p
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/blocks/execute")
def api_blocks_execute(payload: ExecutePlanPayload):
    c = cfg()
    p = load_plan_local(c, payload.plan_id)
    try:
        out = execute_plan(c, runner("target", c), p, payload.confirmation)
        proof_file = proof(c).write("block_execute", {"safeConfig": c.safe_dict(), "planId": payload.plan_id, "result": out})
        out["proofFile"] = proof_file
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/blocks/overlap-check")
def api_overlap_check():
    c = cfg()
    try:
        return check_remote_overlaps(runner("target", c), c.prometheus.target_data_path, use_sudo=True)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/prometheus/verify")
def api_prom_verify():
    c = cfg()
    t = runner("target", c)
    healthy = t.run("curl -fsS " + q(c.prometheus.target_url_from_target.rstrip("/") + "/-/healthy"), timeout=30)
    ready = t.run("curl -fsS " + q(c.prometheus.target_url_from_target.rstrip("/") + "/-/ready"), timeout=30)
    status = t.run("systemctl is-active " + q(c.prometheus.prometheus_service), timeout=30)
    overlap = check_remote_overlaps(t, c.prometheus.target_data_path, use_sudo=True)
    ok = healthy.ok and ready.ok and status.ok and overlap.get("overlapCount") == 0
    return {"ok": ok, "healthy": healthy.model_dump(), "ready": ready.model_dump(), "systemd": status.model_dump(), "overlapCheck": overlap}


@app.post("/api/annotations/export")
def api_ann_export(payload: AnnotationRangePayload):
    c = cfg()
    if not c.grafana.enabled:
        raise HTTPException(400, "Grafana annotations are disabled in config")
    try:
        out = export_annotations(c, payload.from_utc, payload.to_utc)
        proof_file = proof(c).write("annotations_export", {"safeConfig": c.safe_dict(), "range": payload.model_dump(), "result": out})
        out["proofFile"] = proof_file
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/annotations/import")
def api_ann_import(payload: ImportAnnotationPayload):
    c = cfg()
    if not c.grafana.enabled:
        raise HTTPException(400, "Grafana annotations are disabled in config")
    try:
        out = import_annotations(c, payload.export_file, payload.import_tag)
        proof_file = proof(c).write("annotations_import", {"safeConfig": c.safe_dict(), "input": payload.model_dump(), "result": out})
        out["proofFile"] = proof_file
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/annotations/verify")
def api_ann_verify(payload: AnnotationRangePayload):
    c = cfg()
    if not c.grafana.enabled:
        raise HTTPException(400, "Grafana annotations are disabled in config")
    try:
        return verify_annotations(c, payload.from_utc, payload.to_utc, getattr(payload, 'import_tag', None))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/records/list")
def api_records_list():
    c = cfg()
    base = Path(c.record_dir_on_controller).expanduser().resolve()
    if not base.exists():
        return {"files": []}
    files = []
    for p in sorted(base.rglob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:200]:
        files.append({"path": str(p), "size": p.stat().st_size})
    return {"files": files}
