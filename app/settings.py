from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "settings.json"
DEFAULT_SETTINGS = {
    "paths": {
        "pdf_inbox_dir": r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles2",
        "models_dir": r"C:\git\OCR\OCR\models",
        "supplier_models_dir": r"C:\git\OCR\OCR\models\suppliers",
    }
}


def ensure_settings_file_exists() -> None:
    if SETTINGS_PATH.exists():
        return
    SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False), encoding="utf-8")


def load_settings() -> Dict[str, Any]:
    ensure_settings_file_exists()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return DEFAULT_SETTINGS
        merged = dict(DEFAULT_SETTINGS)
        merged_paths = dict(DEFAULT_SETTINGS.get("paths", {}))
        merged_paths.update(data.get("paths") or {})
        merged["paths"] = merged_paths
        return merged
    except Exception:
        return DEFAULT_SETTINGS


def get_path(settings: Dict[str, Any], key: str, fallback: str | None = None) -> str | None:
    try:
        value = (settings.get("paths") or {}).get(key)
        return str(value) if value else fallback
    except Exception:
        return fallback
