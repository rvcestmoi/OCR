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
try:
    from ocr.ocr_engine import extract_text_from_pdf, extract_text_with_tesseract
    OCR_ENGINE_AVAILABLE = True
except ImportError:
    # Fallback functions if OCR engine is not available
    def extract_text_from_pdf(pdf_path: str) -> str:
        return "OCR engine not available"
    
    def extract_text_with_tesseract(image_path: str) -> str:
        return "OCR engine not available"
    
    OCR_ENGINE_AVAILABLE = False
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


def format_left_table_filename(filename: str) -> str:
    """Nom à afficher uniquement dans la liste de gauche.

    Cas gérés :
    - ancien format : `<entry_id>__<nom>.pdf`  -> affiche `<nom>.pdf`
    - format actuel : `<nom>___<entry_id>.pdf` -> affiche `<nom>.pdf`

    Le nom réel n'est jamais modifié ; seul le texte affiché l'est.
    """
    name = os.path.basename(str(filename or "").strip())
    if not name:
        return ""

    # 1) ancien format : <entry_id>__<nom>.pdf
    name = strip_entry_prefix(name)

    # 2) format suffixé : <nom>___<entry_id>.pdf
    stem, ext = os.path.splitext(name)
    m = re.match(r"^(?P<base>.+?)___\d+$", stem)
    if m:
        stem = m.group("base").strip()

    return f"{stem}{ext}" if stem else name


def get_left_table_item_filename(item) -> str:
    """Retourne le vrai nom de fichier associé à une ligne de la table de gauche."""
    if item is None:
        return ""

    stored_name = str(item.data(Qt.UserRole + 6) or "").strip()
    if stored_name:
        return os.path.basename(stored_name)

    pdf_path = str(item.data(Qt.UserRole) or "").strip()
    if pdf_path:
        return os.path.basename(pdf_path)

    return str(item.text() or "").strip()


def left_table_filename_matches(item, filename: str) -> bool:
    """Compare un item de la table gauche avec un vrai nom de fichier ou son affichage."""
    target = os.path.basename(str(filename or "").strip())
    if not item or not target:
        return False

    displayed = str(item.text() or "").strip()
    stored = get_left_table_item_filename(item)
    target_display = format_left_table_filename(target)

    return target in {stored, displayed} or target_display in {stored, displayed}
