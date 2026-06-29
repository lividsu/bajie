from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class Config:
    def __init__(self, data_dir: str | Path = "data"):
        self.DATA_DIR = Path(data_dir)
        raw = self._load_yaml(self.DATA_DIR / "config.yaml")
        service = raw.get("service", raw)

        self.RUN_MODE = str(service.get("run_mode", "websocket")).strip().lower()
        self.TENANTS_CONFIG_DIR = str(service.get("tenants_dir", self.DATA_DIR / "tenants"))
        self.DEFAULT_TENANT_ID = str(service.get("default_tenant_id", "default"))
        self.HOST = str(service.get("host", "0.0.0.0"))
        self.PORT = int(service.get("port", 3002))
        self.DEBUG = bool(service.get("debug", True))

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        return data


config = Config()
