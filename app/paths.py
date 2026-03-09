from __future__ import annotations

import os

DEFAULT_PDF_FOLDER = os.environ.get("OCR_PDF_FOLDER", r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles2")
MODELS_DIR = os.environ.get("OCR_MODELS_DIR", r"C:\git\OCR\OCR\models")
SUPPLIERS_DIR = os.path.join(MODELS_DIR, "suppliers")
