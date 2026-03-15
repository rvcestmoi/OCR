from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import sys


def _base_dir() -> Path:
    # EXE : dossier de OCR.exe
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # DEV : racine projet (…/OCR)
    return Path(__file__).resolve().parents[1]

BASE_DIR = _base_dir()
SETTINGS_DIR = BASE_DIR / "settings"
SETTINGS_PATH = SETTINGS_DIR / "app_settings.json"

DEFAULT_SETTINGS = {
    "paths": {
        "pdf_inbox_dir": r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles2",
        "models_dir": r"C:\git\OCR\OCR\models",
        "supplier_models_dir": r"C:\git\OCR\OCR\models\suppliers",
        "dms_export_dir": r"C:\konverter\DMS",
        "csv_export_dir": r"C:\konverter\CSV",
        "tesseract_path": r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
        "poppler_path": r"C:\poppler\Library\bin",
    },
    "ocr": {
        "dpi": 150,
        "languages": "fra+eng+deu+spa+ita+nld",
    },
}


def ensure_settings_file_exists() -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    if SETTINGS_PATH.exists():
        return
    SETTINGS_PATH.write_text(
        json.dumps(DEFAULT_SETTINGS, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_settings() -> Dict[str, Any]:
    ensure_settings_file_exists()
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return DEFAULT_SETTINGS
        return _deep_merge_dict(DEFAULT_SETTINGS, raw)
    except Exception:
        return DEFAULT_SETTINGS


def get_path(settings: Dict[str, Any], key: str, fallback: str | None = None) -> str | None:
    try:
        value = (settings.get("paths") or {}).get(key)
        return str(value) if value else fallback
    except Exception:
        return fallback


def get_ocr_value(settings: Dict[str, Any], key: str, fallback=None):
    try:
        return (settings.get("ocr") or {}).get(key, fallback)
    except Exception:
        return fallback