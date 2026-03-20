from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import csv
from app.paths import CSV_EXPORT_DIR


from PySide6.QtCore import QObject, QThread, Qt, QTimer, Signal, Slot, QStringListModel
from PySide6.QtGui import (QBrush, QColor, QImage, QKeySequence, QPixmap,
                           QShortcut, QTextCharFormat, QTextCursor)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCompleter, QDialog,
                               QFileDialog, QFormLayout, QHeaderView, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressDialog,
                               QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)


from app.paths import DEFAULT_PDF_FOLDER, MODELS_DIR, SUPPLIERS_DIR
from ocr.invoice_parser import parse_invoice
from ocr.field_detector import detect_fields_multilingual
from ocr.ocr_engine import extract_text_from_pdf, extract_text_with_tesseract
from ocr.document_classifier import classify_document_text
from ocr.supplier_model import (build_supplier_key, extract_best_bank_ids,
                                extract_fields_with_model, learn_supplier_patterns,
                                load_supplier_model, merge_patterns,
                                save_supplier_model, validate_bic, validate_iban)
from ui.block_options_dialog import BlockOptionsDialog
from ui.folder_select_dialog import FolderSelectDialog
from ui.pdf_viewer import PdfViewer
from app.paths import DMS_EXPORT_FOLDER
import shutil

try:
    from shiboken6 import isValid
except Exception:
    def isValid(_obj):
        return _obj is not None

_URL_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)
AMOUNT_CANDIDATE_RE = re.compile(r"\b\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2,3})\b")
ONLY_AMOUNT_2DEC_RE = re.compile(r"^\s*\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\s*(?:€|EUR)?\s*$", re.IGNORECASE)
HAS_LETTERS_RE = re.compile(r"[A-Za-z]")
ENTRY_FILE_SEPARATOR = "__"
from app.paths import ALLOWED_DOC_EXTENSIONS, OCR_ALLOWED_EXTENSIONS, IMAGE_EXTENSIONS


def is_supported_document(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in ALLOWED_DOC_EXTENSIONS

def is_ocr_allowed_document(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in OCR_ALLOWED_EXTENSIONS

def is_image_document(path: str) -> bool:
    return os.path.splitext(str(path or ""))[1].lower() in IMAGE_EXTENSIONS

ENTRY_FILE_SEPARATOR = "__"


def strip_entry_prefix(filename: str) -> str:
    name = os.path.basename(str(filename or "").strip())
    if ENTRY_FILE_SEPARATOR not in name:
        return name

    left, right = name.split(ENTRY_FILE_SEPARATOR, 1)
    if left and right:
        return right

    return name

def build_storage_filename(entry_id: str, original_name: str) -> str:
    entry_id = str(entry_id or "").strip()
    original_name = os.path.basename(str(original_name or "").strip())

    if not original_name:
        return ""

    if not entry_id:
        return original_name

    prefix = f"{entry_id}{ENTRY_FILE_SEPARATOR}"
    if original_name.startswith(prefix):
        return original_name

    return f"{prefix}{original_name}"


def strip_entry_prefix(filename: str) -> str:
    name = os.path.basename(str(filename or "").strip())
    if ENTRY_FILE_SEPARATOR not in name:
        return name

    left, right = name.split(ENTRY_FILE_SEPARATOR, 1)
    if left and right:
        return right

    return name
