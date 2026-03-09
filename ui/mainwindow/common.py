from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


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
from ocr.ocr_engine import extract_text_from_pdf
from ocr.document_classifier import classify_document_text
from ocr.supplier_model import (build_supplier_key, extract_best_bank_ids,
                                extract_fields_with_model, learn_supplier_patterns,
                                load_supplier_model, merge_patterns,
                                save_supplier_model)
from ui.block_options_dialog import BlockOptionsDialog
from ui.folder_select_dialog import FolderSelectDialog
from ui.geb_search_dialog import GebSearchDialog
from ui.pdf_viewer import PdfViewer

try:
    from shiboken6 import isValid
except Exception:
    def isValid(_obj):
        return _obj is not None

_URL_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)
AMOUNT_CANDIDATE_RE = re.compile(r"\b\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2,3})\b")
ONLY_AMOUNT_2DEC_RE = re.compile(r"^\s*\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\s*(?:€|EUR)?\s*$", re.IGNORECASE)
HAS_LETTERS_RE = re.compile(r"[A-Za-z]")
