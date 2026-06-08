from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class CommandResult(BaseModel):
    ok: bool
    role: str = ""
    host: str = ""
    command: str = ""
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = 0.0


class BlockMeta(BaseModel):
    id: str
    path: str
    minTime: int
    maxTime: int
    minIso: str
    maxIso: str
    sizeBytes: int = 0


class GenericResponse(BaseModel):
    ok: bool
    message: str = ""
    data: Dict[str, Any] = {}
    lines: List[str] = []
