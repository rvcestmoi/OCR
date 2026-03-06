# ui/main_window.py
from __future__ import annotations

import os
import re
import json
import fitz  # PyMuPDF
import os, re, json
import fitz
class _DownloadCanceled(Exception):
        pass
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import URLError, HTTPError
from PySide6.QtWidgets import QMessageBox, QProgressDialog
from PySide6.QtCore import Qt
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import URLError, HTTPError
from PySide6.QtWidgets import QInputDialog

_URL_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)
_URL_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)


from PySide6.QtCore import Qt, QStringListModel, QTimer
from PySide6.QtGui import QImage, QPixmap, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton,
    QVBoxLayout, QHBoxLayout, QFileDialog, QLabel,
    QLineEdit, QFormLayout, QMessageBox, QPlainTextEdit,
    QTextEdit, QTableWidget, QTableWidgetItem, QProgressDialog,
    QApplication, QSplitter, QCompleter, QHeaderView
)
from webcolors import names

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ui.pdf_viewer import PdfViewer
from ocr.ocr_engine import extract_text_from_pdf
from ocr.invoice_parser import parse_invoice
from ocr.supplier_model import (
    build_supplier_key,
    load_supplier_model,
    save_supplier_model,
    learn_supplier_patterns,
    merge_patterns,
    extract_fields_with_model,
)
from PySide6.QtWidgets import QMenu
from PySide6.QtWidgets import QDialog
from ui.block_options_dialog import BlockOptionsDialog

from datetime import datetime
from ocr.supplier_model import extract_best_bank_ids
from collections import defaultdict

try:
    from shiboken6 import isValid
except Exception:
    def isValid(_obj):  # fallback
        return _obj is not None

from datetime import datetime
from PySide6.QtGui import QKeySequence
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QPlainTextEdit
from PySide6.QtWidgets import QButtonGroup
from PySide6.QtGui import QColor, QBrush

from db.geb_repository import GebRepository
from ui.geb_search_dialog import GebSearchDialog
from ui.folder_select_dialog import FolderSelectDialog



AMOUNT_CANDIDATE_RE = re.compile(r"\b\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2,3})\b")
ONLY_AMOUNT_2DEC_RE = re.compile(
    r"^\s*\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\s*(?:€|EUR)?\s*$",
    re.IGNORECASE
)
HAS_LETTERS_RE = re.compile(r"[A-Za-z]")


