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
    QApplication, QSplitter, QCompleter
)

from ui.ocr_text_view import OcrTextView
from ui.pdf_viewer import PdfViewer
from ocr.ocr_engine import extract_text_from_pdf
from ocr.invoice_parser import parse_invoice
from ocr.supplier_model import build_supplier_key, load_supplier_model


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

        pdf_nav = QHBoxLayout()
        self.btn_prev_page = QPushButton("⏮")
        self.btn_next_page = QPushButton("⏭")
        self.lbl_page_info = QLabel("0 / 0")

        self.btn_prev_page.clicked.connect(self.on_prev_page)
        self.btn_next_page.clicked.connect(self.on_next_page)

        pdf_nav.addStretch()
        pdf_nav.addWidget(self.btn_prev_page)
        pdf_nav.addWidget(self.lbl_page_info)
        pdf_nav.addWidget(self.btn_next_page)
        pdf_nav.addStretch()
        center_panel.addLayout(pdf_nav)

        self.pdf_viewer = PdfViewer()
        self.pdf_viewer.setMinimumSize(400, 400)
        self.pdf_viewer.text_selected.connect(self.fill_active_field)
        self.pdf_viewer.text_selected.connect(self.append_ocr_text)
        center_panel.addWidget(self.pdf_viewer)

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
        # (tu peux remettre False si tu veux un vrai contrôle d'état)
        # self.btn_transporter_action.setEnabled(False)

        transporter_layout = QHBoxLayout()
        transporter_layout.addWidget(self.transporter_input)
        transporter_layout.addWidget(self.btn_transporter_action)
        transporter_layout.addStretch()
        form_layout.addRow("Transporteur :", transporter_layout)

        form_layout.addRow("Date facture :", self.date_input)
        form_layout.addRow("N° facture :", self.invoice_number_input)

        # =========================
        # Gestion multi-dossiers
        # =========================
        self.folder_inputs: list[tuple[QWidget, QLineEdit]] = []

        self.folder_container = QVBoxLayout()
        self.folder_container.setSpacing(5)

        self.btn_add_folder = QPushButton("➕")
        self.btn_add_folder.setFixedWidth(30)
        self.btn_add_folder.clicked.connect(self.add_folder_field)

        folder_main_layout = QHBoxLayout()
        folder_main_layout.addLayout(self.folder_container)
        folder_main_layout.addWidget(self.btn_add_folder)
        folder_main_layout.addStretch()

        form_layout.addRow("N° dossier :", folder_main_layout)
                # =========================
        # Gestion champ actif (PDF -> champ)
        # =========================
        self.FIELD_COLORS = {
            self.iban_input: QColor(100, 149, 237, 80),           # bleu
            self.bic_input: QColor(186, 85, 211, 80),             # violet
            self.date_input: QColor(60, 179, 113, 80),            # vert
            self.invoice_number_input: QColor(255, 215, 0, 80),   # jaune
            # ⚠️ les champs dossier sont dynamiques : gérés à la création
        }


        # premier champ par défaut
        self.add_folder_field()

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

        main_layout.addLayout(center_panel, 5)
        main_layout.addLayout(right_panel, 3)


        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.mousePressEvent = lambda e, f=field: self.set_active_field(f)
            field.textChanged.connect(lambda _, f=field: f.setStyleSheet(""))

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

    # =========================
    # Active field / PDF selection
    # =========================
    def set_active_field(self, field):
        self.active_field = field

        self.pdf_viewer.active_field = field
        self.pdf_viewer.field_colors = self.FIELD_COLORS

        field.setStyleSheet("background-color: #fff3cd;")

        # ✅ Volet info selon champ actif
        if field in (self.iban_input, self.bic_input, self.transporter_input):
            self.load_transporter_information()
            return

        # Champs dossier (dynamiques)
        for _, le in getattr(self, "folder_inputs", []):
            if field == le:
                self.load_tour_information(le.text())
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
        return [le for _, le in self.folder_inputs]

    def clear_folder_fields(self, keep_one: bool = True):
        for container, _ in self.folder_inputs:
            container.deleteLater()
        self.folder_inputs.clear()
        if keep_one:
            self.add_folder_field()

    def get_folder_numbers(self) -> list[str]:
        out: list[str] = []
        for _, le in self.folder_inputs:
            val = le.text().strip()
            if val:
                out.append(val)
        return out

    def add_folder_field(self, value=""):
        line_edit = QLineEdit()
        line_edit.setPlaceholderText("Numéro de dossier")

        # ✅ sécurisation : value peut être bool / None / int
        if value is None or value is False:
            value = ""
        value = str(value)

        line_edit.setText(value)
        line_edit.mousePressEvent = lambda e, f=line_edit: self.set_active_field(f)
        line_edit.textChanged.connect(lambda _=None, le=line_edit: self.on_folder_changed(le))

        btn_remove = QPushButton("❌")
        btn_remove.setFixedWidth(30)

        row_layout = QHBoxLayout()
        row_layout.addWidget(line_edit)
        row_layout.addWidget(btn_remove)

        container_widget = QWidget()
        container_widget.setLayout(row_layout)

        self.folder_container.addWidget(container_widget)
        self.folder_inputs.append((container_widget, line_edit))

        btn_remove.clicked.connect(lambda: self.remove_folder_field(container_widget))

    def on_folder_changed(self, line_edit: QLineEdit):
        # Si on est en train d’éditer ce champ dossier, on refresh le volet info tour
        if self.active_field == line_edit:
            self.load_tour_information(line_edit.text())


    def remove_folder_field(self, widget):
        if len(self.folder_inputs) <= 1:
            return  # on garde toujours au moins 1 champ

        for i, (container, line_edit) in enumerate(self.folder_inputs):
            if container == widget:
                self.folder_container.removeWidget(container)
                container.deleteLater()
                self.folder_inputs.pop(i)
                break

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

        self.pdf_viewer.clear_highlights()
        self.display_pdf()

        self.clear_fields()
        self.ocr_text_view.clear()
        self.load_saved_data()
        self.update_page_indicator()
        self.load_related_pdfs()

    def display_pdf(self):
        if not self.current_pdf_path or not os.path.exists(self.current_pdf_path):
            return

        try:
            doc = fitz.open(self.current_pdf_path)
            pixmaps = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                pixmaps.append(QPixmap.fromImage(img))
            self.pdf_viewer.set_pages(pixmaps)
            self.update_page_indicator()
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

            QMessageBox.information(self, "OCR terminé", "Analyse OCR terminée.\nVous pouvez corriger les champs.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur OCR", str(e))

    def fill_fields(self, data):
        self.iban_input.setText(data.iban or "")
        self.bic_input.setText(data.bic or "")
        self.date_input.setText(data.invoice_date or "")
        self.invoice_number_input.setText(data.invoice_number or "")

        # Multi dossiers
        self.clear_folder_fields(keep_one=False)
        if getattr(data, "folder_numbers", None):
            # si ton parse_invoice renvoie déjà une liste
            for n in (data.folder_numbers or []):
                self.add_folder_field(n)
        else:
            # compat : un seul folder_number
            if data.folder_number:
                self.add_folder_field(data.folder_number)
            else:
                self.add_folder_field()

    def highlight_missing_fields(self):
        fields = [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]
        for field in fields:
            if field in (self.iban_input, self.bic_input) and self.bank_valid is not None:
                continue
            field.setStyleSheet("background-color: #ffe6e6;" if not field.text().strip() else "background-color: #e6ffe6;")

        # dossiers : au moins 1 rempli
        folders = self.get_folder_numbers()
        for _, le in self.folder_inputs:
            if folders:
                le.setStyleSheet("background-color: #e6ffe6;" if le.text().strip() else "")
            else:
                le.setStyleSheet("background-color: #ffe6e6;")

    def clear_fields(self):
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.clear()
            field.setStyleSheet("")
        self.clear_folder_fields(keep_one=True)

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
            # on remplit le 1er champ vide sinon on en crée un nouveau
            for _, le in self.folder_inputs:
                if not le.text().strip():
                    le.setText(dossier)
                    le.setStyleSheet("background-color: #e6ffe6;")
                    return
            self.add_folder_field(dossier)
            self.folder_inputs[-1][1].setStyleSheet("background-color: #e6ffe6;")
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

        data = {
            "iban": self.iban_input.text().strip(),
            "bic": self.bic_input.text().strip(),
            "invoice_date": self.date_input.text().strip(),
            "invoice_number": self.invoice_number_input.text().strip(),
            "folder_numbers": self.get_folder_numbers(),
        }

        base_name = os.path.splitext(os.path.basename(self.current_pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        os.makedirs(model_dir, exist_ok=True)
        json_path = os.path.join(model_dir, f"{base_name}.json")

        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Sauvegarde", "Données sauvegardées avec succès.")
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

            self.iban_input.setText(data.get("iban", ""))
            self.bic_input.setText(data.get("bic", ""))
            self.date_input.setText(data.get("invoice_date", ""))
            self.invoice_number_input.setText(data.get("invoice_number", ""))

            # compat : ancien format "folder_number"
            folders = data.get("folder_numbers", None)
            if folders is None:
                one = data.get("folder_number", "")
                folders = [one] if one else []

            self.clear_folder_fields(keep_one=False)
            if folders:
                for n in folders:
                    self.add_folder_field(n)
            else:
                self.add_folder_field()

        except Exception as e:
            QMessageBox.warning(self, "Erreur chargement", str(e))

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
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()

        supplier_key = build_supplier_key(iban, bic)
        if not supplier_key:
            QMessageBox.warning(self, "Modèle fournisseur", "IBAN et BIC requis pour créer un modèle fournisseur.")
            return

        supplier_dir = r"C:\git\OCR\OCR\models\suppliers"
        os.makedirs(supplier_dir, exist_ok=True)
        model_path = os.path.join(supplier_dir, f"{supplier_key}.json")

        data = {
            "supplier_key": supplier_key,
            "iban": iban,
            "bic": bic,
            "invoice_number_example": self.invoice_number_input.text().strip(),
            "date_example": self.date_input.text().strip(),
            # on garde aussi un exemple de dossier (1er non vide)
            "folder_number_example": (self.get_folder_numbers()[0] if self.get_folder_numbers() else "")
        }

        try:
            with open(model_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Modèle fournisseur", "Modèle fournisseur sauvegardé / mis à jour.")
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))

    def apply_supplier_model(self, model: dict):
        if not model:
            return

        if not self.invoice_number_input.text().strip():
            self.invoice_number_input.setText(model.get("invoice_number_example", ""))

        if not self.date_input.text().strip():
            self.date_input.setText(model.get("date_example", ""))

        # Remplir un dossier par défaut uniquement si aucun saisi
        if not self.get_folder_numbers():
            example = model.get("folder_number_example", "")
            if example:
                self.folder_inputs[0][1].setText(example)

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

        self.current_pdf_path = path
        self.display_pdf()
        self.clear_fields()
        self.ocr_text_view.clear()
        self.load_saved_data()

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

    def load_transporter_information(self):
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()

        self.transporter_info.clear()
        if not iban or not bic:
            return

        try:
            record = self.transporter_repo.find_transporter_by_bank(iban, bic)
            if not record:
                self.transporter_info.setPlainText("❌ Transporteur non trouvé en base.")
                return

            kundennr = record.get("KundenNr") or record.get("kundennr") or ""
            name = record.get("name1", "")

            self.selected_kundennr = str(kundennr) if kundennr is not None else None
            self.transporter_input.setText(f"{name} ({kundennr})")

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

            # laisse enable_transporter_update décider si flèche active (modif réelle)
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

        try:
            record = self.transporter_repo.find_tour_by_tournr(tour_nr)
            if not record:
                self.transporter_info.setPlainText(f"❌ Tour non trouvée : {tour_nr}")
                return

            self.transporter_info.setPlainText(f"🧾 Tour trouvée\nTourNr : {record.get('TourNr', '')}")

        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement tour :\n{e}")


    def load_tour_information(self, tour_nr: str):
        self.transporter_info.clear()

        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            self.transporter_info.setPlainText("ℹ️ Aucun numéro de dossier.")
            return

        try:
            record = self.tour_repo.find_by_tournr(tour_nr)

            if not record:
                self.transporter_info.setPlainText(f"❌ Tour non trouvée : {tour_nr}")
                return

            self.transporter_info.setPlainText(
                f"🧾 Tour trouvée\n"
                f"TourNr : {record.get('TourNr', '')}"
            )

        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement tour :\n{e}")
