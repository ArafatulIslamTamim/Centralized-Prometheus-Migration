from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.services.time_utils import now_id


class ProofService:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).expanduser().resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def write(self, name: str, payload: Dict[str, Any]) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
        path = self.base / f"{now_id()}_{safe}.json"
        path.write_text(json.dumps(payload, indent=2, default=str))
        return str(path)

    def write_named(self, filename: str, payload: Dict[str, Any]) -> str:
        path = self.base / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str))
        return str(path)
