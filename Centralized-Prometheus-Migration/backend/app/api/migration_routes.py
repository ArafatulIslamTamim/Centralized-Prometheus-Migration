from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from app.models.migration_models import CommandResult, MigrationConfig
from app.services.migration_service import MigrationService
from app.services.proof_service import ProofService
from app.services.range_block_migration_service import RangeBlockMigrationService

router = APIRouter(prefix="/api/migration", tags=["migration"])

proof_service = ProofService()
service = MigrationService(proof_service)
range_service = RangeBlockMigrationService()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def parse_request_config(request: Request) -> MigrationConfig:
    """
    Accepts either a direct MigrationConfig JSON body or a wrapped body:
    {"config": {...}}. Empty body uses MigrationConfig defaults.
    """
    raw = await request.body()

    if not raw:
        return MigrationConfig()

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Request body is not valid JSON",
                "raw_body": raw.decode("utf-8", errors="replace"),
                "error": str(exc),
            },
        ) from exc

    if payload is None:
        return MigrationConfig()

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Request body must be a JSON object",
                "received_type": type(payload).__name__,
                "payload": payload,
            },
        )

    if "config" in payload and isinstance(payload["config"], dict):
        payload = payload["config"]

    try:
        return MigrationConfig.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Invalid migration configuration payload",
                "error": str(exc),
                "received_keys": list(payload.keys()),
                "payload_preview": payload,
            },
        ) from exc


def _format_range_output(data: dict[str, Any]) -> str:
    parts: list[str] = []

    message = data.get("message")
    if message:
        parts.append(str(message))

    stdout = data.get("stdout")
    if stdout:
        parts.append("\n=== STDOUT ===\n" + str(stdout).rstrip())

    stderr = data.get("stderr")
    if stderr:
        parts.append("\n=== STDERR ===\n" + str(stderr).rstrip())

    extra = {
        key: value
        for key, value in data.items()
        if key not in {"ok", "message", "stdout", "stderr"}
    }
    if extra:
        parts.append("\n=== RESULT JSON ===\n" + json.dumps(extra, indent=2, default=str))

    return "\n".join(parts).strip() or "No output returned."


def _range_result(
    config: MigrationConfig,
    stage: str,
    title: str,
    func: Callable[[MigrationConfig], dict[str, Any]],
) -> CommandResult:
    """
    Converts RangeBlockMigrationService dict output into the same CommandResult
    shape used by every other migration step. This fixes the frontend
    'No output returned' problem and saves range logs/proof JSON.
    """
    migration_id = config.migration_id or proof_service.new_migration_id()
    config.migration_id = migration_id
    started_at = _now()

    try:
        data = func(config)
        ok = bool(data.get("ok", True))
        output = _format_range_output(data)
        proof = {
            key: value
            for key, value in data.items()
            if key not in {"stdout", "stderr"}
        }
    except Exception as exc:
        ok = False
        output = f"{title} failed: {exc}"
        proof = {"error": str(exc)}

    result = CommandResult(
        ok=ok,
        title=title,
        output=output,
        started_at=started_at,
        finished_at=_now(),
        migration_id=migration_id,
        proof=proof,
    )
    proof_service.save_result(result, config, stage)
    return result


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/precheck", response_model=CommandResult)
async def precheck(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.precheck(config)


@router.post("/lm2-backup", response_model=CommandResult)
async def lm2_backup(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.create_lm2_backup(config)


@router.post("/lm1-create-snapshot", response_model=CommandResult)
async def lm1_create_snapshot(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.lm1_create_snapshot(config)


@router.post("/lm2-cleanup-old-source", response_model=CommandResult)
async def lm2_cleanup_old_source(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.lm2_cleanup_old_source(config)


@router.post("/lm1-transfer-snapshot", response_model=CommandResult)
async def lm1_transfer_snapshot(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.lm1_transfer_snapshot(config)


@router.post("/lm2-merge", response_model=CommandResult)
async def lm2_merge(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.lm2_merge(config)


@router.post("/validate", response_model=CommandResult)
async def validate(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.validate(config)


@router.post("/grafana-check", response_model=CommandResult)
async def grafana_check(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return service.grafana_check(config)


# =========================================================
# Exact custom-range migration routes
# Use these for 1h or another exact selected time window.
# Do not mix these with the full snapshot transfer/merge flow.
# =========================================================

@router.post("/lm1-range-manifest", response_model=CommandResult)
async def lm1_range_manifest(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return _range_result(
        config,
        "lm1_range_manifest",
        "Create Exact Range Manifest",
        range_service.create_lm1_range_manifest,
    )


@router.post("/transfer-range-blocks", response_model=CommandResult)
async def transfer_range_blocks(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return _range_result(
        config,
        "transfer_range_blocks",
        "Transfer Exact Range Blocks",
        range_service.transfer_lm1_range_blocks_to_lm2,
    )


@router.post("/merge-range-blocks", response_model=CommandResult)
async def merge_range_blocks(request: Request) -> CommandResult:
    config = await parse_request_config(request)
    return _range_result(
        config,
        "merge_range_blocks",
        "Merge Exact Range Blocks",
        range_service.merge_received_range_blocks_into_lm2,
    )


@router.get("/history")
def history() -> list[dict[str, Any]]:
    return proof_service.read_history()


@router.get("/proof/{migration_id}")
def proof(migration_id: str) -> dict[str, Any]:
    result = proof_service.read_proof(migration_id)
    if result.get("error"):
        raise HTTPException(status_code=404, detail="Proof file not found")
    return result
