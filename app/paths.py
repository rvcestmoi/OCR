from __future__ import annotations

import os

DEFAULT_PDF_FOLDER = os.environ.get("OCR_PDF_FOLDER", r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles2")
MODELS_DIR = os.environ.get("OCR_MODELS_DIR", r"C:\git\OCR\OCR\models")
SUPPLIERS_DIR = os.path.join(MODELS_DIR, "suppliers")
DMS_EXPORT_FOLDER = r"C:\konverter\DMS"
ALLOWED_DOC_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
OCR_ALLOWED_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
CSV_EXPORT_FOLDER = r"C:\konverter\CSV"