class MainWindow(QMainWindow):


    DOSSIER_PATTERN = re.compile(
        r"(?<!\d)(?:"
        r"1\d{8}"                    # 9 chiffres commençant par 1
        r"|150\d{5,8}"               # 150 + 5..8 chiffres  (ex: 15000003)
        r"|(?:845|255|355|445|645|675|695|725|785)\d{6,8}"  # autres : 3 + 6..8 chiffres
        r")(?!\d)"
    )
     # Dossier affiché au démarrage (liste des PDF)
    DEFAULT_PDF_FOLDER = r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles2"

    def __init__(self):
        super().__init__()
        from db.connection import SqlServerConnection
        from db.config import DB_CONFIG
        from db.logmail_repository import LogmailRepository
        from db.transporter_repository import TransporterRepository
        from db.bank_repository import BankRepository
        from db.tour_repository import TourRepository
        from ui.ocr_text_view import OcrTextView
        from db.tour_repository import TourRepository
        from db.geb_repository import GebRepository
   
        

        self.db_conn = SqlServerConnection(**DB_CONFIG)

        self.logmail_repo = LogmailRepository(self.db_conn)
        self.transporter_repo = TransporterRepository(self.db_conn)
        self.bank_repo = BankRepository(self.db_conn)
        self.tour_repo = TourRepository(self.db_conn)
        self.geb_repo = GebRepository(self.db_conn)

        # --- State ---
        self.current_pdf_path: str | None = None
        self.active_field: QLineEdit | None = None
        self.search_selections = []
        self.current_match_index = -1

        self.selected_kundennr: str | None = None
        self.current_db_iban: str | None = None
        self.current_db_bic: str | None = None
        self.bank_valid: bool | None = None
        self.selected_invoice_entry_id = None
        self.selected_invoice_filename = None
        self.transporter_selected_mode = False 
        # anti double-trigger (cellClicked + currentCellChanged)
        self._last_main_selected_path: str | None = None
        self._did_autoload_default_folder = False

        self._vat_theo_cache: dict[str, float | None] = {}
        self._pending_tags_to_add: set[str] = set()

        

        # PDF "affiché" (peut être la facture ou une PJ)
        self.view_pdf_path: str | None = None

        # Groupe de PDFs (même entry_id)
        self.entry_pdf_paths: list[str] = []
        self.current_doc_index: int = 0

        # --- Window ---
        self.setWindowTitle("OCR Factures Fournisseurs")
        self.resize(1200, 800)

        # =========================
        # Widget central + layout
        # =========================
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # =========================
        # Panneau gauche (splitter)
        # =========================
        # =========================
        # Panneau gauche (sans partie basse)
        # =========================
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        self.btn_scan_folder = QPushButton("📂 Analyser un dossier")
        self.btn_scan_folder.clicked.connect(self.select_folder)

        self.btn_ocr_all = QPushButton("⚙️ OCRiser")
        self.btn_ocr_all.clicked.connect(self.ocr_all_pdfs)

        left_layout.addWidget(self.btn_ocr_all)
        #left_layout.addWidget(self.btn_scan_folder)

        self.pdf_table = QTableWidget(left_widget)
        self.pdf_table.setObjectName("pdf_table")
        self.pdf_table.setColumnCount(3)
        self.pdf_table.setHorizontalHeaderLabels(["Nom du fichier", "IBAN", "BIC"])

        hdr = self.pdf_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)

        self.pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pdf_table.setAlternatingRowColors(True)
        self.pdf_table.cellClicked.connect(self.on_pdf_selected)

        self.pdf_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pdf_table.customContextMenuRequested.connect(self.on_pdf_table_context_menu)
        self.pdf_table.currentCellChanged.connect(self._on_pdf_current_cell_changed)

        # --- filtres (en haut du tableau gauche) ---
        self.left_filter_mode = "pending"

        filter_bar = QHBoxLayout()

        self.btn_filter_pending = QPushButton("🕓 En attente")
        self.btn_filter_pending.setCheckable(True)
        self.btn_filter_pending.setChecked(True)

        self.btn_filter_validated = QPushButton("✅ Validés")
        self.btn_filter_validated.setCheckable(True)

        self.btn_filter_errors = QPushButton("⚠️ Erreurs")
        self.btn_filter_errors.setCheckable(True)

        # exclusif
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_group.addButton(self.btn_filter_pending)
        self._filter_group.addButton(self.btn_filter_validated)
        self._filter_group.addButton(self.btn_filter_errors)

        filter_bar.addWidget(self.btn_filter_pending)
        filter_bar.addWidget(self.btn_filter_validated)
        filter_bar.addWidget(self.btn_filter_errors)
        filter_bar.addStretch(1)

        left_layout.addLayout(filter_bar)

        self.btn_filter_pending.clicked.connect(lambda: self.set_left_filter("pending"))
        self.btn_filter_validated.clicked.connect(lambda: self.set_left_filter("validated"))
        self.btn_filter_errors.clicked.connect(lambda: self.set_left_filter("errors"))


        left_layout.addWidget(self.pdf_table)

        main_layout.addWidget(left_widget, 2)        

        left_top_widget = QWidget()
        left_top_layout = QVBoxLayout(left_top_widget)

        self.btn_scan_folder = QPushButton("📂 Analyser un dossier")
        self.btn_scan_folder.clicked.connect(self.select_folder)

        self.btn_ocr_all = QPushButton("⚙️ OCRiser")
        self.btn_ocr_all.clicked.connect(self.ocr_all_pdfs)

        left_top_layout.addWidget(self.btn_ocr_all)
        ##left_top_layout.addWidget(self.btn_scan_folder)

        self.pdf_table = QTableWidget()
        self.pdf_table.setColumnCount(1)
        self.pdf_table.setHorizontalHeaderLabels(["Nom du fichier"])
        self.pdf_table.horizontalHeader().setStretchLastSection(True)
        self.pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pdf_table.setAlternatingRowColors(True)
        self.pdf_table.cellClicked.connect(self.on_pdf_selected)

        self.pdf_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pdf_table.customContextMenuRequested.connect(self.on_pdf_table_context_menu)
        self.pdf_table.currentCellChanged.connect(self._on_pdf_current_cell_changed)

        left_top_layout.addWidget(self.pdf_table)

        left_bottom_widget = QWidget()
        left_bottom_layout = QVBoxLayout(left_bottom_widget)
        left_bottom_layout.addWidget(QLabel("📎 Pièces jointes associées"))

        self.related_pdf_table = QTableWidget()
        self.related_pdf_table.setColumnCount(1)
        self.related_pdf_table.setHorizontalHeaderLabels(["Fichier lié"])
        self.related_pdf_table.horizontalHeader().setStretchLastSection(True)
        self.related_pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.related_pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.related_pdf_table.setAlternatingRowColors(True)
        self.related_pdf_table.cellClicked.connect(self.on_related_pdf_selected)
        self.related_pdf_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.related_pdf_table.customContextMenuRequested.connect(self.on_related_pdf_context_menu)

        left_bottom_layout.addWidget(self.related_pdf_table)



        # =========================
        # Panneau central (PDF)
        # =========================
        center_panel = QVBoxLayout()

        # --- Barre navigation PDF (docs + pages) ---
        pdf_nav = QHBoxLayout()

        # ✅ navigation documents (même entry_id)
        self.btn_prev_doc = QPushButton("⏪")
        self.btn_next_doc = QPushButton("⏩")
        self.lbl_doc_info = QLabel("Doc 0 / 0")

        self.btn_attach_cmr = QPushButton("Rattacher CMR…")
        self.btn_attach_cmr.setToolTip("Rattacher le document affiché à un dossier (liste de droite)")
        self.btn_attach_cmr.clicked.connect(self.on_attach_cmr_main)

        self.btn_fetch_links = QPushButton("🔗 Télécharger liens…")
        self.btn_fetch_links.setToolTip("Télécharger les documents pointés par des liens dans le PDF actuellement affiché")
        self.btn_fetch_links.clicked.connect(self.on_fetch_links_main)


        self.btn_prev_doc.setToolTip("Document précédent")
        self.btn_next_doc.setToolTip("Document suivant")

        self.btn_prev_doc.clicked.connect(self.on_prev_doc)
        self.btn_next_doc.clicked.connect(self.on_next_doc)

        # ✅ navigation pages (dans le PDF)
        self.btn_prev_page = QPushButton("⏮")
        self.btn_next_page = QPushButton("⏭")
        self.lbl_page_info = QLabel("Page 0 / 0")

        self.btn_prev_page.clicked.connect(self.on_prev_page)
        self.btn_next_page.clicked.connect(self.on_next_page)

        pdf_nav.addStretch()
        pdf_nav.addWidget(self.btn_prev_doc)
        pdf_nav.addWidget(self.lbl_doc_info)
        pdf_nav.addWidget(self.btn_next_doc)

        pdf_nav.addSpacing(8)
        pdf_nav.addWidget(self.btn_attach_cmr)

        pdf_nav.addWidget(self.btn_fetch_links)


        pdf_nav.addSpacing(16)

        pdf_nav.addWidget(self.btn_prev_page)
        pdf_nav.addWidget(self.lbl_page_info)
        pdf_nav.addWidget(self.btn_next_page)
        pdf_nav.addStretch()

        center_panel.addLayout(pdf_nav)

        # --- Viewer ---
        self.pdf_viewer = PdfViewer()
        self.pdf_viewer.setMinimumSize(400, 400)
        self.pdf_viewer.text_selected.connect(self.fill_active_field)
        self.pdf_viewer.text_selected.connect(self.append_ocr_text)
        center_panel.addWidget(self.pdf_viewer)

        # --- Clic droit sur la zone PDF ---
        target = getattr(self.pdf_viewer, "label", self.pdf_viewer)
        target.setContextMenuPolicy(Qt.CustomContextMenu)
        target.customContextMenuRequested.connect(self.on_pdf_context_menu)

        # Stockage en mémoire des détails palettes (chargé depuis le JSON)
        self.pallet_details = {}

        self.block_options = {}   # { "nom_fichier.pdf": {"blocked": bool, "comment": str} }


        # --- Volet info (transporteur/tour) ---
        self.transporter_info = QPlainTextEdit()
        self.transporter_info.setReadOnly(True)
        self.transporter_info.setMaximumHeight(120)
        self.transporter_info.setPlaceholderText("Informations transporteur (BDD)…")
        center_panel.addWidget(self.transporter_info)

        # =========================
        # Panneau droit (form)
        # =========================
        right_panel = QVBoxLayout()
        form_layout = QFormLayout()

        self.iban_input = QLineEdit()
        self.bic_input = QLineEdit()
        self.iban_input.editingFinished.connect(self.on_bank_fields_changed)
        self.bic_input.editingFinished.connect(self.on_bank_fields_changed)

        self.date_input = QLineEdit()
        self.invoice_number_input = QLineEdit()

        form_layout.addRow("IBAN :", self.iban_input)
        form_layout.addRow("BIC :", self.bic_input)

        # ----- Transporteur + completer -----
        self.transporter_input = QLineEdit()
        self.transporter_input.setPlaceholderText("Rechercher transporteur…")
        self.transporter_input.setClearButtonEnabled(True)

        self.transporter_model = QStringListModel()
        self.transporter_completer = QCompleter()
        self.transporter_completer.setModel(self.transporter_model)
        self.transporter_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.transporter_completer.setFilterMode(Qt.MatchContains)
        self.transporter_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.transporter_input.setCompleter(self.transporter_completer)

        self.transporter_input.textChanged.connect(self.search_transporters)
        self.transporter_completer.activated.connect(self.on_transporter_selected)

        self.btn_transporter_action = QPushButton("➡")
        self.btn_transporter_action.setFixedWidth(30)
        self.btn_transporter_action.clicked.connect(self.on_transporter_action)

        transporter_layout = QHBoxLayout()
        transporter_layout.addWidget(self.transporter_input)
        transporter_layout.addWidget(self.btn_transporter_action)
        transporter_layout.addStretch()
        form_layout.addRow("Transporteur :", transporter_layout)
        self.transporter_vat_input = QLineEdit()
        self.transporter_vat_input.setReadOnly(True)
        self.transporter_vat_input.setPlaceholderText("N° TVA (BDD)…")
        self.transporter_vat_input.setFocusPolicy(Qt.NoFocus)
        self.transporter_vat_input.setStyleSheet("background-color: #f3f3f3;")

        form_layout.addRow("N° TVA transporteur :", self.transporter_vat_input)

        form_layout.addRow("Date facture :", self.date_input)
        form_layout.addRow("N° facture :", self.invoice_number_input)

        # =========================
        # Table dossiers (N° dossier / Montant HT)
        # =========================
        self.folder_table = QTableWidget(0, 4)
        self.folder_table.setHorizontalHeaderLabels(["N° dossier", "Montant HT (OCR)", "TVA théorique (%)", "CMR"])
        self.folder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.folder_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.folder_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.folder_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)


        # Totaux dossiers
        self.lbl_folder_totals = QLabel("")
        self.lbl_folder_totals.setStyleSheet("padding:4px;")

        # =========================
        # TVA (table sous les dossiers)
        # =========================
        self.vat_table = QTableWidget(0, 3)
        self.vat_table.setHorizontalHeaderLabels(["Taux TVA (%)", "Base HT", "Montant TVA"])
        self.vat_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.vat_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.vat_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.vat_table.setAlternatingRowColors(True)
        self.vat_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.vat_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.vat_table.setMinimumHeight(50)
        self.vat_table.setMaximumHeight(100)

        self.lbl_vat_total = QLabel("")
        self.lbl_vat_total.setStyleSheet("padding:4px;")

        # =========================
        # Frais (table au-dessus de TVA)
        # =========================
        self.fees_table = QTableWidget(0, 3)
        self.fees_table.setHorizontalHeaderLabels(["GebNr", "Désignation", "Montant"])
        self.fees_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.fees_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.fees_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.fees_table.setAlternatingRowColors(True)
        self.fees_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.fees_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.fees_table.setMinimumHeight(50)
        self.fees_table.setMaximumHeight(50)

        self.btn_add_fee = QPushButton("➕ Ajouter un frais")
        self.btn_add_fee.clicked.connect(self.on_add_fee)

        self.btn_remove_fee = QPushButton("🗑 Supprimer")
        self.btn_remove_fee.clicked.connect(self.on_remove_fee)

        # =========================
        # Conteneur vertical (dossiers + totaux + TVA)
        # =========================
        folders_box = QWidget()
        self.folders_layout = QVBoxLayout(folders_box)
        self.folders_layout.setContentsMargins(0, 0, 0, 0)


        self.folders_layout.addWidget(QLabel("Frais :"))
        fees_bar = QHBoxLayout()
        fees_bar.addWidget(self.btn_add_fee)
        fees_bar.addWidget(self.btn_remove_fee)
        fees_bar.addStretch(1)
        self.folders_layout.addLayout(fees_bar)
        self.folders_layout.addWidget(self.fees_table)

        self.folders_layout.addWidget(self.folder_table)
        self.folders_layout.addWidget(self.lbl_folder_totals)

        self.folders_layout.addWidget(QLabel("TVA :"))
        self.folders_layout.addWidget(self.vat_table)
        self.folders_layout.addWidget(self.lbl_vat_total)

        form_layout.addRow("Dossiers :", folders_box)

        # Lignes vides permanentes
        self._ensure_empty_folder_row()
        self._ensure_empty_vat_row()

        # =========================
        # Gestion champ actif (PDF -> champ)
        # =========================
        self.FIELD_COLORS = {
            self.iban_input: QColor(100, 149, 237, 80),           # bleu
            self.bic_input: QColor(186, 85, 211, 80),             # violet
            self.date_input: QColor(60, 179, 113, 80),            # vert
            self.invoice_number_input: QColor(255, 215, 0, 80),   # jaune
        }

        # Boutons principaux
        self.btn_analyze_pdf = QPushButton("🔍 Analyser le PDF (OCR)")
        self.btn_analyze_pdf.clicked.connect(self.analyze_pdf)

        self.btn_save_data = QPushButton("💾 Sauvegarder")
        self.btn_save_data.clicked.connect(self.on_save_clicked)

        self.shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_save.setContext(Qt.ApplicationShortcut)
        self.shortcut_save.activated.connect(self.on_ctrl_s_save)

        self.btn_validate = QPushButton("✅ Valider la facture (V)")
        self.btn_validate.clicked.connect(self.on_validate_invoice)
        
 
        self.shortcut_validate = QShortcut(QKeySequence("V"), self)
        self.shortcut_validate.setContext(Qt.ApplicationShortcut)
        self.shortcut_validate.activated.connect(self.on_validate_invoice)

        right_panel.addWidget(self.btn_save_data)
        right_panel.addWidget(self.btn_validate)

        #self.btn_save_supplier = QPushButton("⭐ Mettre à jour modèle fournisseur")
        #self.btn_save_supplier.clicked.connect(self.save_supplier_model)
        #right_panel.addWidget(self.btn_save_supplier)

        right_panel.addLayout(form_layout)
        right_panel.addStretch()
        right_panel.addWidget(self.btn_analyze_pdf)

        # Layout global
        main_layout.addLayout(center_panel, 5)
        main_layout.addLayout(right_panel, 3)

        # Champs cliquables
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.mousePressEvent = lambda e, f=field: self.set_active_field(f)
            field.textChanged.connect(lambda _, f=field: f.setStyleSheet(""))
            self.transporter_input.mousePressEvent = lambda e, f=self.transporter_input: self.set_active_field(f)
            self.transporter_input.textChanged.connect(lambda _: self.transporter_input.setStyleSheet(""))

        # =========================
        # Recherche dans texte OCR
        # =========================
        self.ocr_search_input = QLineEdit()
        self.ocr_search_input.setPlaceholderText("🔍 Rechercher dans le texte OCR…")
        self.ocr_search_input.textChanged.connect(self.search_in_ocr_text)
        right_panel.addWidget(self.ocr_search_input)

        # =========================
        # Zone OCR brut
        # =========================
        self.ocr_text_view = OcrTextView()
        self.ocr_text_view.setReadOnly(True)
        self.ocr_text_view.setPlaceholderText("Texte brut OCR (Tesseract / PDF)…")
        self.ocr_text_view.setMinimumHeight(200)
        right_panel.addWidget(QLabel("🧾 Texte OCR brut :"))
        right_panel.addWidget(self.ocr_text_view)

        self.ocr_text_view.assign_to_field.connect(self.assign_text_to_field)

        # =========================
        # Navigation recherche OCR
        # =========================
        nav_layout = QHBoxLayout()
        self.btn_prev_match = QPushButton("⬅️")
        self.btn_next_match = QPushButton("➡️")
        self.search_counter_label = QLabel("0 / 0")
        self.btn_prev_match.clicked.connect(self.goto_previous_match)
        self.btn_next_match.clicked.connect(self.goto_next_match)
        nav_layout.addWidget(self.btn_prev_match)
        nav_layout.addWidget(self.btn_next_match)
        nav_layout.addWidget(self.search_counter_label)
        nav_layout.addStretch()
        right_panel.addLayout(nav_layout)

        # =========================
        # Reactive arrow when edit
        # =========================
        self.iban_input.textChanged.connect(self.enable_transporter_update)
        self.bic_input.textChanged.connect(self.enable_transporter_update)
        self.transporter_input.textChanged.connect(self.enable_transporter_update)

        # Optionnel (mais utile) : état initial boutons doc
        self.btn_prev_doc.setEnabled(False)
        self.btn_next_doc.setEnabled(False)



    # =========================
    # Active field / PDF selection
    # =========================
    def set_active_field(self, field):
        self.active_field = field

        self.pdf_viewer.active_field = field
        self.pdf_viewer.field_colors = self.FIELD_COLORS

        field.setStyleSheet("background-color: #fff3cd;")

        # ✅ Volet info selon champ actif
        # ✅ Volet info selon champ actif
        if field in (self.iban_input, self.bic_input):
            # IBAN/BIC -> toujours par banque
            self.transporter_selected_mode = False
            self.load_transporter_information(force_by_kundennr=False)
            return

        if field == self.transporter_input:
            # Transporteur -> si on a sélectionné un transporteur avant, on recharge par kundennr
            self.load_transporter_information(force_by_kundennr=self.transporter_selected_mode)
            return

        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
            if field == dossier_le or field == amount_le or field == vat_theo_le:
                self.load_tour_information(dossier_le.text())
                return





    def fill_active_field(self, text: str):
        if not self.active_field:
            return

        value = text.strip()

        if self.active_field == self.invoice_number_input:
            value = "".join(c for c in value if c.isdigit())
        elif self.active_field in self.get_folder_line_edits():
            # extraction dossier via pattern
            m = re.search(self.DOSSIER_PATTERN, value)
            value = m.group(0) if m else ""
        elif self.active_field == self.iban_input:
            value = value.replace(" ", "").upper()
        elif self.active_field == self.bic_input:
            value = value.replace(" ", "").upper()

        self.active_field.setText(value)
        self.active_field.setText(value)
        self.active_field.setStyleSheet("background-color: #e6ffe6;")

        if self.active_field in (self.iban_input, self.bic_input):
            QTimer.singleShot(0, self._refresh_transporter_after_bank_autofill)

        self.active_field.setStyleSheet("background-color: #e6ffe6;")

    # =========================
    # Folder fields helpers
    # =========================
    def get_folder_line_edits(self) -> list[QLineEdit]:
        out = []
        for r in range(self.folder_table.rowCount()):
            dossier_le, _ , vat_theo_le= self._get_row_widgets(r)
            if dossier_le:
                out.append(dossier_le)
        return out


    def clear_folder_fields(self, *args, **kwargs):
        self.folder_table.setRowCount(0)
        self._ensure_empty_folder_row()
        self.update_folder_totals()

    def on_folder_changed(self, line_edit: QLineEdit):
        # Si on est en train d’éditer ce champ dossier, on refresh le volet info tour
        if self.active_field == line_edit:
            self.load_tour_information(line_edit.text())


    # =========================
    # PDF list / display
    # =========================
    def select_folder(self):
        
        folder = QFileDialog.getExistingDirectory(self, "Sélectionner un dossier", self.DEFAULT_PDF_FOLDER)
        if not folder:
            return

        self.pdf_table.setRowCount(0)
        pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]
        for row, file in enumerate(pdf_files):
            self.pdf_table.insertRow(row)

            pdf_path = os.path.join(folder, file)

            # Col 0: nom fichier
            item0 = QTableWidgetItem(file)
            item0.setData(Qt.UserRole, pdf_path)
            self.pdf_table.setItem(row, 0, item0)

            # Col 1-2: IBAN / BIC depuis JSON si existe
            iban, bic = self._get_saved_iban_bic_for_pdf(pdf_path)
            self.pdf_table.setItem(row, 1, QTableWidgetItem(iban))
            self.pdf_table.setItem(row, 2, QTableWidgetItem(bic))

        self.current_pdf_path = None
        self.clear_fields()

    def on_pdf_selected(self, row, column):
        item = self.pdf_table.item(row, 0)
        if not item:
            return

        self.current_pdf_path = item.data(Qt.UserRole)
        invoice_filename = os.path.basename(self.current_pdf_path)

        invoice_filename = os.path.basename(self.current_pdf_path)
        self.selected_invoice_filename = invoice_filename

        # ✅ entry_id direct depuis la ligne (évite SQL)
        entry_id = item.data(Qt.UserRole + 4)
        self.selected_invoice_entry_id = entry_id if entry_id and not str(entry_id).startswith("__NO_ENTRY__") else None

        # ✅ liste complète des PDFs du groupe (évite SQL)
        group_paths = item.data(Qt.UserRole + 5)
        if isinstance(group_paths, list) and group_paths:
            # met le représentant en premier
            rep = self.current_pdf_path
            paths = [rep] + [p for p in group_paths if p != rep]
            self.entry_pdf_paths = paths
            self.current_doc_index = 0
            self.update_doc_indicator()
            self.show_doc_by_index(0)
        else:
            self.build_entry_pdf_group()
            self.show_doc_by_index(0)


        # facture = PDF cible
        self.view_pdf_path = self.current_pdf_path


        # ✅ Met à jour le panneau de droite : recharge JSON si existe, sinon OCR direct
        self.refresh_invoice_data()

        new_path = item.data(Qt.UserRole)
        if getattr(self, "_last_main_selected_path", None) == new_path:
            return
        self._last_main_selected_path = new_path
        self.current_pdf_path = new_path


    def display_pdf(self):
        pdf_path = self.view_pdf_path or self.current_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            return

        try:
            doc = fitz.open(pdf_path)
            pixmaps = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                pixmaps.append(QPixmap.fromImage(img))
            self.pdf_viewer.set_pages(pixmaps)
            doc.close()
        except Exception as e:
            QMessageBox.critical(self, "Erreur PDF", str(e))

    def refresh_invoice_data(self):
        """Recharge les données pour le PDF sélectionné.
        - Si un JSON existe : on recharge.
        - Sinon : on OCR automatiquement.
        """
        if not self.current_pdf_path:
            return

        # reset UI
        self.bank_valid = None
        self.selected_kundennr = None
        self.current_db_iban = None
        self.current_db_bic = None
        self.transporter_selected_mode = False

        # champs facture
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.blockSignals(True)
            field.clear()
            field.setStyleSheet("")
            field.blockSignals(False)

        # transporteur
        self.transporter_input.blockSignals(True)
        self.transporter_input.clear()
        self.transporter_input.blockSignals(False)
        self.btn_transporter_action.setEnabled(False)
        self.transporter_info.clear()

        # dossiers + TVA
        self.clear_folder_fields()
        self.vat_table.setRowCount(0)
        self._ensure_empty_vat_row()
        self.update_vat_total()

        self.fees_table.setRowCount(0)

        # OCR texte + recherche
        self.ocr_text_view.setPlainText("")
        self.search_selections = []
        self.current_match_index = -1
        self.search_counter_label.setText("0 / 0")

        # 1) Si un JSON existe -> on recharge et on NE FAIT PAS d'OCR (même si le load échoue)
        json_path = self._get_saved_json_path(self.current_pdf_path)
        if os.path.exists(json_path):
            ok = self.load_saved_data()
            if ok:
                self.check_bank_information()
                self.load_transporter_information()
                self.highlight_missing_fields()
            else:
                # pas d'OCR automatique : on évite d'écraser les champs
                self.statusBar().showMessage("Données sauvegardées trouvées mais chargement impossible (pas d'OCR auto).", 5000)
            return

        # 2) Sinon -> OCR direct (sans popup) ***** A decocher pour océrisation
        ##self.analyze_pdf(show_message=False)

    # =========================
    # OCR
    # =========================
    def analyze_pdf(self, checked: bool = False, show_message: bool = False):

        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        try:
            text = extract_text_from_pdf(self.current_pdf_path)
            self.ocr_text_view.setPlainText(text)

            data = parse_invoice(text)

            self.fill_fields(data)
            self.autofill_folder_amounts_from_ocr(text)
            self.update_folder_totals()
            self.check_bank_information()
            self.load_transporter_information()

            iban = self.iban_input.text().strip()
            bic = self.bic_input.text().strip()
            supplier_key = build_supplier_key(iban, bic)
            model = load_supplier_model(supplier_key)

            if model:
                self.apply_supplier_model(model)

            self.highlight_missing_fields()
            ocr_text = self.ocr_text_view.toPlainText() or ""
            best = extract_best_bank_ids(
                ocr_text,
                prefer_iban=self.iban_input.text().strip(),
                prefer_bic=self.bic_input.text().strip(),
            )

            if best["iban"] and not self.iban_input.text().strip():
                self.iban_input.setText(best["iban"])
            if best["bic"] and not self.bic_input.text().strip():
                self.bic_input.setText(best["bic"])

            supplier_key = build_supplier_key(
                self.iban_input.text().strip(),
                self.bic_input.text().strip(),
            )
            if supplier_key:
                model = load_supplier_model(supplier_key)
                if model:
                    self.apply_supplier_model(model)


            self.statusBar().showMessage("OCR terminé.", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Erreur OCR", str(e))
        if show_message:
            QMessageBox.information(...)

    def fill_fields(self, data):
        # Champs simples
        self.iban_input.setText(data.iban or "")
        self.bic_input.setText(data.bic or "")
        self.date_input.setText(data.invoice_date or "")
        self.invoice_number_input.setText(data.invoice_number or "")

        # dossiers
        self.folder_table.setRowCount(0)

        folder_numbers = getattr(data, "folder_numbers", None)
        if folder_numbers:
            for n in folder_numbers:
                if n:
                    self._add_folder_row(str(n), "")
        else:
            if getattr(data, "folder_number", None):
                self._add_folder_row(str(data.folder_number), "")
        # ligne vide permanente
        self._ensure_empty_folder_row()
        self.update_folder_totals()
        # --- TVA ---
        self.vat_table.setRowCount(0)

        vat_lines = getattr(data, "vat_lines", None) or []
        for r in vat_lines:
            self._add_vat_row(r.get("rate", ""), r.get("base", ""), r.get("vat", ""))

        self._ensure_empty_vat_row()
        self.update_vat_total()

        # Totaux / couleurs (si tu as ces fonctions)
        if hasattr(self, "update_folder_totals"):
            self.update_folder_totals()


    def highlight_missing_fields(self):
        fields = [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]
        for field in fields:
            if field in (self.iban_input, self.bic_input) and self.bank_valid is not None:
                continue
            field.setStyleSheet("background-color: #ffe6e6;" if not field.text().strip() else "background-color: #e6ffe6;")

        rows = self.get_folder_rows()
        has_any = any(r.get("tour_nr") for r in rows)

        for r in range(self.folder_table.rowCount()):
            dossier_le, _, vat_theo_le = self._get_row_widgets(r)
            if not dossier_le:
                continue
            if has_any:
                # vert seulement si rempli
                dossier_le.setStyleSheet("background-color: #e6ffe6;" if dossier_le.text().strip() else "")
            else:
                # si aucun dossier saisi, on met la première ligne en rouge
                dossier_le.setStyleSheet("background-color: #ffe6e6;" if r == 0 else "")


    def clear_fields(self):
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.clear()
            field.setStyleSheet("")
        self.clear_folder_fields()

    # =========================
    # OCR text view helpers
    # =========================
    def append_ocr_text(self, text: str):
        if not text.strip():
            return
        current = self.ocr_text_view.toPlainText()
        self.ocr_text_view.setPlainText(current + "\n\n--- OCR sélection ---\n" + text)

    def assign_text_to_field(self, text: str, field_key: str):
        text = text.strip()

        if field_key == "invoice_number":
            cleaned = re.sub(r"[^A-Z0-9\-_/\. ]", "", text.upper()).strip()
            self.invoice_number_input.setText(cleaned)
            self.invoice_number_input.setStyleSheet("background-color: #e6ffe6;")
            return

        if field_key == "folder_number":
            m = re.search(self.DOSSIER_PATTERN, text)
            dossier = m.group(0) if m else ""

            # remplir la première ligne dont la colonne dossier est vide (en évitant la ligne vide du bas si elle existe)
            for r in range(self.folder_table.rowCount()):
                dossier_le, _ , vat_theo_le= self._get_row_widgets(r)
                if dossier_le and not dossier_le.text().strip():
                    dossier_le.setText(dossier)
                    dossier_le.setStyleSheet("background-color: #e6ffe6;")
                    self._ensure_empty_folder_row()
                    return

            # sinon on force une nouvelle ligne (avant/avec la ligne vide)
            self._add_folder_row(dossier, "")
            self._ensure_empty_folder_row()
            return

        if field_key == "iban":
            self.iban_input.setText(text.replace(" ", "").upper())
            self.iban_input.setStyleSheet("background-color: #e6ffe6;")
            QTimer.singleShot(0, self._refresh_transporter_after_bank_autofill)
            return

        if field_key == "bic":
            self.bic_input.setText(text.replace(" ", "").upper())
            self.bic_input.setStyleSheet("background-color: #e6ffe6;")
            QTimer.singleShot(0, self._refresh_transporter_after_bank_autofill)
            return

        if field_key == "date":
            self.date_input.setText(text)
            self.date_input.setStyleSheet("background-color: #e6ffe6;")
            return

    # =========================
    # OCR search
    # =========================
    def search_in_ocr_text(self, query: str):
        editor = self.ocr_text_view
        self.search_selections = []
        self.current_match_index = -1
        editor.setExtraSelections([])

        if not query.strip():
            self.search_counter_label.setText("0 / 0")
            return

        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.Start)

        while True:
            cursor = editor.document().find(query, cursor)
            if cursor.isNull():
                break

            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor

            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#fff59d"))
            sel.format = fmt
            self.search_selections.append(sel)

        if not self.search_selections:
            self.search_counter_label.setText("0 / 0")
            return

        self.current_match_index = 0
        self._update_active_match()
        self.search_counter_label.setText(f"1 / {len(self.search_selections)}")

    def goto_next_match(self):
        if not self.search_selections:
            return
        self.current_match_index = (self.current_match_index + 1) % len(self.search_selections)
        self._update_active_match()

    def goto_previous_match(self):
        if not self.search_selections:
            return
        self.current_match_index = (self.current_match_index - 1) % len(self.search_selections)
        self._update_active_match()

    def _update_active_match(self):
        editor = self.ocr_text_view
        updated = []

        for i, sel in enumerate(self.search_selections):
            fmt = QTextCharFormat()
            if i == self.current_match_index:
                fmt.setBackground(QColor("#ffcc80"))
                editor.setTextCursor(sel.cursor)
            else:
                fmt.setBackground(QColor("#fff59d"))

            sel.format = fmt
            updated.append(sel)

        editor.setExtraSelections(updated)
        self.search_counter_label.setText(f"{self.current_match_index + 1} / {len(self.search_selections)}")

    # =========================
    # Save / Load JSON (multi dossiers)
    # =========================
    def save_current_data(self, status: str | None = None, show_message: bool = True):
        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return
        self.compact_folder_rows()
        json_path = self._get_saved_json_path(self.current_pdf_path)

        # --- load existing (to preserve extra keys like pallet_details / block_options / etc.) ---
        existing = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except Exception:
                existing = {}

        existing_status = (existing.get("status") or "").strip()
        existing_validated_at = (existing.get("validated_at") or "").strip()

        final_status = (status or existing_status or "draft").strip()
        validated_at = existing_validated_at
        if status == "validated" and not validated_at:
            validated_at = datetime.now().isoformat(timespec="seconds")

        # --- folders + transporter ---
        folders = self.get_folder_rows()

        trans_kundennr = (self.selected_kundennr or "").strip()
        # fallback si selected_kundennr n'est pas set mais que le champ contient "Nom (12345)"
        if not trans_kundennr:
            m = re.search(r"\((\d+)\)\s*$", self.transporter_input.text() or "")
            if m:
                trans_kundennr = m.group(1)

        vat_lines = self.get_vat_rows()

        base_total = 0.0
        vat_total = 0.0
        for ln in vat_lines:
            b = self._parse_amount((ln.get("base") or "").strip())
            v = self._parse_amount((ln.get("vat") or "").strip())
            if b is not None:
                base_total += b
            if v is not None:
                vat_total += v

        ttc_total = base_total + vat_total


        # --- build data payload ---
        data = {
            "iban": self.iban_input.text().strip(),
            "bic": self.bic_input.text().strip(),
            "invoice_date": self.date_input.text().strip(),
            "invoice_number": self.invoice_number_input.text().strip(),
            "folders": folders,
            "folder_number": folders[0]["tour_nr"] if folders else "",
            "fees": self.get_fee_rows(),  
            "vat_lines": vat_lines,
            "total_base_ht": round(base_total, 2),
            "total_vat": round(vat_total, 2),
            "total_ttc": round(ttc_total, 2),
            "ocr_text": self.ocr_text_view.toPlainText(),
            "transporter_kundennr": trans_kundennr,
            "status": final_status,
            "validated_at": validated_at,
        }
        # --- entry_id + récap CMR ---
        data["entry_id"] = (self.selected_invoice_entry_id or "").strip()
        data["cmr_attachments"] = self._collect_cmr_attachments_for_current_entry()
 # --- tags (ex: 'supprime') ---
        existing_tags = existing.get("tags") or []
        if isinstance(existing_tags, str):
            existing_tags = [existing_tags]
        if not isinstance(existing_tags, list):
            existing_tags = []

        tags_set = {str(t).strip() for t in existing_tags if str(t).strip()}
        pending = getattr(self, "_pending_tags_to_add", set()) or set()
        for t in pending:
            tt = str(t).strip()
            if tt:
                tags_set.add(tt)

        data["tags"] = sorted(tags_set)

        # clear pending tags
        try:
            self._pending_tags_to_add.clear()
        except Exception:
            pass

        os.makedirs(os.path.dirname(json_path), exist_ok=True)

        try:
            # merge: keep anything already stored
            existing.update(data)

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

            # update left table in real-time
            self._update_left_table_iban_bic(
                self.current_pdf_path,
                self.iban_input.text(),
                self.bic_input.text()
            )
            # rafraîchit la couleur de la ligne courante
            for row in range(self.pdf_table.rowCount()):
                it0 = self.pdf_table.item(row, 0)
                if it0 and it0.data(Qt.UserRole) == self.current_pdf_path:
                    self.refresh_left_row_processing_state(row)
                    break

            self.apply_left_filter_to_table()
            # ✅ mettre à jour le status stocké en table + re-filtrer
            for row in range(self.pdf_table.rowCount()):
                it0 = self.pdf_table.item(row, 0)
                if it0 and it0.data(Qt.UserRole) == self.current_pdf_path:
                    it0.setData(Qt.UserRole + 1, final_status)  # final_status existe dans save_current_data
                    break

            self.apply_left_filter_to_table()

            if show_message:
                QMessageBox.information(self, "Sauvegarde", "Données sauvegardées avec succès.")
            else:
                self.statusBar().showMessage("Données sauvegardées.", 2500)

        except Exception as e:
            QMessageBox.critical(self, "Erreur sauvegarde", str(e))

    def load_saved_data(self):
        if not self.current_pdf_path:
            return

        json_path = self._get_saved_json_path(self.current_pdf_path)
        if not os.path.exists(json_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # --- Transporteur (KundenNr sauvegardé) ---
            self.selected_kundennr = (data.get("transporter_kundennr") or "").strip() or None
            self.transporter_selected_mode = bool(self.selected_kundennr)

            # reset visuel (évite de garder un ancien transporteur affiché)
            self.transporter_input.blockSignals(True)
            self.transporter_input.setText("")
            self.transporter_input.blockSignals(False)

            # si on a un KundenNr -> on recharge depuis la BDD sans passer par IBAN/BIC
            if self.selected_kundennr:
                self.load_transporter_information(force_by_kundennr=True)
            else:
                # sinon on retombe sur la logique existante (IBAN/BIC)
                self.load_transporter_information(force_by_kundennr=False)
            self.pallet_details = data.get("pallet_details", {}) or {}
            self.block_options = data.get("block_options", {}) or {}
            self.iban_input.setText(data.get("iban", ""))
            self.bic_input.setText(data.get("bic", ""))
            self._update_left_table_iban_bic(
            self.current_pdf_path,
            self.iban_input.text(),
            self.bic_input.text()
            )
            self.date_input.setText(data.get("invoice_date", ""))
            self.invoice_number_input.setText(data.get("invoice_number", ""))
            self.vat_table.setRowCount(0)
            vat_lines = data.get("vat_lines", [])
            ocr_text = data.get("ocr_text", "")
            if isinstance(ocr_text, str):
                self.ocr_text_view.setPlainText(ocr_text)
            else:
                self.ocr_text_view.setPlainText("")
            if isinstance(vat_lines, list):
                for r in vat_lines:
                    self._add_vat_row(r.get("rate", ""), r.get("base", ""), r.get("vat", ""))

            self._ensure_empty_vat_row()
            self.update_vat_total()

            self.rebuild_fees_from_json(data)

            # ✅ dossiers -> table
            self.rebuild_folder_fields_from_json(data)

        except Exception as e:
            QMessageBox.warning(self, "Erreur chargement", str(e))
    

    def rebuild_folder_fields_from_json(self, data: dict):
        # reset table
        self.vat_table.setRowCount(0)
        vat_lines = data.get("vat_lines", [])
        if isinstance(vat_lines, list):
            for r in vat_lines:
                self._add_vat_row(r.get("rate", ""), r.get("base", ""), r.get("vat", ""))

        self._ensure_empty_vat_row()
        self.update_vat_total()

        self.folder_table.setRowCount(0)

        folders = data.get("folders")

        if isinstance(folders, list) and folders:
            for row in folders:
                tour_nr = "" if row is None else str(row.get("tour_nr", "") or "")
                amt = "" if row is None else str(row.get("amount_ht_ocr", "") or "")
                if tour_nr or amt:
                    self._add_folder_row(tour_nr, amt)
        else:
            # compat ancienne version
            one = str(data.get("folder_number", "") or "")
            if one:
                self._add_folder_row(one, "")

        self._ensure_empty_folder_row()
        self.update_folder_totals()

    def ocr_all_pdfs(self):
        # sécurité table
        if not hasattr(self, "pdf_table") or self.pdf_table is None or self.pdf_table.rowCount() == 0:
            QMessageBox.information(self, "OCR", "Aucun PDF à traiter.")
            return

        # sauvegarde l'état courant
        previous_pdf = self.current_pdf_path

        processed = 0
        skipped = 0
        errors = 0

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            pdf_path = it0.data(Qt.UserRole)
            if not pdf_path or not os.path.exists(pdf_path):
                continue

            # ✅ On OCRise uniquement les non-sauvegardés (pas de JSON)
            if self._has_saved_json_for_pdf(pdf_path):
                skipped += 1
                continue

            try:
                self.current_pdf_path = pdf_path

                # OCR (sans popup)
                self.analyze_pdf(show_message=False)

                # ✅ sauvegarde pour créer le JSON + mettre à jour IBAN/BIC dans la table
                self.save_current_data(status="draft", show_message=False)

                # status en table (pour filtres)
                self._set_left_row_status(pdf_path, "draft")

                processed += 1

            except Exception as e:
                errors += 1
                # (optionnel) marquer en erreur pour ton futur onglet “Erreurs”
                self._set_left_row_status(pdf_path, "error")
                # on continue sur les autres
                print(f"OCR error on {pdf_path}: {e}")

        # restore
        self.current_pdf_path = previous_pdf

        self.refresh_left_table_processing_states()
        self.apply_left_filter_to_table()

        # ré-applique tes filtres si tu les as
        if hasattr(self, "apply_left_filter_to_table"):
            self.apply_left_filter_to_table()

        QMessageBox.information(
            self,
            "OCR terminé",
            f"Traités : {processed}\nDéjà sauvegardés (skip) : {skipped}\nErreurs : {errors}"
        )

    def _save_data_for_pdf(self, pdf_path, data):
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        os.makedirs(model_dir, exist_ok=True)
        json_path = os.path.join(model_dir, f"{base_name}.json")

        folder_numbers = []
        if getattr(data, "folder_numbers", None):
            folder_numbers = data.folder_numbers or []
        elif getattr(data, "folder_number", None):
            folder_numbers = [data.folder_number] if data.folder_number else []

        payload = {
            "iban": data.iban or "",
            "bic": data.bic or "",
            "invoice_date": data.invoice_date or "",
            "invoice_number": data.invoice_number or "",
            "folder_numbers": folder_numbers,
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _model_exists_for_pdf(self, pdf_path):
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        json_path = os.path.join(model_dir, f"{base_name}.json")
        return os.path.exists(json_path)

    def save_supplier_model(self, checked: bool = False, show_message: bool = True) -> bool:
        ocr_text = self.ocr_text_view.toPlainText() or ""

        # 1) récupérer IBAN/BIC robustes depuis l’OCR (validation + scoring)
        best = extract_best_bank_ids(
            ocr_text,
            prefer_iban=self.iban_input.text().strip(),
            prefer_bic=self.bic_input.text().strip(),
        )

        iban = best.get("iban") or self.iban_input.text().strip()
        bic  = best.get("bic")  or self.bic_input.text().strip()

        # En mode "silencieux" (validation), on ne modifie pas les champs UI
        if show_message:
            if iban:
                self.iban_input.setText(iban)
            if bic:
                self.bic_input.setText(bic)

        supplier_key = build_supplier_key(iban, bic)
        if not supplier_key:
            msg = (
                "Impossible de sauvegarder le modèle : IBAN/BIC non fiables.\n"
                "Corrige IBAN/BIC puis réessaie."
            )
            if show_message:
                QMessageBox.warning(self, "Modèle transporteur", msg)
            else:
                self.statusBar().showMessage("Modèle transporteur non mis à jour (IBAN/BIC non fiables).", 4000)
            return False

        # 2) charger l’existant
        existing = load_supplier_model(supplier_key) or {}

        # 3) apprendre / merger les patterns
        new_patterns = learn_supplier_patterns(
            ocr_text,
            iban=iban,
            bic=bic,
            invoice_number=self.invoice_number_input.text().strip(),
            invoice_date=self.date_input.text().strip(),
        )
        merged = merge_patterns(existing.get("patterns") or {}, new_patterns)

        folders = self.get_folder_numbers()

        # 4) construire data
        data = dict(existing)
        data.update({
            "supplier_key": supplier_key,
            "iban": iban,
            "bic": bic,
            "invoice_number_example": self.invoice_number_input.text().strip(),
            "date_example": self.date_input.text().strip(),
            "folder_number_example": (folders[0] if folders else ""),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "patterns": merged,
            "model_version": 2,
        })

        # 5) sauver le fichier
        try:
            save_supplier_model(supplier_key, data)
            if show_message:
                QMessageBox.information(self, "Modèle transporteur", "Modèle transporteur sauvegardé / mis à jour.")
            else:
                self.statusBar().showMessage("Modèle transporteur mis à jour.", 3000)
            return True
        except Exception as e:
            if show_message:
                QMessageBox.critical(self, "Erreur modèle transporteur", str(e))
            else:
                self.statusBar().showMessage("Erreur MAJ modèle transporteur.", 4000)
            return False

    def apply_supplier_model(self, model: dict):
        if not model:
            return

        ocr_text = self.ocr_text_view.toPlainText() or ""
        found = extract_fields_with_model(ocr_text, model)

        # IBAN/BIC : valeur trouvée via patterns, sinon valeur stockée modèle
        if not self.iban_input.text().strip():
            self.iban_input.setText(found.get("iban") or model.get("iban", ""))

        if not self.bic_input.text().strip():
            self.bic_input.setText(found.get("bic") or model.get("bic", ""))

        cur = (self.invoice_number_input.text() or "").strip()
        is_ok = cur and any(c.isdigit() for c in cur) and cur.upper() not in {"DESCRIPTION", "DATE", "FACTURE", "INVOICE"}
        if not is_ok:
            self.invoice_number_input.setText(
                found.get("invoice_number") or model.get("invoice_number_example", "")
            )

        if not self.date_input.text().strip():
            self.date_input.setText(
                found.get("invoice_date") or model.get("date_example", "")
            )

        # dossier : on garde le comportement actuel (exemple)
        if not self.get_folder_numbers():
            example = model.get("folder_number_example", "")
            if example:
                dossier_le, _ , vat_theo_le= self._get_row_widgets(0)
                if dossier_le:
                    dossier_le.setText(example)
                    self._ensure_empty_folder_row()

    # =========================
    # PDF navigation
    # =========================
    def on_prev_page(self):
        self.pdf_viewer.previous_page()
        self.update_page_indicator()

    def on_next_page(self):
        self.pdf_viewer.next_page()
        self.update_page_indicator()

    def update_page_indicator(self):
        total = self.pdf_viewer.page_count()
        if total == 0:
            self.lbl_page_info.setText("0 / 0")
            return
        current = self.pdf_viewer.current_page_index() + 1
        self.lbl_page_info.setText(f"Page {current} / {total}")
        self.btn_prev_page.setEnabled(current > 1)
        self.btn_next_page.setEnabled(current < total)

    # =========================
    # Related PDFs (BDD)
    # =========================
    def load_related_pdfs(self):
        self.related_pdf_table.setRowCount(0)
        if not self.current_pdf_path:
            return

        current_dir = os.path.dirname(self.current_pdf_path)
        nom_pdf = os.path.basename(self.current_pdf_path)

        try:
            entry_id = self.logmail_repo.get_entry_id_for_file(nom_pdf)
            if not entry_id:
                return

            rows = self.logmail_repo.get_files_for_entry(entry_id)
            for row_idx, row in enumerate(rows):
                self.related_pdf_table.insertRow(row_idx)
                pdf_name = row["nom_pdf"]
                full_path = os.path.join(current_dir, pdf_name)
                item = QTableWidgetItem(pdf_name)
                item.setData(Qt.UserRole, full_path)
                self.related_pdf_table.setItem(row_idx, 0, item)

        except Exception as e:
            QMessageBox.warning(self, "BDD", f"Erreur lors du chargement des pièces jointes liées :\n{e}")

    def on_related_pdf_selected(self, row, column):
        item = self.related_pdf_table.item(row, 0)
        if not item:
            return

        path = item.data(Qt.UserRole)
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "PDF", "Fichier introuvable.")
            return

        # on affiche ce PDF, sans changer la facture cible
        self.view_pdf_path = path

        # si ce PDF est dans le groupe, on met à jour l’index
        if path in self.entry_pdf_paths:
            self.current_doc_index = self.entry_pdf_paths.index(path)

        self.display_pdf()
        self.update_page_indicator()
        self.update_doc_indicator()


    # =========================
    # Bank / transporter
    # =========================
    def check_bank_information(self):
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        self.bank_valid = None

        if not iban or not bic:
            return

        record = self.bank_repo.find_by_iban_bic(iban, bic)
        if record:
            self.bank_valid = True
            self.iban_input.setStyleSheet("background-color: #e6ffe6;")
            self.bic_input.setStyleSheet("background-color: #e6ffe6;")
        else:
            self.bank_valid = False
            self.iban_input.setStyleSheet("background-color: #fff3cd;")
            self.bic_input.setStyleSheet("background-color: #fff3cd;")

    def load_transporter_information(self, force_by_kundennr: bool = False):
        self.transporter_info.clear()
        self.transporter_vat_input.setText("")

        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        # si on est en mode transporteur sélectionné, on privilégie KundenNr
        if self.transporter_selected_mode and self.selected_kundennr:
            force_by_kundennr = True


        try:
            record = None

            # 1) si on force KundenNr (sélection transporteur) OU si IBAN/BIC absents mais KundenNr connu
            if force_by_kundennr or ((not iban or not bic) and self.selected_kundennr):
                record = self.transporter_repo.find_transporter_by_kundennr(self.selected_kundennr)

            # 2) sinon comportement existant : chercher par banque
            elif iban and bic:
                record = self.transporter_repo.find_transporter_by_bank(iban, bic)

            if not record:
                self.transporter_info.setPlainText("❌ Transporteur non trouvé en base.")
                self.transporter_vat_input.setText("")
                self._set_transporter_match_color(False)
                return

            kundennr = record.get("KundenNr") or record.get("kundennr") or ""

            # ✅ Charger le N° TVA (UstId) du transporteur
            try:
                vat_row = self.transporter_repo.get_ustid_by_kundennr(str(kundennr))
                ustid = ""
                if vat_row:
                    ustid = vat_row.get("UstId") or vat_row.get("ustid") or ""
                self.transporter_vat_input.setText(str(ustid).strip())
            except Exception:
                # on laisse vide si erreur SQL
                self.transporter_vat_input.setText("")
            name = record.get("name1", "")

            self.selected_kundennr = str(kundennr) if kundennr is not None else None

            # ⚠️ Important : ne pas déclencher search_transporters quand on set le texte
            self.transporter_input.blockSignals(True)
            self.transporter_input.setText(f"{name} ({kundennr})")
            self.transporter_input.blockSignals(False)

            self.current_db_iban = record.get("IBAN", "") or ""
            self.current_db_bic = record.get("SWIFT", "") or ""

            text = (
                f"🏦 Banque : {record.get('BankName', '')}\n"
                f"IBAN : {record.get('IBAN', '')}\n"
                f"SWIFT : {record.get('SWIFT', '')}\n\n"
                f"🚚 Transporteur : {record.get('name1', '')}\n"
                f"Adresse : {record.get('Strasse', '')}\n"
                f"Ville : {record.get('Ort', '')+record.get('PLZ', '')}\n"
                f"Pays : {record.get('LKZ', '')}"
            )
            self.transporter_info.setPlainText(text)

            self.enable_transporter_update()
            self.update_transporter_vs_dossiers_status()

        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement transporteur :\n{e}")
            self._set_transporter_match_color(False)


    def on_bank_fields_changed(self):
        self.check_bank_information()
        self.load_transporter_information()

    def search_transporters(self, text: str):
        # si déjà format "Name (123)" on ne relance pas de recherche
        if "(" in text and ")" in text:
            return
        if len(text.strip()) < 2:
            self.transporter_model.setStringList([])
            return

        try:
            rows = self.transporter_repo.search_transporters_by_name(text.strip())
            suggestions = [f"{r['name1']} ({r['kundennr']})" for r in rows]
            self.transporter_model.setStringList(suggestions)
        except Exception as e:
            print("Erreur recherche transporteur:", e)
            
    def on_transporter_selected(self, text: str):
        self.transporter_input.setText(text)

        if "(" in text and ")" in text:
            self.selected_kundennr = text.split("(")[-1].replace(")", "").strip()
        else:
            self.selected_kundennr = None

        # ✅ On passe en mode "transporteur choisi"
        self.transporter_selected_mode = bool(self.selected_kundennr)

        # ✅ Charger le transporteur par KundenNr (pas par IBAN/BIC)
        self.load_transporter_information(force_by_kundennr=True)

        self.enable_transporter_update()


    def on_transporter_action(self):
        if not self.selected_kundennr:
            return

        kundennr = self.selected_kundennr
        new_iban = self.iban_input.text().strip()
        new_bic = self.bic_input.text().strip()

        old_record = self.transporter_repo.get_bank_by_kundennr(kundennr)
        old_iban = old_record.get("IBAN", "") if old_record else ""
        old_bic = old_record.get("SWIFT", "") if old_record else ""

        msg = QMessageBox(self)
        msg.setWindowTitle("Mise à jour banque")
        msg.setText(
            "Voulez-vous mettre à jour les coordonnées bancaires ?\n\n"
            f"Ancien IBAN : {old_iban}\n"
            f"Ancien BIC  : {old_bic}\n\n"
            f"Nouveau IBAN : {new_iban}\n"
            f"Nouveau BIC  : {new_bic}"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() == QMessageBox.Yes:
            self.transporter_repo.update_bank(kundennr, new_iban, new_bic)

            # IMPORTANT: on ne relance PAS load_transporter_information() ici,
            # sinon ça risque de re-lire la BDD (pas encore commit/latence) et
            # de recalculer un état qui te “regrise”.
            self.current_db_iban = new_iban
            self.current_db_bic = new_bic

            QMessageBox.information(self, "Succès", "Coordonnées mises à jour.")

        self.enable_transporter_update()

    def enable_transporter_update(self):
        new_iban = self.iban_input.text().strip()
        new_bic = self.bic_input.text().strip()

        if not self.selected_kundennr:
            self.btn_transporter_action.setEnabled(False)
            return

        # Activer uniquement si modif réelle par rapport aux valeurs de référence
        base_iban = (self.current_db_iban or "").strip()
        base_bic = (self.current_db_bic or "").strip()

        if new_iban and new_bic and (new_iban != base_iban or new_bic != base_bic):
            self.btn_transporter_action.setEnabled(True)
        else:
            self.btn_transporter_action.setEnabled(False)


    def load_tour_information(self, tour_nr: str):
        self.last_loaded_tour_nr = (tour_nr or "").strip()
        self.transporter_info.clear()
        tour_nr = (tour_nr or "").strip()

        if not tour_nr:
            self.transporter_info.setPlainText("ℹ️ Aucun numéro de dossier.")
            return

        if not re.fullmatch(self.DOSSIER_PATTERN, tour_nr):
            self.transporter_info.setPlainText(f"❌ Numéro de dossier invalide : {tour_nr}")
            return

        try:
            record = self.tour_repo.find_by_tournr(tour_nr)
            if not record:
                self.transporter_info.setPlainText(f"❌ Tour non trouvée : {tour_nr}")
                return

            info = self.tour_repo.get_tour_extended_info(tour_nr) or {}

            invoice_tours = self._get_current_invoice_tours()
            cmr_tours = self._get_cmr_attached_tours_for_entry()

            missing = sorted(invoice_tours - cmr_tours) if invoice_tours else []
            all_ok = bool(invoice_tours) and not missing
            this_ok = tour_nr in cmr_tours

            global_icon = "✅" if all_ok else ("⚠️" if invoice_tours else "—")
            this_icon = "🧾✅" if this_ok else "🧾❌"

            header = f"🧾 Tour trouvée {global_icon}"
            if missing:
                header += f" | CMR manquantes: {', '.join(missing)}"

            txt = (
                f"{header}\n"
                f"TourNr : {info.get('TourNr', tour_nr)} {this_icon}\n"
                f"Départ : {info.get('Depart', '')}\n"
                f"Arrivée : {info.get('Arrivee', '')}\n"
                f"Date Tour : {info.get('DateTour', '')}\n"
                f"Date Livraison : {info.get('DateLivraison', '')}\n"
                f"Total Poids : {info.get('Total_Poids', '')}\n"
                f"Total MPL : {info.get('Total_MPL', '')}"
            )

            self.transporter_info.setPlainText(txt)

        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement tour :\n{e}")

    def on_related_pdf_context_menu(self, pos):

        invoice_row = self.pdf_table.currentRow()
        entry_id = None
        invoice_filename = None

        if invoice_row >= 0:
            it = self.pdf_table.item(invoice_row, 0)
            if it:
                invoice_filename = it.text().strip()
                entry_id = self.logmail_repo.get_entry_id_for_file(invoice_filename)

        action_associer.setEnabled(bool(entry_id))

        # (optionnel) garder en mémoire
        self.selected_invoice_filename = invoice_filename
        self.selected_invoice_entry_id = entry_id

        item = self.related_pdf_table.itemAt(pos)
        if not item:
            return

        linked_filename = item.text()

        menu = QMenu(self)

        action_associer = menu.addAction("Associer à la facture sélectionnée (liste du haut)")
        action_associer.setEnabled(bool(self.selected_invoice_entry_id))

        chosen = menu.exec(self.related_pdf_table.viewport().mapToGlobal(pos))
        if chosen != action_associer:
            return

        if not self.selected_invoice_entry_id or not self.selected_invoice_filename:
            QMessageBox.warning(self, "Association", "Aucune facture sélectionnée dans la liste du haut.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Associer une pièce jointe")
        msg.setText(
            f"Associer le fichier :\n\n"
            f"  {linked_filename}\n\n"
            f"à la facture :\n\n"
            f"  {self.selected_invoice_filename}\n\n"
            f"(entry_id = {self.selected_invoice_entry_id})"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() == QMessageBox.Yes:
            try:
                self.logmail_repo.update_entry_for_file(linked_filename, self.selected_invoice_entry_id)
                QMessageBox.information(self, "Association", "Fichier associé à la facture.")
                self.load_related_pdfs()  # refresh
            except Exception as e:
                QMessageBox.critical(self, "Erreur association", str(e))


    def _format_amount_2(self, v: float) -> str:
        return f"{v:.2f}"

    def _best_ht_amount_for_tour(self, lines: list[str], tour_nr: str) -> float | None:
        dossier_re = re.compile(r"\b\d{8}\b")
        def contains_tour(ln: str) -> bool:
            if tour_nr in ln:
                return True
            # fallback si OCR a mis des espaces / tirets dans le numéro
            compact = re.sub(r"[ \u00A0-]", "", ln)
            return tour_nr in compact

        idx = next((i for i, ln in enumerate(lines) if contains_tour(ln)), None)
        if idx is None:
            return None

        # ✅ fenêtre centrée sur la ligne du dossier (les montants sont souvent juste AVANT)
        start = max(0, idx - 12)
        end = min(len(lines), idx + 25)

        # stop si autre dossier apparaît (avant)
        for j in range(idx - 1, start - 1, -1):
            ln = lines[j]
            for d in dossier_re.findall(ln):
                if d != tour_nr:
                    start = j + 1
                    break
            else:
                continue
            break

        # stop si autre dossier apparaît (après)
        for j in range(idx + 1, end):
            ln = lines[j]
            for d in dossier_re.findall(ln):
                if d != tour_nr:
                    end = j
                    break
            else:
                continue
            break

        best = None  # (score, position, value)
        found_2dec = False

        def prev_nonempty(k: int) -> str:
            for x in range(k - 1, start - 1, -1):
                t = lines[x].strip()
                if t:
                    return t
            return ""

        for j in range(start, end):
            raw = lines[j].strip()
            if not raw:
                continue

            up = raw.upper()

            # ignorer unités parasites
            if "CO2" in up or "CO2E" in up or "KG" in up:
                continue

            # si lettres (hors € / EUR), ignorer
            if HAS_LETTERS_RE.search(raw) and ("€" not in raw and "EUR" not in up):
                continue

            strict_line = bool(ONLY_AMOUNT_2DEC_RE.match(raw))

            for s_amt in AMOUNT_CANDIDATE_RE.findall(raw):
                v = self._parse_amount(s_amt)
                if v is None or v <= 0:
                    continue

                # on évite les taux/quantités
                if v < 50:
                    continue

                mdec = re.search(r"[.,](\d+)$", s_amt)
                dlen = len(mdec.group(1)) if mdec else 0
                if dlen == 2:
                    found_2dec = True

                score = 0

                # ✅ priorité à la proximité de la ligne dossier
                dist = abs(j - idx)
                score += max(0, 25 - dist * 2)

                # décimales
                if dlen == 2:
                    score += 30
                elif dlen == 3:
                    score += 10
                else:
                    score -= 40

                # bonus si montant seul
                if strict_line:
                    score += 80
                    # bonus si la ligne précédente ressemble à une quantité (rare, mais utile)
                    prev = prev_nonempty(j)
                    if re.fullmatch(r"\d{1,3}", prev.strip()):
                        score += 25

                cand = (score, j, round(v, 2))
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
                    best = cand

        if not best:
            return None

        # si on a trouvé des montants en 2 décimales, on refuse les autres
        if found_2dec:
            best2 = None
            for j in range(start, end):
                raw = lines[j].strip()
                if not raw:
                    continue
                up = raw.upper()
                if HAS_LETTERS_RE.search(raw) and ("€" not in raw and "EUR" not in up):
                    continue
                strict_line = bool(ONLY_AMOUNT_2DEC_RE.match(raw))
                for s_amt in AMOUNT_CANDIDATE_RE.findall(raw):
                    v = self._parse_amount(s_amt)
                    if v is None or v < 50:
                        continue
                    mdec = re.search(r"[.,](\d+)$", s_amt)
                    dlen = len(mdec.group(1)) if mdec else 0
                    if dlen != 2:
                        continue

                    dist = abs(j - idx)
                    score = max(0, 25 - dist * 2) + 30
                    if strict_line:
                        score += 80
                    cand = (score, j, round(v, 2))
                    if best2 is None or cand[0] > best2[0] or (cand[0] == best2[0] and cand[1] > best2[1]):
                        best2 = cand
            if best2:
                return best2[2]

        return best[2]


    def autofill_folder_amounts_from_ocr(self, ocr_text: str):
        txt = ocr_text or ""
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if not lines:
            return

        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
            if not dossier_le or not amount_le:
                continue

            tour_nr = (dossier_le.text() or "").strip()
            if not tour_nr:
                continue

            # ne pas écraser si déjà rempli
            if (amount_le.text() or "").strip():
                continue

            best = self._best_ht_amount_for_tour(lines, tour_nr)
            if best is not None:
                amount_le.setText(self._format_amount_2(best))

    def _parse_amount(self, s: str):
        if not s:
            return None
        s = s.strip().replace(" ", "").replace("\u00A0", "")
        s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
   
    def update_folder_totals(self):
        rows = self.get_folder_rows()

        tour_nrs = [r["tour_nr"] for r in rows if r.get("tour_nr")]
        kosten_map = self.tour_repo.get_kosten_by_tournrs(tour_nrs) if tour_nrs else {}

        total_db = 0.0
        has_db = False
        for t in tour_nrs:
            v = kosten_map.get(t)
            if v is not None:
                total_db += float(v)
                has_db = True

        total_ocr = 0.0
        has_ocr = False
        for r in rows:
            a = self._parse_amount(r.get("amount_ht_ocr", ""))
            if a is not None:
                total_ocr += a
                has_ocr = True

        # Affichage : si au moins un dossier existe, on montre le total BDD même si introuvable
        if not rows:
            self.lbl_folder_totals.setText("")
            self.lbl_folder_totals.setStyleSheet("padding:4px;")
            return

        bdd_txt = f"{total_db:.2f}" if has_db else "N/A"
        ocr_txt = f"{total_ocr:.2f}" if has_ocr else "N/A"
        self.lbl_folder_totals.setText(f"Total OCR = {ocr_txt} | Total BDD = {bdd_txt}")

        if has_ocr and has_db and abs(total_ocr - total_db) <= 0.01:
            self.lbl_folder_totals.setStyleSheet("padding:4px; background-color:#e6ffe6;")
        else:
            self.lbl_folder_totals.setStyleSheet("padding:4px; background-color:#fff3cd;")

    def _make_folder_cell(self, placeholder: str):
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setClearButtonEnabled(True)
        return le

    def _get_row_widgets(self, row: int):
        dossier_le = self.folder_table.cellWidget(row, 0)
        amount_le = self.folder_table.cellWidget(row, 1)
        vat_theo_le = self.folder_table.cellWidget(row, 2)
        return dossier_le, amount_le, vat_theo_le 
    
    def _add_folder_row(self, dossier: str = "", amount: str = "", vat_theo: str = ""):
        row = self.folder_table.rowCount()
        self.folder_table.insertRow(row)

        dossier_le = self._make_folder_cell("Numéro de dossier")
        amount_le = self._make_folder_cell("Montant HT (OCR)")

        vat_theo_le = self._make_folder_cell("TVA théorique (%)")
        vat_theo_le.setReadOnly(True)
        vat_theo_le.setFocusPolicy(Qt.NoFocus)
        vat_theo_le.setStyleSheet("background-color: #f3f3f3;")

        cmr_lbl = QLabel("")
        cmr_lbl.setAlignment(Qt.AlignCenter)
        cmr_lbl.setToolTip("CMR OK ?")
        self.folder_table.setCellWidget(row, 3, cmr_lbl)

        dossier_le.setText("" if dossier is None else str(dossier))
        amount_le.setText("" if amount is None else str(amount))
        vat_theo_le.setText("" if vat_theo is None else str(vat_theo))

        dossier_le.mousePressEvent = lambda e, f=dossier_le: self.set_active_field(f)
        amount_le.mousePressEvent = lambda e, f=amount_le: self.set_active_field(f)

        dossier_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        amount_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        dossier_le.editingFinished.connect(self.compact_folder_rows)
        amount_le.editingFinished.connect(self.compact_folder_rows)

        self.folder_table.setCellWidget(row, 0, dossier_le)
        self.folder_table.setCellWidget(row, 1, amount_le)
        self.folder_table.setCellWidget(row, 2, vat_theo_le)

        self._update_folder_row_status(row)


    def _ensure_empty_folder_row(self):
        # si aucune ligne -> en créer une vide
        if self.folder_table.rowCount() == 0:
            self._add_folder_row("", "")
            return

        last = self.folder_table.rowCount() - 1
        dossier_le, amount_le, vat_theo_le = self._get_row_widgets(last)
        dossier_txt = (dossier_le.text() if dossier_le else "").strip()
        amount_txt = (amount_le.text() if amount_le else "").strip()

        # si la dernière ligne n'est plus vide -> ajouter une nouvelle ligne vide
        if dossier_txt or amount_txt:
            self._add_folder_row("", "")

    def _on_folder_row_changed(self, row: int):
        self._update_folder_row_status(row)
        self.update_folder_totals()
        self._ensure_empty_folder_row()

        # si le champ actif est le dossier de cette ligne, refresh le volet tour
        dossier_le, _, vat_theo_le = self._get_row_widgets(row)
        if self.active_field == dossier_le:
            self.load_tour_information(dossier_le.text())

    def get_folder_rows(self):
        rows = []
        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
            dossier = (dossier_le.text() if dossier_le else "").strip()
            amount = (amount_le.text() if amount_le else "").strip()
            # ignorer la ligne totalement vide (celle du bas)
            if dossier or amount:
                rows.append({"tour_nr": dossier, "amount_ht_ocr": amount})
        return rows
    
    def _update_folder_row_status(self, row: int):
        dossier_le, amount_le, vat_theo_le = self._get_row_widgets(row)
        if not dossier_le or not amount_le:
            return

        tour_nr = dossier_le.text().strip()

        cmr_lbl = self._get_row_cmr_widget(row)

        # CMR icon
        if cmr_lbl is not None:
            if not tour_nr:
                cmr_lbl.setText("")
                cmr_lbl.setToolTip("")
            else:
                ok, missing_by_tour = self._check_all_orders_have_cmr()
                required = self._get_required_orders_by_tour({tour_nr})
                attached = self._get_cmr_attached_orders_for_entry()

                req = required.get(tour_nr, set())
                att = attached.get(tour_nr, set())

                if not tour_nr:
                    cmr_lbl.setText("")
                    cmr_lbl.setToolTip("")
                elif not req:
                    cmr_lbl.setText("🧾❓")
                    cmr_lbl.setToolTip("Aucune commande (AufNr) trouvée en BDD pour ce dossier.")
                elif req.issubset(att):
                    cmr_lbl.setText("🧾✅")
                    cmr_lbl.setToolTip(f"Toutes les commandes ont une CMR ({len(req)}/{len(req)}).")
                elif len(att) > 0:
                    miss = sorted(req - att)
                    cmr_lbl.setText("🧾⚠️")
                    cmr_lbl.setToolTip(f"CMR partielle: {len(att)}/{len(req)}. Manque: {', '.join(miss[:10])}" + ("..." if len(miss) > 10 else ""))
                else:
                    cmr_lbl.setText("🧾❌")
                    cmr_lbl.setToolTip(f"Aucune CMR sur les commandes. Attendu: {len(req)} commande(s).")



        amount_ocr = self._parse_amount(amount_le.text())

        dossier_le.setStyleSheet("")
        amount_le.setStyleSheet("")
        amount_le.setToolTip("")

        # ligne vide => neutre
        if not tour_nr:
            vat_theo_le.setText("")
            vat_theo_le.setToolTip("")
            return
        
        # TVA théorique (BDD)
        try:
            if tour_nr in self._vat_theo_cache:
                vat_val = self._vat_theo_cache.get(tour_nr)
            else:
                vat_val = self.tour_repo.get_theoretical_vat_percent_by_tournr(tour_nr)
                self._vat_theo_cache[tour_nr] = vat_val

            if vat_val is not None:
                vat_theo_le.setText(self._format_percent(vat_val))
                vat_theo_le.setToolTip(f"TVA théorique BDD = {vat_val}")
            else:
                vat_theo_le.setText("")
                vat_theo_le.setToolTip("TVA théorique introuvable en BDD.")
        except Exception as e:
            vat_theo_le.setText("")
            vat_theo_le.setToolTip(f"Erreur BDD TVA: {e}")


        try:
            db_kosten = self.tour_repo.get_kosten_by_tournr(tour_nr)
        except Exception as e:
            amount_le.setStyleSheet("background-color: #ffe6e6;")
            amount_le.setToolTip(f"Erreur BDD: {e}")
            return

        if db_kosten is None:
            dossier_le.setStyleSheet("background-color: #ffe6e6;")
            amount_le.setStyleSheet("background-color: #ffe6e6;")
            amount_le.setToolTip("Tour non trouvée en base (xxatour).")
            return

        try:
            db_val = float(db_kosten)
        except Exception:
            db_val = None

        amount_le.setToolTip(f"Montant BDD (kosten) = {db_val}")

        if amount_ocr is None or db_val is None:
            amount_le.setStyleSheet("background-color: #fff3cd;")
            return

        if abs(amount_ocr - db_val) <= 0.01:
            amount_le.setStyleSheet("background-color: #e6ffe6;")
        else:
            amount_le.setStyleSheet("background-color: #fff3cd;")

    def get_folder_numbers(self) -> list[str]:
        return [r["tour_nr"] for r in self.get_folder_rows() if r.get("tour_nr")]
    

    def _make_vat_cell(self, placeholder: str):
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setClearButtonEnabled(True)
        return le

    def _get_vat_row_widgets(self, row: int):
        rate_le = self.vat_table.cellWidget(row, 0)
        base_le = self.vat_table.cellWidget(row, 1)
        vat_le  = self.vat_table.cellWidget(row, 2)
        return rate_le, base_le, vat_le

    def _add_vat_row(self, rate: str = "", base: str = "", vat: str = ""):
        row = self.vat_table.rowCount()
        self.vat_table.insertRow(row)

        rate_le = self._make_vat_cell("ex: 20")
        base_le = self._make_vat_cell("Base HT")
        vat_le  = self._make_vat_cell("Montant TVA")

        rate_le.setText("" if rate is None else str(rate))
        base_le.setText("" if base is None else str(base))
        vat_le.setText("" if vat is None else str(vat))

        # champ actif
        rate_le.mousePressEvent = lambda e, f=rate_le: self.set_active_field(f)
        base_le.mousePressEvent = lambda e, f=base_le: self.set_active_field(f)
        vat_le.mousePressEvent  = lambda e, f=vat_le: self.set_active_field(f)

        # changements => total + ligne vide
        rate_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))
        base_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))
        vat_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))

        self.vat_table.setCellWidget(row, 0, rate_le)
        self.vat_table.setCellWidget(row, 1, base_le)
        self.vat_table.setCellWidget(row, 2, vat_le)

    def _ensure_empty_vat_row(self):
        if self.vat_table.rowCount() == 0:
            self._add_vat_row("", "", "")
            return

        last = self.vat_table.rowCount() - 1
        rate_le, base_le, vat_le = self._get_vat_row_widgets(last)
        rate_txt = (rate_le.text() if rate_le else "").strip()
        base_txt = (base_le.text() if base_le else "").strip()
        vat_txt  = (vat_le.text() if vat_le else "").strip()

        if rate_txt or base_txt or vat_txt:
            self._add_vat_row("", "", "")

    def _on_vat_row_changed(self, row: int):
        self.update_vat_total()
        self._ensure_empty_vat_row()

    def get_vat_rows(self):
        rows = []
        for r in range(self.vat_table.rowCount()):
            rate_le, base_le, vat_le = self._get_vat_row_widgets(r)
            rate = (rate_le.text() if rate_le else "").strip()
            base = (base_le.text() if base_le else "").strip()
            vat  = (vat_le.text() if vat_le else "").strip()
            if rate or base or vat:
                rows.append({"rate": rate, "base": base, "vat": vat})
        return rows

    def update_vat_total(self):
        base_total = 0.0
        vat_total = 0.0
        has_any = False

        # ✅ dédoublonnage des lignes (rate, base, vat) pour éviter double comptage
        seen = set()  # (rate, base, vat) arrondis

        for r in range(self.vat_table.rowCount()):
            rate_le, base_le, vat_le = self._get_vat_row_widgets(r)

            rate_txt = (rate_le.text() if rate_le else "").strip()
            base_txt = (base_le.text() if base_le else "").strip()
            vat_txt  = (vat_le.text() if vat_le else "").strip()

            # ligne vide -> ignore
            if not rate_txt and not base_txt and not vat_txt:
                continue

            b = self._parse_amount(base_txt)
            v = self._parse_amount(vat_txt)
            rt = self._parse_amount(rate_txt)

            # si on n'a pas base+vat, on n'additionne pas (évite les lignes incomplètes)
            if b is None and v is None:
                continue

            # clé de déduplication si on a tout
            if rt is not None and b is not None and v is not None:
                key = (round(rt, 2), round(b, 2), round(v, 2))
                if key in seen:
                    continue
                seen.add(key)

            if b is not None:
                base_total += b
                has_any = True
            if v is not None:
                vat_total += v
                has_any = True

        if not has_any:
            self.lbl_vat_total.setText("")
            self.lbl_vat_total.setStyleSheet("padding:4px;")
            return

        ttc_total = base_total + vat_total

        self.lbl_vat_total.setText(
            f"Base HT = {base_total:.2f} | Total TVA = {vat_total:.2f} | Total TTC = {ttc_total:.2f}"
        )

        # vert (info) : total calculé
        self.lbl_vat_total.setStyleSheet("padding:4px; background-color:#e6ffe6;")


    def build_entry_pdf_group(self):
        """
        Construit self.entry_pdf_paths à partir du entry_id de la facture sélectionnée.
        La facture (current_pdf_path) est mise en premier.
        """

        self.entry_pdf_paths = []
        self.current_doc_index = 0

        if not self.selected_invoice_entry_id or not self.current_pdf_path:
            # groupe minimal = juste la facture
            if self.current_pdf_path:
                self.entry_pdf_paths = [self.current_pdf_path]
            self.update_doc_indicator()
            return

        current_dir = os.path.dirname(self.current_pdf_path)
        invoice_path = self.current_pdf_path

        try:
            rows = self.logmail_repo.get_files_for_entry(self.selected_invoice_entry_id) or []
        except Exception:
            rows = []

        paths = []
        for r in rows:
            name = r.get("nom_pdf") or r.get("Nom_PDF") or r.get("filename") or ""
            name = str(name).strip()
            if not name:
                continue
            if not name.lower().endswith(".pdf"):
                continue
            full_path = os.path.join(current_dir, name)
            if os.path.exists(full_path) and full_path not in paths:
                paths.append(full_path)

        # s’assurer que la facture est dans la liste + en premier
        if invoice_path in paths:
            paths.remove(invoice_path)
        paths.insert(0, invoice_path)

        self.entry_pdf_paths = paths
        self.update_doc_indicator()


    def show_doc_by_index(self, index: int):
        if not self.entry_pdf_paths:
            self.update_doc_indicator()
            return

        index = max(0, min(index, len(self.entry_pdf_paths) - 1))
        self.current_doc_index = index

        self.view_pdf_path = self.entry_pdf_paths[self.current_doc_index]
        self.display_pdf()
        self.update_page_indicator()
        self.update_doc_indicator()


    def update_doc_indicator(self):
        total = len(self.entry_pdf_paths)
        if total <= 0:
            self.lbl_doc_info.setText("Doc 0 / 0")
            self.btn_prev_doc.setEnabled(False)
            self.btn_next_doc.setEnabled(False)
            return

        self.lbl_doc_info.setText(f"Doc {self.current_doc_index + 1} / {total}")
        self.btn_prev_doc.setEnabled(self.current_doc_index > 0)
        self.btn_next_doc.setEnabled(self.current_doc_index < total - 1)


    def on_prev_doc(self):
        if not self.entry_pdf_paths:
            return
        self.show_doc_by_index(self.current_doc_index - 1)


    def on_next_doc(self):
        if not self.entry_pdf_paths:
            return
        self.show_doc_by_index(self.current_doc_index + 1)

    def on_pdf_context_menu(self, pos):
        menu = QMenu(self)

        act_pal = menu.addAction("Details palettes")
        tour_nrs = self.get_folder_numbers()
        act_pal.setEnabled(bool(tour_nrs))

        menu.addSeparator()

        act_block = menu.addAction("Options de blocage")
        act_block.setEnabled(bool(self.view_pdf_path or self.current_pdf_path))

        chosen = menu.exec(getattr(self.pdf_viewer, "label", self.pdf_viewer).mapToGlobal(pos))
        if chosen == act_pal:
            self.open_pallet_details_dialog()
        elif chosen == act_block:
            self.open_block_options_dialog()

    def open_pallet_details_dialog(self):
        from ui.pallet_details_dialog import PalletDetailsDialog
        tour_nrs = self.get_folder_numbers()
        if not tour_nrs:
            QMessageBox.information(self, "Palettes", "Aucun numéro de dossier renseigné.")
            return

        dlg = PalletDetailsDialog(
            self,
            tour_numbers=tour_nrs,
            tour_repo=self.tour_repo,
            existing_saved=getattr(self, "pallet_details", {}) or {},
        )

        if dlg.exec() != QDialog.Accepted:
            return

        result = dlg.get_result()
        self.pallet_details = result
        self._save_pallet_details_to_json(result)

        QMessageBox.information(self, "Palettes", "Détails palettes sauvegardés.")


    def _current_model_json_path(self) -> str | None:
        if not self.current_pdf_path:
            return None
        base_name = os.path.splitext(os.path.basename(self.current_pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        os.makedirs(model_dir, exist_ok=True)
        return os.path.join(model_dir, f"{base_name}.json")


    def _save_pallet_details_to_json(self, pallet_details: dict):
        json_path = self._current_model_json_path()
        if not json_path:
            return

        data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

        data["pallet_details"] = pallet_details

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


    def _read_model_json(self) -> tuple[str | None, dict]:
        json_path = self._current_model_json_path()
        if not json_path:
            return None, {}

        data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

        return json_path, data

    def _write_model_json(self, json_path: str, data: dict) -> None:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def open_block_options_dialog(self):
        # doc affiché (facture ou PJ)
        doc_path = self.view_pdf_path or self.current_pdf_path
        if not doc_path:
            return

        doc_name = os.path.basename(doc_path)

        json_path, data = self._read_model_json()
        if not json_path:
            return

        block_options = data.get("block_options", {}) or {}
        current = block_options.get(doc_name, {}) or {}

        dlg = BlockOptionsDialog(
            self,
            document_name=doc_name,
            blocked=bool(current.get("blocked", False)),
            comment=str(current.get("comment", "") or ""),
        )

        if dlg.exec() != QDialog.Accepted:
            return

        block_options[doc_name] = dlg.get_result()
        data["block_options"] = block_options
        self._write_model_json(json_path, data)

        # en mémoire aussi (utile si tu veux t’en servir ailleurs)
        self.block_options = block_options


    def on_pdf_table_context_menu(self, pos):
        """Clic-droit sur la liste du haut : rattacher un document à la facture sélectionnée."""

        item = self.pdf_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        it0 = self.pdf_table.item(row, 0)
        if not it0:
            return

        linked_filename = (it0.text() or "").strip()
        if not linked_filename:
            return

        menu = QMenu(self)

        pdf_path = it0.data(Qt.UserRole)  # ✅ définir AVANT de l'utiliser

        action_link = menu.addAction("Rattacher ce document à la facture sélectionnée")
        action_attach_cmr = menu.addAction("Rattacher CMR à un dossier…")
        action_attach_cmr.setEnabled(bool(pdf_path))  # ✅ maintenant OK

        menu.addSeparator()
        action_delete = menu.addAction("Supprimer")
        action_delete.setEnabled(bool(pdf_path))
        action_fetch_links = menu.addAction("Télécharger documents via liens (CMR)…")
        action_fetch_links.setEnabled(bool(pdf_path))
        # --- cible = ligne actuellement sélectionnée (la facture cible)
        target_row = self.pdf_table.currentRow()
        target_filename = None
        target_entry_id = None

        if target_row >= 0:
            it = self.pdf_table.item(target_row, 0)
            if it:
                target_filename = (it.text() or "").strip()
                target_entry_id = self.logmail_repo.get_entry_id_for_file(target_filename)

        # fallback: si mémorisé via clic gauche
        if not target_entry_id and self.selected_invoice_filename:
            target_filename = self.selected_invoice_filename
            target_entry_id = self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(target_filename)

        can_link = bool(target_entry_id and target_filename and linked_filename and linked_filename != target_filename)
        action_link.setEnabled(can_link)
        action_relink = menu.addAction("Rattacher à un Dossier (regrouper avec un autre fichier)…")
        action_relink.setEnabled(bool(pdf_path))

        chosen = menu.exec(self.pdf_table.viewport().mapToGlobal(pos))


        # ✅ IMPORTANT: gérer l'action CMR AVANT le "chosen != action_link"
        if chosen == action_attach_cmr:
            self.attach_cmr_to_dossier_from_right_list(pdf_path, linked_filename)
            return

        if chosen == action_delete:
            self.mark_pdf_as_deleted(pdf_path, linked_filename)
            return
        
        if chosen == action_relink:
            self.relink_left_document_to_other_group(row)
            return

        if chosen != action_link:
            return

        if chosen == action_fetch_links:
            self.fetch_linked_documents_from_pdf(pdf_path, linked_filename)
            return

        if not can_link:
            return

        # ... ici tu continues ton rattachement "document -> facture" existant (entry_id)
        # en utilisant target_filename/target_entry_id


        # cible = ligne actuellement sélectionnée (la facture cible)
        target_row = self.pdf_table.currentRow()
        target_filename = None
        target_entry_id = None

        if target_row >= 0:
            it = self.pdf_table.item(target_row, 0)
            if it:
                target_filename = (it.text() or "").strip()
                target_entry_id = self.logmail_repo.get_entry_id_for_file(target_filename)

        # fallback: si tu avais déjà mémorisé une cible via clic gauche
        if not target_entry_id and self.selected_invoice_filename:
            target_filename = self.selected_invoice_filename
            target_entry_id = self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(target_filename)

        can_link = bool(target_entry_id and target_filename and linked_filename and linked_filename != target_filename)
        action_link.setEnabled(can_link)

        # et pour la suite du code, utilise target_filename/target_entry_id au lieu de selected_invoice_*

        action_link.setEnabled(can_link)

        chosen = menu.exec(self.pdf_table.viewport().mapToGlobal(pos))

        if chosen == action_delete:
            self.mark_pdf_as_deleted(pdf_path, linked_filename)
            return

        if chosen == action_attach_cmr:
            self.attach_cmr_to_dossier_from_right_list(pdf_path, linked_filename)
            return
        

        if chosen != action_link:
            return
        


        if not can_link:
            QMessageBox.information(
                self,
                "Rattachement",
                "Sélectionne d'abord une facture (clic gauche) dans la liste du haut.",
            )
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Rattacher un document")
        msg.setText(
            f"Rattacher le fichier :\n\n"
            f"  {linked_filename}\n\n"
            f"à la facture :\n\n"
            f"  {self.selected_invoice_filename}\n\n"
            f"(entry_id = {self.selected_invoice_entry_id})"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() != QMessageBox.Yes:
            return

        try:
            self.logmail_repo.update_entry_for_file(linked_filename, self.selected_invoice_entry_id)
        except Exception as e:
            QMessageBox.critical(self, "Erreur rattachement", str(e))
            return

        # Refresh groupe + liste pièces associées
        current_view = self.view_pdf_path or self.current_pdf_path
        self.build_entry_pdf_group()
        if current_view and current_view in self.entry_pdf_paths:
            self.current_doc_index = self.entry_pdf_paths.index(current_view)
        self.update_doc_indicator()

        QMessageBox.information(self, "Rattachement", "Document rattaché à la facture.")

    def _on_pdf_current_cell_changed(self, currentRow, currentColumn, previousRow, previousColumn):
        if currentRow >= 0:
            self.on_pdf_selected(currentRow, currentColumn)

    def load_default_folder(self):
        """Charge automatiquement le dossier par défaut au démarrage."""
        folder = self.DEFAULT_PDF_FOLDER
        if folder and os.path.isdir(folder):
            self.load_folder(folder)
        else:
            QMessageBox.warning(
                self,
                "Dossier PDF introuvable",
                f"Le dossier PDF par défaut n'existe pas :\n{folder}\n\n"
                "Vous pouvez en choisir un autre via : 'Analyser un dossier'."
            )

    def load_folder(self, folder: str):
        """Remplit la liste de PDFs à partir d'un dossier (avec IBAN/BIC + status pour filtres)."""

        # --- Sécurité : si la table a été détruite, on évite le crash ---
        if not hasattr(self, "pdf_table") or self.pdf_table is None or not isValid(self.pdf_table):
            tbl = self.findChild(QTableWidget, "pdf_table")
            if tbl is not None and isValid(tbl):
                self.pdf_table = tbl
            else:
                return

        self.pdf_table.setRowCount(0)

        try:
            pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Impossible de lire le dossier :\n{folder}\n\n{e}")
            return

        self.pdf_table.setRowCount(0)

        try:
            pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Impossible de lire le dossier :\n{folder}\n\n{e}")
            return

        # ✅ mapping nom_pdf -> entry_id (en batch)
        try:
            entry_map = self.logmail_repo.get_entry_ids_for_files(pdf_files) or {}
        except Exception:
            entry_map = {}

        # ✅ group by entry_id
        groups: dict[str, list[str]] = {}
        for fn in pdf_files:
            entry_id = entry_map.get(fn)
            if not entry_id:
                entry_id = f"__NO_ENTRY__::{fn}"
            groups.setdefault(entry_id, []).append(fn)

        # (option debug OK ICI, car groups existe)
        print("NB groupes:", len(groups), {k: len(v) for k, v in groups.items()})

        # ✅ construire les lignes (1 ligne par entry_id)
        rows_to_add = []
        for entry_id, files in groups.items():
            group_paths = [os.path.join(folder, f) for f in files if os.path.exists(os.path.join(folder, f))]
            if not group_paths:
                continue

            rep_path = self._choose_representative_pdf(group_paths)
            if not rep_path:
                rep_path = group_paths[0]
            rep_filename = os.path.basename(rep_path)

            rows_to_add.append((rep_filename, rep_path, entry_id, group_paths))

        # tri stable (par nom du représentant)
        rows_to_add.sort(key=lambda x: x[0].lower())

        for row, (rep_filename, rep_path, entry_id, group_paths) in enumerate(rows_to_add):
            iban, bic = self._get_saved_iban_bic_for_pdf(rep_path)
            status = self._get_saved_status_for_pdf(rep_path)

            self.pdf_table.insertRow(row)

            extra = max(0, len(group_paths) - 1)
            display_name = rep_filename if extra == 0 else f"{rep_filename} (+{extra})"

            it0 = QTableWidgetItem(display_name)

            names = "\n".join([os.path.basename(p) for p in group_paths])
            it0.setToolTip(f"entry_id: {entry_id}\nDocuments: {len(group_paths)}\n\n{names}")
            it0.setData(Qt.UserRole, rep_path)           # chemin du PDF représentant
            it0.setData(Qt.UserRole + 1, status)         # status pour filtres
            it0.setData(Qt.UserRole + 4, entry_id)       # ✅ entry_id du groupe
            it0.setData(Qt.UserRole + 5, group_paths)    # ✅ liste complète des PDFs du groupe

           

            self.pdf_table.setItem(row, 0, it0)
            self.pdf_table.setItem(row, 1, QTableWidgetItem(iban))
            self.pdf_table.setItem(row, 2, QTableWidgetItem(bic))

        # filtres + refresh states
        self.apply_left_filter_to_table()
        self.refresh_left_table_processing_states()
        self.apply_left_filter_to_table()

        self.current_pdf_path = None
        self.clear_fields()


        # Appliquer le filtre courant (pending/validated/errors)
        if hasattr(self, "apply_left_filter_to_table"):
            self.apply_left_filter_to_table()
        self.refresh_left_table_processing_states()
        self.apply_left_filter_to_table()

        # reset panneau de droite
        self.current_pdf_path = None
        self.clear_fields()

    def _get_saved_json_path(self, pdf_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        return os.path.join(model_dir, f"{base_name}.json")
    
    def _get_saved_json_path_for_pdf(self, pdf_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        return os.path.join(model_dir, f"{base_name}.json")

    def _get_saved_iban_bic_for_pdf(self, pdf_path: str) -> tuple[str, str]:
        json_path = self._get_saved_json_path_for_pdf(pdf_path)
        if not os.path.exists(json_path):
            return ("", "")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return (str(data.get("iban", "")).strip(), str(data.get("bic", "")).strip())
        except Exception:
            return ("", "")
        
    def _update_left_table_iban_bic(self, pdf_path: str, iban: str, bic: str):
        """Met à jour en temps réel les colonnes IBAN/BIC du tableau de gauche pour un PDF."""
        if not pdf_path:
            return
        if not hasattr(self, "pdf_table"):
            return
        if self.pdf_table.columnCount() < 3:
            return

        iban = (iban or "").strip()
        bic = (bic or "").strip()

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue
            p = it0.data(Qt.UserRole)
            if p == pdf_path:
                # col 1 = IBAN, col 2 = BIC
                self.pdf_table.setItem(row, 1, QTableWidgetItem(iban))
                self.pdf_table.setItem(row, 2, QTableWidgetItem(bic))
                return
            
    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_autoload_default_folder:
            self._did_autoload_default_folder = True
            self.load_default_folder()


    def refresh_left_table_saved_infos(self):
        """Recharge IBAN/BIC pour chaque PDF de la table."""
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        if self.pdf_table.columnCount() < 3:
            return

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            pdf_path = it0.data(Qt.UserRole)
            if not pdf_path:
                continue

            iban, bic = self._get_saved_iban_bic_for_pdf(pdf_path)

            it1 = self.pdf_table.item(row, 1)
            if it1 is None:
                self.pdf_table.setItem(row, 1, QTableWidgetItem(iban))
            else:
                it1.setText(iban)

            it2 = self.pdf_table.item(row, 2)
            if it2 is None:
                self.pdf_table.setItem(row, 2, QTableWidgetItem(bic))
            else:
                it2.setText(bic)

    def _is_typing_in_input(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))
    
    def on_validate_invoice(self):
        if self._is_typing_in_input():
            return
        if not self.current_pdf_path:
            self.statusBar().showMessage("Aucun PDF sélectionné.", 3000)
            return
        if not self._block_validate_if_missing_cmr():
            return

        resp = QMessageBox.question(
            self,
            "Validation facture",
            "Valider la facture ?\n",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return

        # 1) Toujours re-sauvegarder AVANT les updates SQL
        self.save_current_data(status="validated", show_message=False)
    
        # ✅ Met à jour le modèle transporteur (patterns) à la validation
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            # ne bloque pas la validation si le modèle échoue
            pass

        # 2) Déterminer la valeur à appliquer selon blocage
        doc_name = os.path.basename(self.current_pdf_path)
        blocked = bool((self.block_options.get(doc_name, {}) or {}).get("blocked", False))
        value = 601 if blocked else 600
        comment = str((self.block_options.get(doc_name, {}) or {}).get("comment", "") or "").strip()

        # 3) Dossiers (TourNr) -> updates SQL
        tournrs = sorted({
            (r.get("tour_nr") or "").strip()
            for r in self.get_folder_rows()
            if (r.get("tour_nr") or "").strip()
        })

        if not tournrs:
            QMessageBox.warning(self, "Validation", "Aucun dossier (TourNr) trouvé : pas de mise à jour SQL.")
            return

        errors = []
        for t in tournrs:
            try:
                self.tour_repo.set_infosymbol18_for_tournr(t, value=value)
                self.tour_repo.set_block_status_for_tournr(t, is_blocked=blocked, motif=comment)
            except Exception as e:
                errors.append(f"{t} : {e}")

        if errors:
            QMessageBox.warning(
                self,
                "Validation",
                "Facture VALIDÉE et sauvegardée.\n\nErreurs SQL:\n" + "\n".join(errors)
            )
        else:
            suffix = " (document BLOQUÉ)" if blocked else ""
            QMessageBox.information(
                self,
                "Validation",
                f"Facture VALIDÉE et sauvegardée. Dossier(s){suffix}."
            )

    def _get_saved_status_for_pdf(self, pdf_path: str) -> str:
        json_path = self._get_saved_json_path(pdf_path)
        if not os.path.exists(json_path):
            return "draft"
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return (data.get("status") or "draft").strip()
        except Exception:
            return "draft"
        
    def set_left_filter(self, mode: str):
        self.left_filter_mode = mode
        self.apply_left_filter_to_table()

    def apply_left_filter_to_table(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            deleted = bool(it0.data(Qt.UserRole + 3))
            if deleted:
                visible = (self.left_filter_mode == "errors")
                self.pdf_table.setRowHidden(row, not visible)
                continue           

            status = (it0.data(Qt.UserRole + 1) or "draft").strip()

            if self.left_filter_mode == "pending":
                visible = (status != "validated")
            elif self.left_filter_mode == "validated":
                visible = (status == "validated")
            elif self.left_filter_mode == "errors":
                state = (it0.data(Qt.UserRole + 2) or "").strip()
                visible = (state == "error")

            self.pdf_table.setRowHidden(row, not visible)

    def _has_saved_json_for_pdf(self, pdf_path: str) -> bool:
        if not pdf_path:
            return False
        return os.path.exists(self._get_saved_json_path(pdf_path))

    def _set_left_row_status(self, pdf_path: str, status: str):
        """Stocke le status dans la colonne 0 (UserRole+1) pour tes filtres."""
        if not pdf_path or not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                it0.setData(Qt.UserRole + 1, (status or "draft").strip())
                break


    def _set_transporter_match_color(self, ok: bool | None):
        """
        ok=True  => vert
        ok=False => rouge
        ok=None  => neutre
        """
        if ok is None:
            self.transporter_info.setStyleSheet("")
            # si tu veux aussi colorer le champ TVA transporteur :
            self.transporter_vat_input.setStyleSheet("background-color: #f3f3f3;")
            return

        if ok:
            bg = "#d4edda"  # vert clair
            border = "#28a745"
        else:
            bg = "#f8d7da"  # rouge clair
            border = "#dc3545"

        self.transporter_info.setStyleSheet(f"background-color: {bg}; border: 2px solid {border};")
        self.transporter_vat_input.setStyleSheet(f"background-color: {bg}; border: 2px solid {border};")

    def update_transporter_vs_dossiers_status(self):
        """
        Règle demandée :
        - si IBAN/BIC => transporteur trouvé en base
        - et si TOUS les dossiers sont trouvés via :
            SELECT tournr FROM xxatour WHERE tournr IN (...)
        => VERT
        sinon => ROUGE
        """
        dossiers = sorted({d.strip() for d in self.get_folder_numbers() if d and d.strip()})
        if not dossiers:
            self._set_transporter_match_color(None)
            return

        # transporteur non trouvé
        if not self.selected_kundennr:
            self._set_transporter_match_color(False)
            return

        try:
            found = self.tour_repo.get_existing_tournrs_in_xxatour(dossiers)
            missing = set(dossiers) - set(found)
            self._set_transporter_match_color(len(missing) == 0)

            # optionnel: un petit message barre de statut
            if missing:
                self.statusBar().showMessage(f"Transporteur/dossiers incohérents : {len(missing)} dossier(s) non trouvés en xxatour.", 5000)
        except Exception as e:
            # en cas d'erreur SQL => rouge
            self._set_transporter_match_color(False)
            self.statusBar().showMessage(f"Erreur contrôle xxatour : {e}", 5000)

    def _read_saved_invoice_json(self, pdf_path: str) -> dict:
        json_path = self._get_saved_json_path(pdf_path)
        if not os.path.exists(json_path):
            return {}
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _extract_tournrs_from_saved(self, data: dict) -> list[str]:
        tournrs = []
        folders = data.get("folders") or []
        if isinstance(folders, list):
            for f in folders:
                if isinstance(f, dict):
                    t = f.get("tour_nr") or f.get("TourNr") or f.get("tournr") or ""
                else:
                    t = str(f)
                t = str(t).strip()
                if t:
                    tournrs.append(t)

        if not tournrs:
            t = str(data.get("folder_number") or "").strip()
            if t:
                tournrs.append(t)

        # unique, stable
        return sorted(set(tournrs))

    def _set_left_row_visual(self, row: int, state: str, tooltip: str = ""):
        """
        state: 'ok' | 'error' | 'unknown'
        """
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        it0 = self.pdf_table.item(row, 0)
        if it0:
            it0.setData(Qt.UserRole + 2, state)  # pour le filtre "Erreurs"

        if state == "ok":
            color = QColor(212, 237, 218)   # vert clair
        elif state == "error":
            color = QColor(248, 215, 218)   # rouge clair
        else:
            color = None

        for col in range(self.pdf_table.columnCount()):
            it = self.pdf_table.item(row, col)
            if it is None:
                it = QTableWidgetItem("")
                self.pdf_table.setItem(row, col, it)

            if color is None:
                it.setBackground(QBrush())
            else:
                it.setBackground(color)

            it.setToolTip(tooltip or "")

    def refresh_left_row_processing_state(self, row: int):
        it0 = self.pdf_table.item(row, 0)

        if not it0:
            return
        pdf_path = it0.data(Qt.UserRole)
        if not pdf_path:
            self._set_left_row_visual(row, "unknown", "")
            return

        data = self._read_saved_invoice_json(pdf_path)
        if not data:
            # pas encore sauvegardé => neutre
            self._set_left_row_visual(row, "unknown", "Non sauvegardé.")
            return
        
        # Tag "supprime" => toujours en erreurs
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tags_norm = {str(t).strip().lower() for t in tags if str(t).strip()}

        if "supprime" in tags_norm:
            it0.setData(Qt.UserRole + 3, 1)  # flag "deleted"
            self._set_left_row_visual(row, "error", "Tag 'supprime' : fichier marqué comme supprimé.")
            return

        iban = str(data.get("iban") or "").strip()
        bic = str(data.get("bic") or "").strip()
        tournrs = self._extract_tournrs_from_saved(data)

        if not iban or not bic:
            self._set_left_row_visual(row, "error", "IBAN/BIC manquant dans le JSON.")
            return
        if not tournrs:
            self._set_left_row_visual(row, "error", "Aucun dossier (TourNr) dans le JSON.")
            return

        # 1) transporteur trouvé par iban/bic ?
        try:
            rec = self.transporter_repo.find_transporter_by_bank(iban, bic)
        except Exception as e:
            self._set_left_row_visual(row, "error", f"Erreur SQL transporteur: {e}")
            return

        if not rec:
            self._set_left_row_visual(row, "error", "Transporteur introuvable en base pour cet IBAN/BIC.")
            return

        # 2) tous les dossiers existent dans xxatour ?
        try:
            found = self.tour_repo.get_existing_tournrs_in_xxatour(tournrs)
            missing = sorted(set(tournrs) - set(found))
        except Exception as e:
            self._set_left_row_visual(row, "error", f"Erreur SQL xxatour: {e}")
            return

        if missing:
            more = "" if len(missing) <= 6 else f" (+{len(missing)-6})"
            self._set_left_row_visual(row, "error", f"Dossier(s) manquant(s) en xxatour: {', '.join(missing[:6])}{more}")
            return

        self._set_left_row_visual(row, "ok", "OK : transporteur trouvé + tous les dossiers présents en base.")

    def refresh_left_table_processing_states(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        for row in range(self.pdf_table.rowCount()):
            self.refresh_left_row_processing_state(row)

    def _add_fee_row(self, gebnr: str, bez: str, amount: str = ""):
        row = self.fees_table.rowCount()
        self.fees_table.insertRow(row)

        it0 = QTableWidgetItem(str(gebnr))
        it0.setFlags(it0.flags() & ~Qt.ItemIsEditable)
        it1 = QTableWidgetItem(str(bez))
        it1.setFlags(it1.flags() & ~Qt.ItemIsEditable)

        self.fees_table.setItem(row, 0, it0)
        self.fees_table.setItem(row, 1, it1)

        le = QLineEdit()
        le.setPlaceholderText("Montant")
        le.setClearButtonEnabled(True)
        le.setText("" if amount is None else str(amount))
        le.mousePressEvent = lambda e, f=le: self.set_active_field(f)  # si tu veux le click->champ actif
        self.fees_table.setCellWidget(row, 2, le)

    def on_add_fee(self):
        dlg = GebSearchDialog(self.geb_repo, self)

        if dlg.exec() != QDialog.Accepted or not dlg.selected:
            return

        gebnr = dlg.selected["gebnr"]
        bez = dlg.selected["bez"]

        # éviter doublon
        for r in range(self.fees_table.rowCount()):
            it = self.fees_table.item(r, 0)
            if it and it.text().strip() == gebnr:
                return

        self._add_fee_row(gebnr, bez, "")

    def on_remove_fee(self):
        rows = sorted({idx.row() for idx in self.fees_table.selectionModel().selectedRows()}, reverse=True)
        for r in rows:
            self.fees_table.removeRow(r)

    def get_fee_rows(self):
        out = []
        for r in range(self.fees_table.rowCount()):
            gebnr = (self.fees_table.item(r, 0).text().strip() if self.fees_table.item(r, 0) else "")
            bez = (self.fees_table.item(r, 1).text().strip() if self.fees_table.item(r, 1) else "")
            le = self.fees_table.cellWidget(r, 2)
            amount = (le.text().strip() if le else "")
            if gebnr or bez or amount:
                out.append({"gebnr": gebnr, "bez": bez, "amount": amount})
        return out

    def rebuild_fees_from_json(self, data: dict):
        self.fees_table.setRowCount(0)
        fees = data.get("fees", [])
        if isinstance(fees, list):
            for f in fees:
                if not isinstance(f, dict):
                    continue
                gebnr = str(f.get("gebnr", "") or "").strip()
                bez = str(f.get("bez", "") or "").strip()
                amount = str(f.get("amount", "") or "").strip()
                if gebnr or bez or amount:
                    self._add_fee_row(gebnr, bez, amount)

    def on_ctrl_s_save(self):
        # Ctrl+S = pas de popup, juste statusbar
        self.save_current_data(show_message=False)

        # MAJ modèle supplier en silencieux
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            pass

    def _format_percent(self, v: float | None) -> str:
        if v is None:
            return ""
        try:
            fv = float(v)
        except Exception:
            return ""
        if abs(fv - round(fv)) < 1e-9:
            return str(int(round(fv)))
        return f"{fv:.2f}"
    
    def on_delete_folder_row(self):
        # pas de PDF => pas de sauvegarde/tag
        if not self.current_pdf_path:
            return

        # lignes sélectionnées (ou ligne courante)
        rows = sorted({idx.row() for idx in self.folder_table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            cr = self.folder_table.currentRow()
            if cr >= 0:
                rows = [cr]

        if not rows:
            return

        removed_any = False

        for r in rows:
            dossier_le, amount_le, _ = self._get_row_widgets(r)
            dossier_txt = (dossier_le.text() if dossier_le else "").strip()
            amount_txt = (amount_le.text() if amount_le else "").strip()

            # ne pas supprimer la ligne "vide" de fin
            if not dossier_txt and not amount_txt:
                continue

            self.folder_table.removeRow(r)
            removed_any = True

        if not removed_any:
            return

        # re-garantir une ligne vide en bas + totaux
        self._ensure_empty_folder_row()
        self.update_folder_totals()
        self.update_transporter_vs_dossiers_status()

        # tag + sauvegarde
        self._pending_tags_to_add.add("supprime")
        self.save_current_data(show_message=False)


    def mark_pdf_as_deleted(self, pdf_path: str, filename: str = ""):
        if not pdf_path:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Supprimer")
        msg.setText(
            "Marquer ce fichier comme supprimé ?\n\n"
            f"{filename or os.path.basename(pdf_path)}\n\n"
            "→ Ajoute le tag 'supprime' au JSON et apparaîtra dans le filtre 'Erreurs'."
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        # si c'est le PDF ouvert, on sauvegarde aussi l'état courant de l'UI
        try:
            if self.current_pdf_path == pdf_path:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        json_path = self._get_saved_json_path(pdf_path)

        # load existing JSON (si existe)
        existing = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except Exception:
                existing = {}

        # add tag
        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []

        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.add("supprime")
        existing["tags"] = sorted(tags_set)

        # optionnel: garder une trace
        existing["deleted_at"] = datetime.now().isoformat(timespec="seconds")

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # refresh la ligne dans la table gauche + refiltre
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                self.refresh_left_row_processing_state(r)
                break

        self.apply_left_filter_to_table()
        self.statusBar().showMessage("Fichier marqué comme supprimé.", 2500)

    def _refresh_transporter_after_bank_autofill(self):
        # équivalent au clic sur IBAN/BIC : on repasse en recherche par banque
        self.transporter_selected_mode = False
        self.selected_kundennr = None

        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        if not iban or not bic:
            return  # on attend d'avoir les deux

        self.check_bank_information()
        self.load_transporter_information(force_by_kundennr=False)

    def compact_folder_rows(self):
        # évite les appels re-entrants
        if getattr(self, "_compacting_folder_rows", False):
            return
        self._compacting_folder_rows = True
        try:
            kept = []
            for r in range(self.folder_table.rowCount()):
                dossier_le, amount_le, _ = self._get_row_widgets(r)
                dossier = (dossier_le.text() if dossier_le else "").strip()
                amount = (amount_le.text() if amount_le else "").strip()

                # on garde les lignes non vides
                if dossier or amount:
                    kept.append((dossier, amount))

            # rebuild table (sans trous)
            self.folder_table.setRowCount(0)
            for dossier, amount in kept:
                self._add_folder_row(dossier=dossier, amount=amount)

            # garde une ligne vide en bas
            self._ensure_empty_folder_row()

            # refresh totaux / statuts
            self.update_folder_totals()
            self.update_transporter_vs_dossiers_status()

        finally:
            self._compacting_folder_rows = False

    def _find_pdf_path_by_filename(self, filename: str) -> str | None:
        """Retrouve le chemin PDF (UserRole) à partir du nom affiché en colonne 0."""
        filename = (filename or "").strip()
        if not filename:
            return None
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and (it0.text() or "").strip() == filename:
                return it0.data(Qt.UserRole)
        return None


    def _get_folder_choices_for_entry(self, entry_id: str) -> list[dict]:
        """
        Retourne la liste des dossiers (tour_nr + amount_ht_ocr) UNIQUEMENT depuis
        le tableau de droite SI l'entry_id correspond au document sélectionné.
        Sinon fallback: on cherche dans les JSON des documents du même entry_id.
        """
        entry_id = (entry_id or "").strip()
        if not entry_id:
            return []

        # 1) Source prioritaire : le tableau de droite (UI) si on est sur le même entry_id
        if self.selected_invoice_entry_id == entry_id:
            folders = self.get_folder_rows() or []
            folders = [f for f in folders if str(f.get("tour_nr") or "").strip()]
            if folders:
                return folders

        # 2) Fallback : lire les JSON d'un doc du même entry_id (utile si clic-droit sans ouvrir)
        try:
            rows = self.logmail_repo.get_files_for_entry(entry_id) or []
        except Exception:
            rows = []

        found: dict[str, dict] = {}
        for r in rows:
            name = str(r.get("nom_pdf") or "").strip()
            if not name:
                continue
            pdf_path = self._find_pdf_path_by_filename(name)
            if not pdf_path:
                continue
            data = self._read_saved_invoice_json(pdf_path) or {}
            folders = data.get("folders") or []
            if not isinstance(folders, list):
                continue
            for f in folders:
                tournr = str(f.get("tour_nr") or "").strip()
                if tournr and tournr not in found:
                    found[tournr] = {"tour_nr": tournr, "amount_ht_ocr": str(f.get("amount_ht_ocr") or "").strip()}

        return list(found.values())
    

    def attach_cmr_to_dossier_from_right_list(self, pdf_path: str, filename: str, entry_id: str | None = None):
        """
        Rattache un PDF (souvent une CMR) à un dossier (TourNr) du même entry_id.

        - Les choix de dossiers viennent du tableau de droite (si l'entry_id est celui affiché),
        sinon fallback : lecture des JSON des docs du même entry_id.
        - La popup n'affiche plus les montants : elle affiche Dossier / Trajet / VPE / Palettes / Poids.
        - On écrit dans le JSON du document : tag 'cmr', cmr_tour_nr, cmr_attached_at.
        """

        if not pdf_path:
            return
        if not filename:
            filename = os.path.basename(pdf_path)

        # ✅ priorité : entry_id déjà connu (fenêtre principale), sinon fallback BDD
        entry_id = (entry_id or self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(filename))
        entry_id = (entry_id or "").strip()

        if not entry_id:
            QMessageBox.information(self, "Rattacher CMR", "Impossible de déterminer l'entry_id de ce document.")
            return

        # Liste des dossiers possibles
        folders = self._get_folder_choices_for_entry(entry_id)
        if not folders:
            QMessageBox.information(
                self,
                "Rattacher CMR",
                "Aucun dossier disponible.\n\n"
                "➡️ Renseigne d'abord les numéros de dossier dans le tableau de droite "
                "(sur un document du même entry_id), puis sauvegarde."
            )
            return

        # TourNr uniques (dans l'ordre)
        tour_numbers: list[str] = []
        seen = set()
        for f in folders:
            t = str((f or {}).get("tour_nr") or "").strip()
            if t and t not in seen:
                seen.add(t)
                tour_numbers.append(t)

        if not tour_numbers:
            QMessageBox.information(self, "Rattacher CMR", "Aucun numéro de dossier valide.")
            return

        # Détails SQL (VPE / palettes / poids / trajet)
        details_rows = []
        try:
            # nécessite la méthode ajoutée dans TourRepository
            details_rows = self.tour_repo.get_palette_details_with_trajet_by_tournrs(tour_numbers) or []
        except Exception:
            details_rows = []

        dlg = FolderSelectDialog(tour_numbers, details_rows, parent=self, title="Rattacher CMR à une commande")
        if dlg.exec() != QDialog.Accepted or not dlg.selected_tour_nr or not dlg.selected_auf_nr:
            return

        tour_nr = str(dlg.selected_tour_nr).strip()
        auf_nr  = str(dlg.selected_auf_nr).strip()
        if not tour_nr:
            return

        # Si le document courant = celui qu'on rattache, on sauvegarde d'abord l'UI (optionnel mais safe)
        try:
            if self.current_pdf_path == pdf_path:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        # --- Update JSON du document CMR ---
        json_path = self._get_saved_json_path(pdf_path)
        existing = self._read_saved_invoice_json(pdf_path) or {}

        # tags -> ajouter "cmr"
        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []
        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.add("cmr")
        existing["tags"] = sorted(tags_set)

        # rattachement
        existing["entry_id"] = entry_id
        existing["cmr_tour_nr"] = tour_nr
        existing["cmr_attached_at"] = datetime.now().isoformat(timespec="seconds")
        existing["cmr_auf_nr"] = auf_nr

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # --- Refresh UI gauche (ligne fichier) ---
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                it0.setToolTip(f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}")
                self.statusBar().showMessage(f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}.", 2500)
                break

        self.apply_left_filter_to_table()
        self.statusBar().showMessage(f"CMR rattachée au dossier {tour_nr}.", 2500)

        # --- Refresh icônes CMR (table dossiers à droite) si on est sur le même entry affiché ---
        try:
            if self.selected_invoice_entry_id and self.selected_invoice_entry_id.strip() == entry_id:
                for r in range(self.folder_table.rowCount()):
                    self._update_folder_row_status(r)
        except Exception:
            pass

        # Optionnel : refresh volet tour si tu en as un affiché
        try:
            if getattr(self, "last_loaded_tour_nr", None):
                self.load_tour_information(self.last_loaded_tour_nr)
        except Exception:
            pass


    def _choose_representative_pdf(self, group_paths: list[str]) -> str:
        """
        Choisit le meilleur PDF pour représenter un entry_id.
        Priorité :
        1) JSON avec iban+bic + au moins un dossier (TourNr)
        2) JSON avec iban+bic
        3) premier fichier
        """
        if not group_paths:
            return ""

        best_iban_bic_and_folders = None
        best_iban_bic = None

        for p in group_paths:
            data = self._read_saved_invoice_json(p) or {}
            if not data:
                continue

            iban = str(data.get("iban") or "").strip()
            bic = str(data.get("bic") or "").strip()
            folders = self._extract_tournrs_from_saved(data) if hasattr(self, "_extract_tournrs_from_saved") else []

            if iban and bic and folders:
                best_iban_bic_and_folders = p
                break
            if iban and bic and best_iban_bic is None:
                best_iban_bic = p

        return best_iban_bic_and_folders or best_iban_bic or group_paths[0]
    

    def on_attach_cmr_main(self):
        # Le document réellement affiché peut être view_pdf_path (navigation doc)
        pdf_path = self.view_pdf_path or self.current_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.information(self, "Rattacher CMR", "Aucun document affiché.")
            return

        filename = os.path.basename(pdf_path)

        # On passe entry_id si déjà connu (plus rapide)
        entry_id = self.selected_invoice_entry_id
        self.attach_cmr_to_dossier_from_right_list(pdf_path, filename, entry_id=entry_id)

    def _collect_cmr_attachments_for_current_entry(self) -> list[dict]:
        """
        Construit la liste des CMR rattachées (depuis les JSON des docs du même entry_id).
        Stocké dans le JSON de la facture sous 'cmr_attachments'.
        """
        out: list[dict] = []
        seen = set()

        paths = self.entry_pdf_paths or []
        for p in paths:
            # on exclut la facture elle-même (current_pdf_path) par sécurité
            if self.current_pdf_path and os.path.abspath(p) == os.path.abspath(self.current_pdf_path):
                continue

            data = self._read_saved_invoice_json(p) or {}
            tour_nr = str(data.get("cmr_tour_nr") or "").strip()

            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            tags_norm = {str(t).strip().lower() for t in tags if str(t).strip()}

            # on considère "CMR" si tag cmr OU cmr_tour_nr rempli
            if "cmr" not in tags_norm and not tour_nr:
                continue

            fn = os.path.basename(p)
            key = (fn, tour_nr)
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "filename": fn,
                "tour_nr": tour_nr,
                "attached_at": str(data.get("cmr_attached_at") or "").strip()
            })

        return out
    

    def _get_current_invoice_tours(self) -> set[str]:
        """TourNr présents dans le tableau de droite (dossiers)."""
        tours = set()
        for f in (self.get_folder_rows() or []):
            t = str(f.get("tour_nr") or "").strip()
            if t:
                tours.add(t)
        return tours


    def _get_cmr_attached_tours_for_entry(self) -> set[str]:
        """TourNr qui ont au moins une CMR rattachée (via JSON des docs du même entry_id)."""
        tours = set()
        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}
            t = str(data.get("cmr_tour_nr") or "").strip()
            if t:
                tours.add(t)
        return tours

    def _get_row_cmr_widget(self, row: int):
        return self.folder_table.cellWidget(row, 3)
    

    def _check_all_dossiers_have_cmr(self) -> tuple[bool, list[str]]:
        """
        Retourne (ok, missing_tours).
        ok = True si tous les TourNr présents dans le tableau de droite ont au moins une CMR rattachée.
        """
        invoice_tours = self._get_current_invoice_tours()  # set[str] depuis tableau de droite
        if not invoice_tours:
            # s'il n'y a aucun dossier, on ne bloque pas ici (tu as peut-être déjà d'autres règles)
            return True, []

        cmr_tours = self._get_cmr_attached_tours_for_entry()  # set[str] depuis JSON CMR
        missing = sorted(invoice_tours - cmr_tours)
        return (len(missing) == 0), missing


    def _block_validate_if_missing_cmr(self) -> bool:
        """
        Retourne True si on peut continuer la validation.
        Retourne False si bloqué + message.
        """
        ok, missing = self._check_all_dossiers_have_cmr()
        if ok:
            return True

        QMessageBox.warning(
            self,
            "Validation impossible",
            "Tous les dossiers doivent être rattachés à au moins une CMR avant validation.\n\n"
            f"Dossiers sans CMR : {', '.join(missing)}"
        )
        return False   



    def on_fetch_links_main(self):
        pdf_path = self.view_pdf_path or self.current_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.information(self, "Liens", "Aucun document affiché.")
            return

        source_filename = os.path.basename(pdf_path)

        info = self._get_logmail_info_for_pdf(source_filename)
        entry_id = (info.get("entry_id") or "").strip()
        message_id = (info.get("message_id") or "").strip()
        sujet = info.get("sujet") or ""
        expediteur = info.get("expediteur") or ""

        if not entry_id:
            try:
                json_path = self._get_saved_json_path(pdf_path)
                if os.path.exists(json_path):
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f) or {}
                    entry_id = str(data.get("entry_id") or "").strip()
            except Exception:
                pass

        if not entry_id:
            QMessageBox.information(self, "Liens", "Impossible de déterminer l'entry_id pour ce document.")
            return

        if not message_id:
            message_id = entry_id

        urls = self._extract_urls_from_pdf(pdf_path)
        if not urls:
            QMessageBox.information(self, "Liens", "Aucun lien HTTP(S) trouvé dans ce document.")
            return

        dest_folder = os.path.dirname(pdf_path)

        # ✅ progress dialog
        self._links_prog = QProgressDialog("Téléchargement des documents liés…", "Annuler", 0, len(urls), self)
        self._links_prog.setWindowModality(Qt.WindowModal)
        self._links_prog.setMinimumDuration(0)
        self._links_prog.setValue(0)
        self._links_prog.show()

        # ✅ thread + worker
        self._links_thread = QThread(self)
        self._links_worker = LinkDownloadWorker(urls, dest_folder)
        self._links_worker.moveToThread(self._links_thread)

        # cancel
        self._links_prog.canceled.connect(self._links_worker.cancel)

        # start
        self._links_thread.started.connect(self._links_worker.run)

        # progress UI
        self._links_worker.progress.connect(lambda v, txt: (self._links_prog.setValue(v), self._links_prog.setLabelText(txt)))

        def _done(downloaded_paths: list[str], errors: list[str], canceled: bool):
            # stop thread download
            try:
                self._links_thread.quit()
            except Exception:
                pass

            if canceled or not downloaded_paths:
                try:
                    self._links_prog.close()
                except Exception:
                    pass
                QMessageBox.information(self, "Liens", "Annulé." if canceled else "Aucun téléchargement.")
                return

            # ✅ maintenant on lance le post-process en thread (BDD + JSON)
            self._post_thread = QThread(self)
            self._post_worker = LinkPostProcessWorker(downloaded_paths, entry_id, message_id, sujet, expediteur)
            self._post_worker.moveToThread(self._post_thread)

            # progress dialog réutilisé
            try:
                self._links_prog.setMaximum(len(downloaded_paths))
                self._links_prog.setValue(0)
                self._links_prog.setLabelText("Mise à jour BDD/JSON…")
            except Exception:
                pass

            # cancel => post worker
            try:
                self._links_prog.canceled.disconnect(self._links_worker.cancel)
            except Exception:
                pass
            self._links_prog.canceled.connect(self._post_worker.cancel)

            self._post_thread.started.connect(self._post_worker.run)
            self._post_worker.progress.connect(lambda v, txt: (self._links_prog.setValue(v), self._links_prog.setLabelText(txt)))

            def _post_done(downloaded_names: list[str], post_errors: list[str], post_canceled: bool):
                try:
                    self._links_prog.close()
                except Exception:
                    pass

                all_errors = (errors or []) + (post_errors or [])

                # ⚠️ refresh folder : peut être lourd, donc on le fait après (et tu peux le désactiver si besoin)
                try:
                    self.load_folder(dest_folder)
                except Exception:
                    pass

                msg = []
                if downloaded_names:
                    msg.append("Téléchargés + ajoutés en base:\n- " + "\n- ".join(downloaded_names))
                if post_canceled:
                    msg.append("\nPost-traitement annulé.")
                if all_errors:
                    msg.append("\nErreurs:\n- " + "\n- ".join(all_errors[:8]) + ("\n(...)" if len(all_errors) > 8 else ""))

                QMessageBox.information(self, "Liens", "\n\n".join(msg) if msg else "Terminé.")

                try:
                    self._post_thread.quit()
                except Exception:
                    pass

            self._post_worker.finished.connect(_post_done)
            self._post_thread.start()

            self._links_worker.finished.connect(_done)
            self._links_thread.finished.connect(self._links_thread.deleteLater)
            self._links_worker.finished.connect(self._links_worker.deleteLater)

            self._links_thread.start()


    def _extract_urls_from_pdf(self, pdf_path: str) -> list[str]:
        urls = []
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                # liens PDF (annotations)
                try:
                    for lk in page.get_links() or []:
                        uri = lk.get("uri")
                        if uri and isinstance(uri, str) and uri.lower().startswith(("http://", "https://")):
                            urls.append(uri)
                except Exception:
                    pass

                # liens dans le texte
                try:
                    txt = page.get_text() or ""
                    for m in _URL_RE.findall(txt):
                        urls.append(m)
                except Exception:
                    pass
            doc.close()
        except Exception:
            return []

        # clean + unique
        out = []
        seen = set()
        for u in urls:
            u = (u or "").strip().rstrip(").,;\"'")
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out  


    def _guess_filename_from_url(self, url: str) -> str:
        try:
            p = urlparse(url)
            base = os.path.basename(p.path) or ""
            if base:
                return self._safe_filename(base)
        except Exception:
            pass
        return "document.pdf"


    def _safe_filename(self, name: str) -> str:
        name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
        name = re.sub(r"\s{2,}", " ", name).strip()
        return name or "document.pdf"


    def _get_logmail_info_for_pdf(self, nom_pdf: str) -> dict:
        # TOP 1 le plus récent (utile si doublons)
        row = self.logmail_repo.fetch_one(
            """
            SELECT TOP 1 message_id, entry_id, sujet, expediteur
            FROM XXA_LOGMAIL_228794
            WHERE nom_pdf = ?
            ORDER BY date_creation DESC, id_log DESC
            """,
            (nom_pdf,),
        )
        return row or {}


    def _upsert_logmail_for_downloaded_file(self, nom_pdf: str, entry_id: str, message_id: str, sujet: str = "", expediteur: str = ""):
        # Update si existe, sinon insert
        self.logmail_repo.execute(
            """
            UPDATE XXA_LOGMAIL_228794
            SET entry_id = ?, message_id = ?
            WHERE nom_pdf = ?;

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO XXA_LOGMAIL_228794 (date_creation, message_id, entry_id, nom_pdf, sujet, expediteur)
                VALUES (SYSDATETIME(), ?, ?, ?, ?, ?)
            END
            """,
            (entry_id, message_id, nom_pdf, message_id, entry_id, nom_pdf, sujet or "", expediteur or ""),
        )


    def _create_minimal_json_no_ocr(self, pdf_path: str, entry_id: str):
        json_path = self._get_saved_json_path(pdf_path)
        if os.path.exists(json_path):
            return
        data = {
            "entry_id": entry_id,
            "status": "draft",
            "tags": ["cmr"],
            "ocr_text": "",
            "folders": [],
            "vat_lines": [],
            "fees": [],
        }
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def on_save_clicked(self):
        # sauvegarde normale (avec popup)
        self.save_current_data(show_message=True)

        # MAJ modèle supplier en silencieux
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            pass


    def _get_cmr_attached_orders_for_entry(self) -> dict[str, set[str]]:
        """
        Retour: {tour_nr -> set(auf_nr)} pour les CMR rattachées sur les docs du même entry_id.
        (compat: si une ancienne CMR n'a pas cmr_auf_nr, on la note en legacy)
        """
        out = defaultdict(set)
        legacy = defaultdict(int)

        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}
            t = str(data.get("cmr_tour_nr") or "").strip()
            a = str(data.get("cmr_auf_nr") or "").strip()
            if not t:
                continue
            if a:
                out[t].add(a)
            else:
                legacy[t] += 1

        # on garde legacy dispo pour tooltips si besoin
        self._cmr_legacy_cache = dict(legacy)
        return dict(out)

    def _get_required_orders_by_tour(self, tours: set[str]) -> dict[str, set[str]]:
        """Retour: {tour_nr -> set(auf_nr)} depuis la BDD (via get_palette_details_with_trajet_by_tournrs)."""
        key = tuple(sorted(tours))
        if getattr(self, "_req_orders_cache_key", None) == key:
            return getattr(self, "_req_orders_cache", {}) or {}

        req = defaultdict(set)
        try:
            rows = self.tour_repo.get_palette_details_with_trajet_by_tournrs(list(tours)) or []
        except Exception:
            rows = []

        for r in rows:
            tour = str(r.get("Dossier") or "").strip()
            auf  = str(r.get("AufNr") or "").strip()
            if tour and auf:
                req[tour].add(auf)

        self._req_orders_cache_key = key
        self._req_orders_cache = dict(req)
        return self._req_orders_cache

    def _check_all_orders_have_cmr(self) -> tuple[bool, dict[str, list[str]]]:
        """
        ok=True si toutes les commandes (AufNr) de tous les dossiers ont une CMR.
        Retourne missing_by_tour = {tour_nr: [auf_nr, ...]}
        """
        invoice_tours = self._get_current_invoice_tours()
        if not invoice_tours:
            return True, {}

        required = self._get_required_orders_by_tour(invoice_tours)
        attached = self._get_cmr_attached_orders_for_entry()
        legacy = getattr(self, "_cmr_legacy_cache", {}) or {}

        missing_by_tour = {}

        for tour in sorted(invoice_tours):
            req = set(required.get(tour, set()))
            att = set(attached.get(tour, set()))

            # compat: si on a une CMR "ancienne" sans auf_nr et qu'il n'y a qu'UNE commande, on considère OK
            if not att and legacy.get(tour, 0) > 0 and len(req) == 1:
                att = set(req)

            if req:
                miss = sorted(req - att)
                if miss:
                    missing_by_tour[tour] = miss
            else:
                # pas de commandes trouvées en BDD -> on bloque (sinon validation fausse)
                missing_by_tour[tour] = ["(aucune commande trouvée en BDD)"]

        return (len(missing_by_tour) == 0), missing_by_tour

    def _block_validate_if_missing_cmr(self) -> bool:
        ok, missing_by_tour = self._check_all_orders_have_cmr()
        if ok:
            return True

        lines = []
        for tour, miss in missing_by_tour.items():
            lines.append(f"{tour}: {', '.join(miss)}")

        QMessageBox.warning(
            self,
            "Validation impossible",
            "Tous les dossiers doivent avoir une CMR pour CHAQUE commande.\n\n"
            "Commandes sans CMR :\n" + "\n".join(lines)
        )
        return False
    

    def relink_left_document_to_other_group(self, row: int):
        it0 = self.pdf_table.item(row, 0)
        if not it0:
            return

        # --- source: choisir quel PDF du groupe on déplace ---
        group_paths = it0.data(Qt.UserRole + 5)
        if isinstance(group_paths, (list, tuple)) and group_paths:
            src_paths = [p for p in group_paths if p and os.path.exists(p)]
        else:
            p = it0.data(Qt.UserRole)
            src_paths = [p] if p and os.path.exists(p) else []

        if not src_paths:
            QMessageBox.information(self, "Regrouper", "Impossible de retrouver le fichier source.")
            return

        # si plusieurs docs dans le groupe, on laisse choisir lequel déplacer
        if len(src_paths) > 1:
            labels = [f"{i+1}) {os.path.basename(p)}" for i, p in enumerate(src_paths)]
            default_idx = 0
            # si le PDF affiché fait partie du groupe, on le pré-sélectionne
            if getattr(self, "current_pdf_path", None) in src_paths:
                default_idx = src_paths.index(self.current_pdf_path)

            choice, ok = QInputDialog.getItem(
                self, "Regrouper", "Document à rattacher :", labels, default_idx, False
            )
            if not ok or not choice:
                return
            src_path = src_paths[int(choice.split(")")[0]) - 1]
        else:
            src_path = src_paths[0]

        src_name = os.path.basename(src_path)
        src_entry_id = (self.logmail_repo.get_entry_id_for_file(src_name) or "").strip()

        # --- cible: choisir un autre fichier (donc un autre entry_id) ---
        candidates = []
        targets = []

        for r in range(self.pdf_table.rowCount()):
            it = self.pdf_table.item(r, 0)
            if not it:
                continue

            target_entry = str(it.data(Qt.UserRole + 4) or "").strip()  # ✅ entry_id
            target_path = it.data(Qt.UserRole)
            if not target_entry or not target_path:
                continue

            # exclure le même groupe
            if src_entry_id and target_entry == src_entry_id:
                continue

            rep_name = os.path.basename(str(target_path))
            group_paths2 = it.data(Qt.UserRole + 5)
            n_docs = len(group_paths2) if isinstance(group_paths2, (list, tuple)) else 1
            label = f"{rep_name}   ({n_docs} doc)   [{target_entry}]"
            candidates.append(label)
            targets.append(target_entry)

        if not candidates:
            QMessageBox.information(self, "Regrouper", "Aucune cible disponible (pas d'autre groupe).")
            return

        choice, ok = QInputDialog.getItem(
            self, "Rattacher à un Dossier", "Choisis le fichier/groupe cible :", candidates, 0, False
        )
        if not ok or not choice:
            return

        idx = candidates.index(choice)
        target_entry_id = targets[idx]

        # --- UPDATE base: regrouper en base ---
        try:
            self.logmail_repo.set_entry_id_for_file(src_name, target_entry_id)
        except Exception as e:
            QMessageBox.warning(self, "Regrouper", f"Erreur SQL:\n{e}")
            return

        # --- UPDATE JSON local: entry_id ---
        try:
            data = self._read_saved_invoice_json(src_path) or {}
            data["entry_id"] = target_entry_id
            json_path = self._get_saved_json_path(src_path)
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            # pas bloquant
            pass

        # --- refresh UI ---
        try:
            self.load_folder(os.path.dirname(src_path))
        except Exception:
            pass

        self.statusBar().showMessage(f"{src_name} rattaché au groupe {target_entry_id}.", 3000)

class LinkDownloadWorker(QObject):
    progress = Signal(int, str)          # (index, label)
    finished = Signal(list, list, bool)  # (downloaded_paths, errors, canceled)

    def __init__(self, urls: list[str], dest_folder: str, parent=None):
        super().__init__(parent)
        self.urls = urls
        self.dest_folder = dest_folder
        self._cancelled = False
        self._current_resp = None

    @Slot()
    def cancel(self):
        self._cancelled = True
        # ✅ tente d'interrompre un read bloqué
        try:
            if self._current_resp is not None:
                self._current_resp.close()
        except Exception:
            pass

    def _safe_filename(self, name: str) -> str:
        name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
        name = re.sub(r"\s{2,}", " ", name).strip()
        return name or "document.pdf"

    def _guess_filename_from_url(self, url: str) -> str:
        try:
            p = urlparse(url)
            base = os.path.basename(p.path) or ""
            if base:
                return self._safe_filename(base)
        except Exception:
            pass
        return "document.pdf"

    def _unique_path(self, path: str) -> str:
        if not os.path.exists(path):
            return path
        root, ext = os.path.splitext(path)
        k = 2
        while os.path.exists(f"{root}_{k}{ext}"):
            k += 1
        return f"{root}_{k}{ext}"

    @Slot()
    def run(self):
        os.makedirs(self.dest_folder, exist_ok=True)

        downloaded = []
        errors = []
        canceled = False

        for i, url in enumerate(self.urls, start=1):
            if self._cancelled:
                canceled = True
                break

            self.progress.emit(i - 1, f"{i}/{len(self.urls)}\n{url}")

            try:
                req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(req, timeout=20) as resp:
                    self._current_resp = resp

                    # nom par défaut
                    filename = self._guess_filename_from_url(url)
                    if not filename.lower().endswith(".pdf"):
                        filename = os.path.splitext(filename)[0] + ".pdf"

                    # content-disposition si présent
                    cd = resp.headers.get("Content-Disposition", "") or ""
                    m = re.search(r'filename="?([^"]+)"?', cd, re.IGNORECASE)
                    if m:
                        fn = self._safe_filename(m.group(1))
                        if not fn.lower().endswith(".pdf"):
                            fn += ".pdf"
                        filename = fn

                    target = self._unique_path(os.path.join(self.dest_folder, filename))
                    tmp = target + ".part"

                    with open(tmp, "wb") as f:
                        # lit un petit header pour valider PDF
                        head = resp.read(5)
                        if self._cancelled:
                            raise RuntimeError("CANCELLED")
                        if not head.startswith(b"%PDF"):
                            raise ValueError("Le lien ne renvoie pas un PDF (%PDF manquant).")
                        f.write(head)

                        while True:
                            if self._cancelled:
                                raise RuntimeError("CANCELLED")
                            chunk = resp.read(256 * 1024)
                            if not chunk:
                                break
                            f.write(chunk)

                    # commit atomique
                    if os.path.exists(target):
                        os.remove(target)
                    os.replace(tmp, target)
                    downloaded.append(target)

            except Exception as e:
                # nettoyage .part
                try:
                    # si on a pu déterminer un tmp
                    if 'tmp' in locals() and os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass

                if str(e) == "CANCELLED":
                    canceled = True
                    break
                errors.append(f"{url} -> {e}")

            finally:
                self._current_resp = None

        # finir la barre
        self.progress.emit(len(self.urls), f"{len(self.urls)}/{len(self.urls)}")
        self.finished.emit(downloaded, errors, canceled)

class LinkPostProcessWorker(QObject):
    progress = Signal(int, str)           # (index, label)
    finished = Signal(list, list, bool)   # (downloaded_names, errors, canceled)

    def __init__(self, pdf_paths: list[str], entry_id: str, message_id: str, sujet: str, expediteur: str):
        super().__init__()
        self.pdf_paths = pdf_paths or []
        self.entry_id = entry_id
        self.message_id = message_id
        self.sujet = sujet or ""
        self.expediteur = expediteur or ""
        self._cancelled = False

    @Slot()
    def cancel(self):
        self._cancelled = True

    def _json_path_for_pdf(self, pdf_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        return os.path.join(model_dir, f"{base_name}.json")

    def _create_minimal_json_no_ocr(self, pdf_path: str):
        json_path = self._json_path_for_pdf(pdf_path)
        if os.path.exists(json_path):
            return
        data = {
            "entry_id": self.entry_id,
            "status": "draft",
            "tags": ["cmr"],
            "ocr_text": "",
            "folders": [],
            "vat_lines": [],
            "fees": [],
        }
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @Slot()
    def run(self):
        downloaded_names = []
        errors = []
        canceled = False

        # ✅ repo DB dans le worker (thread-safe car BaseRepository ouvre une connexion par appel)
        from db.connection import SqlServerConnection
        from db.config import DB_CONFIG
        from db.logmail_repository import LogmailRepository

        repo = LogmailRepository(SqlServerConnection(**DB_CONFIG))

        total = len(self.pdf_paths)

        for i, p in enumerate(self.pdf_paths, start=1):
            if self._cancelled:
                canceled = True
                break

            name = os.path.basename(p)
            self.progress.emit(i - 1, f"BDD/JSON {i}/{total}\n{name}")

            try:
                # upsert logmail
                repo.execute(
                    """
                    UPDATE XXA_LOGMAIL_228794
                    SET entry_id = ?, message_id = ?
                    WHERE nom_pdf = ?;

                    IF @@ROWCOUNT = 0
                    BEGIN
                        INSERT INTO XXA_LOGMAIL_228794 (date_creation, message_id, entry_id, nom_pdf, sujet, expediteur)
                        VALUES (SYSDATETIME(), ?, ?, ?, ?, ?)
                    END
                    """,
                    (self.entry_id, self.message_id, name, self.message_id, self.entry_id, name, self.sujet, self.expediteur),
                )

                # json minimal
                self._create_minimal_json_no_ocr(p)

                downloaded_names.append(name)

            except Exception as e:
                errors.append(f"{name} -> {e}")

        self.progress.emit(total, f"BDD/JSON {total}/{total}")
        self.finished.emit(downloaded_names, errors, canceled)