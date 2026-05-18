from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.migration_models import CommandResult, MigrationConfig


class ProofService:
    def __init__(self, storage_dir: str = "./storage"):
        self.storage_dir = Path(storage_dir)
        self.logs_dir = self.storage_dir / "logs"
        self.proofs_dir = self.storage_dir / "proofs"
        self.history_file = self.storage_dir / "migration_history.json"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.proofs_dir.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            self.history_file.write_text("[]")

    def new_migration_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"mig-{stamp}-{uuid.uuid4().hex[:6]}"

    def save_result(self, result: CommandResult, config: MigrationConfig, stage: str) -> None:
        log_path = self.logs_dir / f"{result.migration_id}-{stage}.log"
        log_path.write_text(result.output or "", encoding="utf-8")

        proof_path = self.proofs_dir / f"{result.migration_id}.json"
        existing: dict[str, Any] = {}
        if proof_path.exists():
            existing = json.loads(proof_path.read_text(encoding="utf-8"))

        existing.setdefault("migration_id", result.migration_id)
        existing.setdefault("created_at", result.started_at.isoformat())
        existing["config"] = config.safe_dict()
        existing.setdefault("stages", {})
        existing["stages"][stage] = {
            "ok": result.ok,
            "title": result.title,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
            "log_file": str(log_path),
            "proof": result.proof,
        }
        proof_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

        history = json.loads(self.history_file.read_text(encoding="utf-8"))
        history.append(
            {
                "migration_id": result.migration_id,
                "stage": stage,
                "ok": result.ok,
                "title": result.title,
                "created_at": result.finished_at.isoformat(),
            }
        )
        self.history_file.write_text(json.dumps(history[-200:], indent=2), encoding="utf-8")

    def read_proof(self, migration_id: str) -> dict[str, Any]:
        proof_path = self.proofs_dir / f"{migration_id}.json"
        if not proof_path.exists():
            return {"error": "Proof not found"}
        return json.loads(proof_path.read_text(encoding="utf-8"))

    def read_history(self) -> list[dict[str, Any]]:
        return json.loads(self.history_file.read_text(encoding="utf-8"))
