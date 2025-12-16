from PySide6.QtWidgets import (
    QMainWindow, QWidget, QListWidget, QPushButton,
    QVBoxLayout, QHBoxLayout, QFileDialog, QLabel,
    QLineEdit, QFormLayout, QMessageBox, QPlainTextEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from ui.ocr_text_view import OcrTextView
from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QPushButton, QLabel, QHBoxLayout
from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor, QTextDocument
from PySide6.QtWidgets import QTextEdit
from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
import re
import json
from PySide6.QtWidgets import QProgressDialog
from PySide6.QtWidgets import QApplication
from ocr.supplier_model import build_supplier_key, load_supplier_model







import os
import fitz  # PyMuPDF

from ocr.ocr_engine import extract_text_from_pdf
from ocr.invoice_parser import parse_invoice
from ui.pdf_viewer import PdfViewer
from PySide6.QtGui import QColor
import json


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("OCR Factures Fournisseurs")
        self.resize(1200, 800)

        self.current_pdf_path = None
        self.active_field = None  # champ sélectionné à droite

        # =========================
        # Widget central
        # =========================
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        # =========================
        # Panneau gauche : liste PDF
        # =========================
        left_panel = QVBoxLayout()

        self.btn_scan_folder = QPushButton("📂 Analyser un dossier")
        self.btn_scan_folder.clicked.connect(self.select_folder)

        self.btn_ocr_all = QPushButton("⚙️ OCRiser")
        self.btn_ocr_all.clicked.connect(self.ocr_all_pdfs)

        left_panel.addWidget(self.btn_ocr_all)



        left_panel.addWidget(self.btn_scan_folder)

        self.pdf_table = QTableWidget()
        self.pdf_table.setColumnCount(1)
        self.pdf_table.setHorizontalHeaderLabels(["Nom du fichier"])
        self.pdf_table.horizontalHeader().setStretchLastSection(True)
        self.pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pdf_table.setAlternatingRowColors(True)
        self.pdf_table.cellClicked.connect(self.on_pdf_selected)

        left_panel.addWidget(self.pdf_table)
        


        # =========================
        # Panneau central : PDF
        # =========================
        center_panel = QVBoxLayout()

        self.pdf_viewer = PdfViewer()
        self.pdf_viewer.setMinimumSize(400, 400)

        # (sera utilisé plus tard pour OCR par sélection)
        self.pdf_viewer.text_selected.connect(self.fill_active_field)
        self.pdf_viewer.text_selected.connect(self.append_ocr_text)


        center_panel.addWidget(self.pdf_viewer)

        # =========================
        # Panneau droit : infos OCR
        # =========================
        right_panel = QVBoxLayout()
        form_layout = QFormLayout()

        self.iban_input = QLineEdit()
        self.bic_input = QLineEdit()
        self.date_input = QLineEdit()
        self.invoice_number_input = QLineEdit()
        self.folder_number_input = QLineEdit()

        form_layout.addRow("IBAN :", self.iban_input)
        form_layout.addRow("BIC :", self.bic_input)
        form_layout.addRow("Date facture :", self.date_input)
        form_layout.addRow("N° facture :", self.invoice_number_input)
        form_layout.addRow("N° dossier :", self.folder_number_input)

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

        # =========================
        # Layout global
        # =========================
        main_layout.addLayout(left_panel, 2)
        main_layout.addLayout(center_panel, 5)
        main_layout.addLayout(right_panel, 3)

        # =========================
        # Gestion champ actif
        # =========================
        for field in [
            self.iban_input,
            self.bic_input,
            self.date_input,
            self.invoice_number_input,
            self.folder_number_input
        ]:
            field.mousePressEvent = lambda e, f=field: self.set_active_field(f)
            field.textChanged.connect(lambda _, f=field: f.setStyleSheet(""))

        self.FIELD_COLORS = {
            self.iban_input: QColor(100, 149, 237, 80),     # bleu
            self.bic_input: QColor(186, 85, 211, 80),       # violet
            self.date_input: QColor(60, 179, 113, 80),      # vert
            self.invoice_number_input: QColor(255, 215, 0, 80),  # jaune
            self.folder_number_input: QColor(255, 165, 0, 80),   # orange
        }

        # =========================
        # Recherche dans texte OCR
        # =========================
        self.ocr_search_input = QLineEdit()
        self.ocr_search_input.setPlaceholderText("🔍 Rechercher dans le texte OCR…")
        self.ocr_search_input.textChanged.connect(self.search_in_ocr_text)

        right_panel.addWidget(self.ocr_search_input)


        # =========================
        # Zone texte OCR brut
        # =========================
        self.ocr_text_view = OcrTextView()
        self.ocr_text_view.setPlaceholderText("Texte brut OCR (Tesseract / PDF)…")
        self.ocr_text_view.setMinimumHeight(200)

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
    # Actions UI
    # =========================

    def set_active_field(self, field):
        self.active_field = field

        self.pdf_viewer.active_field = field
        self.pdf_viewer.field_colors = self.FIELD_COLORS

        field.setStyleSheet("background-color: #fff3cd;")



    def fill_active_field(self, text):
        if not self.active_field:
            return

        value = text.strip()

        # Nettoyage spécifique par champ
        if self.active_field == self.invoice_number_input:
            # On garde uniquement les chiffres
            value = "".join(c for c in value if c.isdigit())

        elif self.active_field == self.folder_number_input:
            value = "".join(c for c in value if c.isdigit())

        elif self.active_field == self.iban_input:
            value = value.replace(" ", "").upper()

        elif self.active_field == self.bic_input:
            value = value.replace(" ", "").upper()

        self.active_field.setText(value)
        self.active_field.setStyleSheet("background-color: #e6ffe6;")



    def select_folder(self):
        DEFAULT_OCR_FOLDER = r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles"

        folder = QFileDialog.getExistingDirectory(
            self,
            "Sélectionner un dossier",
            DEFAULT_OCR_FOLDER
)
        if not folder:
            return
        self.pdf_table.setRowCount(0)

        pdf_files = [
            f for f in sorted(os.listdir(folder))
            if f.lower().endswith(".pdf")
        ]

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

    def display_pdf(self):
        if not self.current_pdf_path or not os.path.exists(self.current_pdf_path):
            return

        try:
            doc = fitz.open(self.current_pdf_path)
            page = doc.load_page(0)

            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format_RGB888
            )

            pixmap = QPixmap.fromImage(img)
            self.pdf_viewer.setPixmap(pixmap)


            doc.close()

        except Exception as e:
            QMessageBox.critical(self, "Erreur PDF", str(e))

    def analyze_pdf(self):
        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        try:
            text = extract_text_from_pdf(self.current_pdf_path)

            # 🆕 affichage texte brut OCR
            self.ocr_text_view.setPlainText(text)

            data = parse_invoice(text)


            self.fill_fields(data)

            from ocr.supplier_model import build_supplier_key, load_supplier_model

            iban = self.iban_input.text().strip()
            bic = self.bic_input.text().strip()

            supplier_key = build_supplier_key(iban, bic)
            print("SUPPLIER KEY:", supplier_key)

            model = load_supplier_model(supplier_key)

            if model:
                print("✅ Modèle fournisseur trouvé")
                self.apply_supplier_model(model)
            else:
                print("❌ Aucun modèle fournisseur")


            
            self.highlight_missing_fields()

            QMessageBox.information(
                self,
                "OCR terminé",
                "Analyse OCR terminée.\nVous pouvez corriger les champs."
            )

        except Exception as e:
            QMessageBox.critical(self, "Erreur OCR", str(e))

        

    # =========================
    # Helpers UI
    # =========================

    def fill_fields(self, data):
        self.iban_input.setText(data.iban or "")
        self.bic_input.setText(data.bic or "")
        self.date_input.setText(data.invoice_date or "")
        self.invoice_number_input.setText(data.invoice_number or "")
        self.folder_number_input.setText(data.folder_number or "")

    def highlight_missing_fields(self):
        fields = [
            self.iban_input,
            self.bic_input,
            self.date_input,
            self.invoice_number_input,
            self.folder_number_input
        ]

        for field in fields:
            if not field.text().strip():
                field.setStyleSheet("background-color: #ffe6e6;")
            else:
                field.setStyleSheet("background-color: #e6ffe6;")

    def clear_fields(self):
        for field in [
            self.iban_input,
            self.bic_input,
            self.date_input,
            self.invoice_number_input,
            self.folder_number_input
        ]:
            field.clear()
            field.setStyleSheet("")



    def save_model(self):
        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        model_data = {
            "iban": self.iban_input.text().strip(),
            "bic": self.bic_input.text().strip(),
            "invoice_date": self.date_input.text().strip(),
            "invoice_number": self.invoice_number_input.text().strip(),
            "folder_number": self.folder_number_input.text().strip(),
        }

        # Vérification minimale
        if not any(model_data.values()):
            QMessageBox.warning(
                self,
                "Rien à sauvegarder",
                "Aucune donnée n’a été renseignée."
            )
            return

        # Dossier modèles
        model_dir = r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles"
        os.makedirs(model_dir, exist_ok=True)

        pdf_name = os.path.splitext(os.path.basename(self.current_pdf_path))[0]
        model_path = os.path.join(model_dir, f"{pdf_name}_model.json")

        try:
            with open(model_path, "w", encoding="utf-8") as f:
                json.dump(model_data, f, indent=4, ensure_ascii=False)

            QMessageBox.information(
                self,
                "Modèle sauvegardé",
                f"Modèle enregistré :\n{model_path}"
            )

        except Exception as e:
            QMessageBox.critical(self, "Erreur sauvegarde", str(e))


    def append_ocr_text(self, text):
        if not text.strip():
            return

        current = self.ocr_text_view.toPlainText()
        self.ocr_text_view.setPlainText(
            current + "\n\n--- OCR sélection ---\n" + text
        )


    def assign_text_to_field(self, text: str, field_key: str):
        text = text.strip()

        mapping = {
            "iban": self.iban_input,
            "bic": self.bic_input,
            "date": self.date_input,
            "invoice_number": self.invoice_number_input,
            "folder_number": self.folder_number_input,
        }

        field = mapping.get(field_key)
        if not field:
            return


        if field_key == "invoice_number":
            text = re.sub(r"[^A-Z0-9\-_/\. ]", "", text.upper()).strip()

        elif field_key == "folder_number":
            text = re.sub(r"[^A-Z0-9\-_/\. ]", "", text.upper()).strip()


        field.setText(text)
        field.setStyleSheet("background-color: #e6ffe6;")


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
            fmt.setBackground(QColor("#fff59d"))  # jaune clair
            sel.format = fmt

            self.search_selections.append(sel)

        if not self.search_selections:
            self.search_counter_label.setText("0 / 0")
            return

        # Sélection active = première
        self.current_match_index = 0
        self._update_active_match()

        self.search_counter_label.setText(
            f"1 / {len(self.search_selections)}"
        )

    def goto_next_match(self):
        if not self.search_selections:
            return

        self.current_match_index = (
            self.current_match_index + 1
        ) % len(self.search_selections)

        self._update_active_match()


    def goto_previous_match(self):
        if not self.search_selections:
            return

        self.current_match_index = (
            self.current_match_index - 1
        ) % len(self.search_selections)

        self._update_active_match()

    def _update_active_match(self):
        editor = self.ocr_text_view

        updated = []

        for i, sel in enumerate(self.search_selections):
            fmt = QTextCharFormat()

            if i == self.current_match_index:
                fmt.setBackground(QColor("#ffcc80"))  # orange (actif)
                editor.setTextCursor(sel.cursor)
            else:
                fmt.setBackground(QColor("#fff59d"))  # jaune

            sel.format = fmt
            updated.append(sel)

        editor.setExtraSelections(updated)

        self.search_counter_label.setText(
            f"{self.current_match_index + 1} / {len(self.search_selections)}"
    )



    def save_current_data(self):
        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        data = {
            "iban": self.iban_input.text().strip(),
            "bic": self.bic_input.text().strip(),
            "invoice_date": self.date_input.text().strip(),
            "invoice_number": self.invoice_number_input.text().strip(),
            "folder_number": self.folder_number_input.text().strip(),
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
            return  # rien à charger

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.iban_input.setText(data.get("iban", ""))
            self.bic_input.setText(data.get("bic", ""))
            self.date_input.setText(data.get("invoice_date", ""))
            self.invoice_number_input.setText(data.get("invoice_number", ""))
            self.folder_number_input.setText(data.get("folder_number", ""))

        except Exception as e:
            QMessageBox.warning(self, "Erreur chargement", str(e))


    def ocr_all_pdfs(self):
        total = self.pdf_table.rowCount()
        if total == 0:
            QMessageBox.warning(self, "OCR", "Aucun PDF à traiter.")
            return

        progress = QProgressDialog(
            "OCR en cours…",
            "Annuler",
            0,
            total,
            self
        )
        progress.setWindowTitle("OCR batch")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        processed = 0
        skipped = 0
        errors = 0

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

            # ✅ SKIP si déjà OCRisé
            if self._model_exists_for_pdf(pdf_path):
                skipped += 1
                progress.setValue(row + 1)
                QApplication.processEvents()
                continue

            try:
                progress.setLabelText(
                    f"OCR en cours : {os.path.basename(pdf_path)}"
                )

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
            f"OCR terminé.\n"
            f"Nouveaux OCR : {processed}\n"
            f"Déjà traités : {skipped}\n"
            f"Erreurs : {errors}"
        )



    def _save_data_for_pdf(self, pdf_path, data):
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        os.makedirs(model_dir, exist_ok=True)

        json_path = os.path.join(model_dir, f"{base_name}.json")

        payload = {
            "iban": data.iban or "",
            "bic": data.bic or "",
            "invoice_date": data.invoice_date or "",
            "invoice_number": data.invoice_number or "",
            "folder_number": data.folder_number or "",
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


    def _model_exists_for_pdf(self, pdf_path):
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        json_path = os.path.join(model_dir, f"{base_name}.json")
        return os.path.exists(json_path)

   
    def save_supplier_model(self):
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()

        supplier_key = build_supplier_key(iban, bic)
        if not supplier_key:
            QMessageBox.warning(
                self,
                "Modèle fournisseur",
                "IBAN et BIC requis pour créer un modèle fournisseur."
            )
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
            "folder_number_example": self.folder_number_input.text().strip()
        }

        try:
            with open(model_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            QMessageBox.information(
                self,
                "Modèle fournisseur",
                "Modèle fournisseur sauvegardé / mis à jour."
            )

        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))


    def apply_supplier_model(self, model: dict):
        if not model:
            return

        # ⚠️ On ne remplace QUE les champs vides
        if not self.invoice_number_input.text().strip():
            self.invoice_number_input.setText(
                model.get("invoice_number_example", "")
            )

        if not self.date_input.text().strip():
            self.date_input.setText(
                model.get("date_example", "")
            )

        if not self.folder_number_input.text().strip():
            self.folder_number_input.setText(
                model.get("folder_number_example", "")
        )
