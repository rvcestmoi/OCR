from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    # En EXE: dossier où se trouve OCR.exe
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # En dev: racine du projet (…/OCR)
    return Path(__file__).resolve().parents[1]


def _load_settings() -> dict:
    base = _base_dir()
    # On supporte plusieurs noms pour éviter les incohérences entre DEV / EXE
    candidates = [
        base / "settings" / "settings.json",
        base / "settings" / "app_settings.json",
        base / "settings" / "app_settings.local.json",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
    return {}


def _resolve_path(value: str | None) -> str:
    if not value:
        return ""
    value = os.path.expandvars(value).strip()

    # UNC paths (\\server\share\...) : Path.is_absolute() peut être capricieux selon l'OS
    if value.startswith("\\\\") or value.startswith("//"):
        return value

    p = Path(value)
    if p.is_absolute():
        return str(p)
    return str((_base_dir() / p).resolve())


_settings = _load_settings()
_paths = (_settings.get("paths") or {})

# Variables d'env = override optionnel
PDF_INBOX_DIR  = os.getenv("OCR_PDF_FOLDER", "") or _resolve_path(_paths.get("pdf_inbox_dir"))
MODELS_DIR = os.getenv("OCR_MODELS_DIR", "") or _resolve_path(_paths.get("models_dir", "models"))
SUPPLIER_MODELS_DIR  = os.getenv("OCR_SUPPLIERS_DIR", "") or _resolve_path(_paths.get("supplier_models_dir", "models/suppliers"))
SUPPLIERS_DIR = SUPPLIER_MODELS_DIR

DMS_EXPORT_DIR = _resolve_path(_paths.get("dms_export_dir"))
DMS_EXPORT_FOLDER = DMS_EXPORT_DIR
CSV_EXPORT_DIR = _resolve_path(_paths.get("csv_export_dir"))
TESSERACT_PATH = _resolve_path(_paths.get("tesseract_path"))
POPPLER_PATH = _resolve_path(_paths.get("poppler_path"))
# --- Extensions autorisées (utilisées par ui/mainwindow/common.py) ---
PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

# Docs qu'on accepte dans les listes / téléchargements
ALLOWED_DOC_EXTENSIONS = set(PDF_EXTENSIONS) | set(IMAGE_EXTENSIONS)

# Extensions “OCR” (si tu autorises OCR image + PDF)
OCR_ALLOWED_EXTENSIONS = set(ALLOWED_DOC_EXTENSIONS)

DEFAULT_PDF_FOLDER = PDF_INBOX_DIR