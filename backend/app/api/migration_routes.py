from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.models.migration_models import CommandResult, MigrationConfig
from app.services.migration_service import MigrationService
from app.services.proof_service import ProofService
from app.services.range_block_migration_service import RangeBlockMigrationService

router = APIRouter(prefix="/api/migration", tags=["migration"])

proof_service = ProofService()
service = MigrationService(proof_service)
range_service = RangeBlockMigrationService()


async def parse_request_config(request: Request) -> MigrationConfig:
    """
    Accepts:
    1. Direct config:
       { "source": {...}, "target": {...} }

    2. Wrapped config:
       { "config": { "source": {...}, "target": {...} } }

    3. Empty body:
       Uses default MigrationConfig.
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
# Custom date-range block migration routes
# These do NOT use full Prometheus snapshot.
# They select only TSDB blocks overlapping the GUI date range.
# =========================================================

@router.post("/lm1-range-manifest")
async def lm1_range_manifest(request: Request) -> dict[str, Any]:
    """
    Step 1:
    Create a manifest on LM1 containing only TSDB blocks that overlap
    lm1_data_start -> lm1_data_end.
    """
    config = await parse_request_config(request)

    try:
        return range_service.create_lm1_range_manifest(config)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to create LM1 custom-range block manifest",
                "error": str(exc),
            },
        ) from exc


@router.post("/transfer-range-blocks")
async def transfer_range_blocks(request: Request) -> dict[str, Any]:
    """
    Step 2:
    Transfer only selected LM1 blocks from LM1 to LM2 receive directory.
    Example target_receive_dir:
    /home/iperf/old_data_range
    """
    config = await parse_request_config(request)

    try:
        return range_service.transfer_lm1_range_blocks_to_lm2(config)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to transfer selected LM1 range blocks to LM2",
                "error": str(exc),
            },
        ) from exc


@router.post("/merge-range-blocks")
async def merge_range_blocks(request: Request) -> dict[str, Any]:
    """
    Step 3:
    Merge selected received blocks into the real LM2 Prometheus data path.

    Important:
    target_prom_path must be the actual active LM2 data path.
    In your case, use:
    /data

    Do not use:
    /var/lib/prometheus/metrics2
    unless Prometheus is actually reading that path.
    """
    config = await parse_request_config(request)

    try:
        return range_service.merge_received_range_blocks_into_lm2(config)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Failed to merge selected LM1 range blocks into LM2",
                "error": str(exc),
            },
        ) from exc


@router.get("/history")
def history() -> list[dict[str, Any]]:
    return service.history()


@router.get("/proof/{migration_id}")
def proof(migration_id: str) -> dict[str, Any]:
    result = service.get_proof(migration_id)
    if not result:
        raise HTTPException(status_code=404, detail="Proof file not found")
    return result