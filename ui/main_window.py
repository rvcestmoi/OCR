# ui/main_window.py
from __future__ import annotations

import os
import re
import json
import fitz  # PyMuPDF



from PySide6.QtCore import Qt, QStringListModel
from PySide6.QtGui import QImage, QPixmap, QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QPushButton,
    QVBoxLayout, QHBoxLayout, QFileDialog, QLabel,
    QLineEdit, QFormLayout, QMessageBox, QPlainTextEdit,
    QTextEdit, QTableWidget, QTableWidgetItem, QProgressDialog,
    QApplication, QSplitter, QCompleter, QHeaderView
)
##from matplotlib import text


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
from ui.pallet_details_dialog import PalletDetailsDialog
from PySide6.QtWidgets import QDialog
from ui.block_options_dialog import BlockOptionsDialog

from datetime import datetime
from ocr.supplier_model import extract_best_bank_ids


class MainWindow(QMainWindow):
    DOSSIER_PATTERN = r"\b(?:1\d{8}|(?:84|25|35|44|64|67|69|72|78)\d{7})\b"

    def __init__(self):
        super().__init__()
        from db.connection import SqlServerConnection
        from db.config import DB_CONFIG
        from db.logmail_repository import LogmailRepository
        from db.transporter_repository import TransporterRepository
        from db.bank_repository import BankRepository
        from db.tour_repository import TourRepository
        from ui.ocr_text_view import OcrTextView

        self.db_conn = SqlServerConnection(**DB_CONFIG)

        self.logmail_repo = LogmailRepository(self.db_conn)
        self.transporter_repo = TransporterRepository(self.db_conn)
        self.bank_repo = BankRepository(self.db_conn)
        self.tour_repo = TourRepository(self.db_conn)

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
        self.transporter_selected_mode = False  # True = on est sur un transporteur choisi (kundennr)

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
        left_splitter = QSplitter(Qt.Vertical)

        left_top_widget = QWidget()
        left_top_layout = QVBoxLayout(left_top_widget)

        self.btn_scan_folder = QPushButton("📂 Analyser un dossier")
        self.btn_scan_folder.clicked.connect(self.select_folder)

        self.btn_ocr_all = QPushButton("⚙️ OCRiser")
        self.btn_ocr_all.clicked.connect(self.ocr_all_pdfs)

        left_top_layout.addWidget(self.btn_ocr_all)
        left_top_layout.addWidget(self.btn_scan_folder)

        self.pdf_table = QTableWidget()
        self.pdf_table.setColumnCount(1)
        self.pdf_table.setHorizontalHeaderLabels(["Nom du fichier"])
        self.pdf_table.horizontalHeader().setStretchLastSection(True)
        self.pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pdf_table.setAlternatingRowColors(True)
        self.pdf_table.cellClicked.connect(self.on_pdf_selected)
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

        left_splitter.addWidget(left_top_widget)
        left_splitter.addWidget(left_bottom_widget)
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)
        main_layout.addWidget(left_splitter, 2)

        # =========================
        # Panneau central (PDF)
        # =========================
        center_panel = QVBoxLayout()

        # --- Barre navigation PDF (docs + pages) ---
        pdf_nav = QHBoxLayout()

        # ✅ navigation documents (même entry_id)
        self.btn_prev_doc = QPushButton("⏪")
        self.btn_next_doc = QPushButton("⏩")
        self.lbl_doc_info = QLabel("0 / 0")

        self.btn_prev_doc.setToolTip("Document précédent")
        self.btn_next_doc.setToolTip("Document suivant")

        self.btn_prev_doc.clicked.connect(self.on_prev_doc)
        self.btn_next_doc.clicked.connect(self.on_next_doc)

        # ✅ navigation pages (dans le PDF)
        self.btn_prev_page = QPushButton("⏮")
        self.btn_next_page = QPushButton("⏭")
        self.lbl_page_info = QLabel("0 / 0")

        self.btn_prev_page.clicked.connect(self.on_prev_page)
        self.btn_next_page.clicked.connect(self.on_next_page)

        pdf_nav.addStretch()
        pdf_nav.addWidget(self.btn_prev_doc)
        pdf_nav.addWidget(self.lbl_doc_info)
        pdf_nav.addWidget(self.btn_next_doc)

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

        form_layout.addRow("Date facture :", self.date_input)
        form_layout.addRow("N° facture :", self.invoice_number_input)

        # =========================
        # Table dossiers (N° dossier / Montant HT)
        # =========================
        self.folder_table = QTableWidget(0, 2)
        self.folder_table.setHorizontalHeaderLabels(["N° dossier", "Montant HT (OCR)"])
        self.folder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.folder_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.folder_table.setAlternatingRowColors(True)
        self.folder_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.folder_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.folder_table.setMinimumHeight(140)

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
        self.vat_table.setMinimumHeight(110)

        self.lbl_vat_total = QLabel("")
        self.lbl_vat_total.setStyleSheet("padding:4px;")

        # =========================
        # Conteneur vertical (dossiers + totaux + TVA)
        # =========================
        folders_box = QWidget()
        self.folders_layout = QVBoxLayout(folders_box)
        self.folders_layout.setContentsMargins(0, 0, 0, 0)

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
        self.btn_save_data.clicked.connect(self.save_current_data)
        right_panel.addWidget(self.btn_save_data)

        self.btn_save_supplier = QPushButton("⭐ Mettre à jour modèle fournisseur")
        self.btn_save_supplier.clicked.connect(self.save_supplier_model)
        right_panel.addWidget(self.btn_save_supplier)

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
            dossier_le, amount_le = self._get_row_widgets(r)
            if field == dossier_le or field == amount_le:
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
        self.active_field.setStyleSheet("background-color: #e6ffe6;")

    # =========================
    # Folder fields helpers
    # =========================
    def get_folder_line_edits(self) -> list[QLineEdit]:
        out = []
        for r in range(self.folder_table.rowCount()):
            dossier_le, _ = self._get_row_widgets(r)
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
        DEFAULT_OCR_FOLDER = r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles"
        folder = QFileDialog.getExistingDirectory(self, "Sélectionner un dossier", DEFAULT_OCR_FOLDER)
        if not folder:
            return

        self.pdf_table.setRowCount(0)

        pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]
        for row, file in enumerate(pdf_files):
            self.pdf_table.insertRow(row)
            item = QTableWidgetItem(file)
            item.setData(Qt.UserRole, os.path.join(folder, file))
            self.pdf_table.setItem(row, 0, item)

        self.current_pdf_path = None
        self.clear_fields()

    def on_pdf_selected(self, row, column):
        item = self.pdf_table.item(row, 0)
        if not item:
            return

        self.current_pdf_path = item.data(Qt.UserRole)
        invoice_filename = os.path.basename(self.current_pdf_path)
        self.selected_invoice_filename = invoice_filename
        self.selected_invoice_entry_id = self.logmail_repo.get_entry_id_for_file(invoice_filename)

        # facture = PDF cible
        self.view_pdf_path = self.current_pdf_path

        # construit la liste des PDFs du même entry_id
        self.build_entry_pdf_group()
        self.show_doc_by_index(0)


        ##self.clear_fields()
        ##self.ocr_text_view.clear()
        ##self.load_saved_data()
        ##self.load_related_pdfs()

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

    # =========================
    # OCR
    # =========================
    def analyze_pdf(self):

        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        try:
            text = extract_text_from_pdf(self.current_pdf_path)
            self.ocr_text_view.setPlainText(text)

            data = parse_invoice(text)

            self.fill_fields(data)
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


            QMessageBox.information(self, "OCR terminé", "Analyse OCR terminée.\nVous pouvez corriger les champs.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur OCR", str(e))

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
            dossier_le, _ = self._get_row_widgets(r)
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
                dossier_le, _ = self._get_row_widgets(r)
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
            return

        if field_key == "bic":
            self.bic_input.setText(text.replace(" ", "").upper())
            self.bic_input.setStyleSheet("background-color: #e6ffe6;")
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
    def save_current_data(self):
        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        folders = self.get_folder_rows()

        data = {
            "iban": self.iban_input.text().strip(),
            "bic": self.bic_input.text().strip(),
            "invoice_date": self.date_input.text().strip(),
            "invoice_number": self.invoice_number_input.text().strip(),
            "folders": folders,
            "folder_number": folders[0]["tour_nr"] if folders else "",
            "vat_lines": self.get_vat_rows(),
        }

        base_name = os.path.splitext(os.path.basename(self.current_pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        os.makedirs(model_dir, exist_ok=True)
        json_path = os.path.join(model_dir, f"{base_name}.json")

        try:
            existing = {}
            if os.path.exists(json_path):
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        existing = json.load(f) or {}
                except Exception:
                    existing = {}

            existing.update(data)  # garde pallet_details / block_options si déjà présents

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)

            QMessageBox.information(self, "Sauvegarde", "Données sauvegardées avec succès.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur sauvegarde", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Erreur sauvegarde", str(e))

    def load_saved_data(self):
        if not self.current_pdf_path:
            return

        base_name = os.path.splitext(os.path.basename(self.current_pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        json_path = os.path.join(model_dir, f"{base_name}.json")
        if not os.path.exists(json_path):
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.pallet_details = data.get("pallet_details", {}) or {}
            self.block_options = data.get("block_options", {}) or {}
            self.iban_input.setText(data.get("iban", ""))
            self.bic_input.setText(data.get("bic", ""))
            self.date_input.setText(data.get("invoice_date", ""))
            self.invoice_number_input.setText(data.get("invoice_number", ""))
            self.vat_table.setRowCount(0)
            vat_lines = data.get("vat_lines", [])
            if isinstance(vat_lines, list):
                for r in vat_lines:
                    self._add_vat_row(r.get("rate", ""), r.get("base", ""), r.get("vat", ""))

            self._ensure_empty_vat_row()
            self.update_vat_total()

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

    # =========================
    # OCR batch
    # =========================
    def ocr_all_pdfs(self):
        total = self.pdf_table.rowCount()
        if total == 0:
            QMessageBox.warning(self, "OCR", "Aucun PDF à traiter.")
            return

        progress = QProgressDialog("OCR en cours…", "Annuler", 0, total, self)
        progress.setWindowTitle("OCR batch")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        processed = skipped = errors = 0

        for row in range(total):
            if progress.wasCanceled():
                break

            item = self.pdf_table.item(row, 0)
            if not item:
                continue

            pdf_path = item.data(Qt.UserRole)
            if not pdf_path or not os.path.exists(pdf_path):
                errors += 1
                continue

            if self._model_exists_for_pdf(pdf_path):
                skipped += 1
                progress.setValue(row + 1)
                QApplication.processEvents()
                continue

            try:
                progress.setLabelText(f"OCR en cours : {os.path.basename(pdf_path)}")
                text = extract_text_from_pdf(pdf_path)
                data = parse_invoice(text)
                self._save_data_for_pdf(pdf_path, data)
                processed += 1
            except Exception as e:
                print(f"OCR erreur sur {pdf_path} :", e)
                errors += 1

            progress.setValue(row + 1)
            QApplication.processEvents()

        progress.close()

        QMessageBox.information(
            self,
            "OCR terminé",
            f"OCR terminé.\nNouveaux OCR : {processed}\nDéjà traités : {skipped}\nErreurs : {errors}"
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

    # =========================
    # Supplier model
    # =========================
    def save_supplier_model(self):
        ocr_text = self.ocr_text_view.toPlainText() or ""

        # 1) récupérer IBAN/BIC robustes depuis l’OCR (validation + scoring)
        best = extract_best_bank_ids(
            ocr_text,
            prefer_iban=self.iban_input.text().strip(),
            prefer_bic=self.bic_input.text().strip(),
        )

        iban = best.get("iban") or self.iban_input.text().strip()
        bic  = best.get("bic")  or self.bic_input.text().strip()

        if iban:
            self.iban_input.setText(iban)
        if bic:
            self.bic_input.setText(bic)

        supplier_key = build_supplier_key(iban, bic)
        if not supplier_key:
            QMessageBox.warning(
                self,
                "Modèle fournisseur",
                "Impossible de sauvegarder : IBAN/BIC non fiables.\n"
                "Corrige IBAN/BIC puis réessaie."
            )
            return

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

        # 4) construire data (TOUJOURS défini)
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
            QMessageBox.information(self, "Modèle fournisseur", "Modèle fournisseur sauvegardé / mis à jour.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

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

        if not self.invoice_number_input.text().strip():
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
                dossier_le, _ = self._get_row_widgets(0)
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
        self.lbl_page_info.setText(f"{current} / {total}")
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
                return

            kundennr = record.get("KundenNr") or record.get("kundennr") or ""
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
                f"Ville : {record.get('Ort', '')}\n"
                f"Pays : {record.get('LKZ', '')}"
            )
            self.transporter_info.setPlainText(text)

            self.enable_transporter_update()

        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement transporteur :\n{e}")


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

            self.transporter_info.setPlainText(
                "🧾 Tour trouvée\n"
                f"TourNr : {record.get('TourNr', '')}"
            )
        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement tour :\n{e}")

    def on_related_pdf_context_menu(self, pos):
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
        return dossier_le, amount_le    
    
    def _add_folder_row(self, dossier: str = "", amount: str = ""):
        row = self.folder_table.rowCount()
        self.folder_table.insertRow(row)

        dossier_le = self._make_folder_cell("Numéro de dossier")
        amount_le = self._make_folder_cell("Montant HT (OCR)")

        dossier_le.setText("" if dossier is None else str(dossier))
        amount_le.setText("" if amount is None else str(amount))

        # Focus => champ actif (pour PDF -> champ)
        dossier_le.mousePressEvent = lambda e, f=dossier_le: self.set_active_field(f)
        amount_le.mousePressEvent = lambda e, f=amount_le: self.set_active_field(f)

        # Change => calcul + ligne vide + volet info
        dossier_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        amount_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))

        self.folder_table.setCellWidget(row, 0, dossier_le)
        self.folder_table.setCellWidget(row, 1, amount_le)

        # 1er calcul
        self._update_folder_row_status(row)


    def _ensure_empty_folder_row(self):
        # si aucune ligne -> en créer une vide
        if self.folder_table.rowCount() == 0:
            self._add_folder_row("", "")
            return

        last = self.folder_table.rowCount() - 1
        dossier_le, amount_le = self._get_row_widgets(last)
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
        dossier_le, _ = self._get_row_widgets(row)
        if self.active_field == dossier_le:
            self.load_tour_information(dossier_le.text())

    def get_folder_rows(self):
        rows = []
        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le = self._get_row_widgets(r)
            dossier = (dossier_le.text() if dossier_le else "").strip()
            amount = (amount_le.text() if amount_le else "").strip()
            # ignorer la ligne totalement vide (celle du bas)
            if dossier or amount:
                rows.append({"tour_nr": dossier, "amount_ht_ocr": amount})
        return rows
    
    def _update_folder_row_status(self, row: int):
        dossier_le, amount_le = self._get_row_widgets(row)
        if not dossier_le or not amount_le:
            return

        tour_nr = dossier_le.text().strip()
        amount_ocr = self._parse_amount(amount_le.text())

        dossier_le.setStyleSheet("")
        amount_le.setStyleSheet("")
        amount_le.setToolTip("")

        # ligne vide => neutre
        if not tour_nr:
            return

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
        total = 0.0
        has_any = False
        for r in range(self.vat_table.rowCount()):
            _, _, vat_le = self._get_vat_row_widgets(r)
            vat_txt = (vat_le.text() if vat_le else "").strip()
            v = self._parse_amount(vat_txt)
            if v is not None:
                total += v
                has_any = True

        if not has_any:
            self.lbl_vat_total.setText("")
            self.lbl_vat_total.setStyleSheet("padding:4px;")
            return

        self.lbl_vat_total.setText(f"Total TVA = {total:.2f}")
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
            if not os.name.lower().endswith(".pdf"):
                return
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
            self.lbl_doc_info.setText("0 / 0")
            self.btn_prev_doc.setEnabled(False)
            self.btn_next_doc.setEnabled(False)
            return

        self.lbl_doc_info.setText(f"{self.current_doc_index + 1} / {total}")
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