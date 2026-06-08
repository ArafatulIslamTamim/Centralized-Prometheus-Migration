from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from app.models.config_models import AppConfig

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", ROOT / "config.json"))
EXAMPLE_CONFIG_PATH = ROOT / "config.example.json"


def load_config() -> AppConfig:
    if CONFIG_PATH.exists():
        return AppConfig.model_validate(json.loads(CONFIG_PATH.read_text()))
    if EXAMPLE_CONFIG_PATH.exists():
        return AppConfig.model_validate(json.loads(EXAMPLE_CONFIG_PATH.read_text()))
    return AppConfig()


def save_config(cfg: AppConfig) -> str:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg.model_dump(), indent=2))
    return str(CONFIG_PATH)
