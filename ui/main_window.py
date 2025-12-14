from PySide6.QtWidgets import (
    QMainWindow, QWidget, QListWidget, QPushButton,
    QVBoxLayout, QHBoxLayout, QFileDialog, QLabel,
    QLineEdit, QFormLayout, QMessageBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

import os
import fitz  # PyMuPDF
from ocr.ocr_engine import extract_text_from_pdf
from ocr.invoice_parser import parse_invoice


# (prévu pour plus tard)
# from ocr.ocr_engine import extract_text_from_pdf
# from ocr.invoice_parser import parse_invoice


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("OCR Factures Fournisseurs")
        self.resize(1200, 800)

        self.current_pdf_path = None

        # Widget central
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)

        # =========================
        # Panneau gauche : PDF list
        # =========================
        left_panel = QVBoxLayout()

        self.btn_scan_folder = QPushButton("📂 Analyser un dossier")
        self.btn_scan_folder.clicked.connect(self.select_folder)

        # Liste des PDF (on pourra passer à un tableau QTableWidget ensuite)
        self.pdf_list = QListWidget()
        self.pdf_list.itemClicked.connect(self.on_pdf_selected)

        left_panel.addWidget(self.btn_scan_folder)
        left_panel.addWidget(self.pdf_list)

        # =========================
        # Panneau central : PDF view
        # =========================
        center_panel = QVBoxLayout()

        self.pdf_label = QLabel()
        self.pdf_label.setAlignment(Qt.AlignCenter)
        self.pdf_label.setMinimumSize(400, 400)
        self.pdf_label.setStyleSheet("border: 1px solid #999; background: #fff;")
        self.pdf_label.setText("Aucun PDF sélectionné")

        center_panel.addWidget(self.pdf_label)

        # =========================
        # Panneau droit : Infos OCR
        # =========================
        right_panel = QVBoxLayout()

        form_layout = QFormLayout()

        self.iban_input = QLineEdit()
        self.bic_input = QLineEdit()
        self.date_input = QLineEdit()
        self.invoice_number_input = QLineEdit()
        self.folder_number_input = QLineEdit()

        # Champs éditables (même si OCR trouvé)
        form_layout.addRow("IBAN :", self.iban_input)
        form_layout.addRow("BIC :", self.bic_input)
        form_layout.addRow("Date facture :", self.date_input)
        form_layout.addRow("N° facture :", self.invoice_number_input)
        form_layout.addRow("N° dossier :", self.folder_number_input)
        for field in [
            self.iban_input,
            self.bic_input,
            self.date_input,
            self.invoice_number_input,
            self.folder_number_input
        ]:
            field.textChanged.connect(
                lambda _, f=field: f.setStyleSheet("")
    )


        self.btn_analyze_pdf = QPushButton("🔍 Analyser le PDF (OCR)")
        self.btn_analyze_pdf.clicked.connect(self.analyze_pdf)

        right_panel.addLayout(form_layout)
        right_panel.addStretch()
        right_panel.addWidget(self.btn_analyze_pdf)

        # =========================
        # Ajout des panneaux
        # =========================
        main_layout.addLayout(left_panel, 2)
        main_layout.addLayout(center_panel, 5)
        main_layout.addLayout(right_panel, 3)

    # =========================
    # Actions
    # =========================

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Sélectionner un dossier")
        if not folder:
            return

        self.pdf_list.clear()
        self.current_pdf_path = None
        self.pdf_label.setText("Aucun PDF sélectionné")
        self.clear_fields()

        # Ajoute tous les PDF du dossier
        for file in sorted(os.listdir(folder)):
            if file.lower().endswith(".pdf"):
                self.pdf_list.addItem(os.path.join(folder, file))

    def on_pdf_selected(self, item):
        self.current_pdf_path = item.text()
        self.display_pdf()
        self.clear_fields()

    def display_pdf(self):
        """Affiche la première page du PDF sélectionné dans le panneau central."""
        if not self.current_pdf_path or not os.path.exists(self.current_pdf_path):
            self.pdf_label.setText("Aucun PDF sélectionné")
            return

        try:
            doc = fitz.open(self.current_pdf_path)
            if doc.page_count == 0:
                self.pdf_label.setText("PDF vide")
                doc.close()
                return

            page = doc.load_page(0)  # première page
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # zoom x2

            # NOTE : get_pixmap renvoie généralement du RGB
            img = QImage(
                pix.samples,
                pix.width,
                pix.height,
                pix.stride,
                QImage.Format_RGB888
            )

            pixmap = QPixmap.fromImage(img)

            self.pdf_label.setPixmap(
                pixmap.scaled(
                    self.pdf_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
            )

            doc.close()

        except Exception as e:
            self.pdf_label.setText(f"Erreur affichage PDF\n{e}")

    def clear_fields(self):
        self.iban_input.clear()
        self.bic_input.clear()
        self.date_input.clear()
        self.invoice_number_input.clear()
        self.folder_number_input.clear()

    def analyze_pdf(self):
        if not self.current_pdf_path:
            QMessageBox.warning(self, "Erreur", "Aucun PDF sélectionné.")
            return

        try:
            # 1️⃣ OCR
            text = extract_text_from_pdf(self.current_pdf_path)

            # 2️⃣ Parsing
            data = parse_invoice(text)

            # 3️⃣ Remplissage UI
            self.fill_fields(data)

            # 4️⃣ Mise en évidence champs manquants
            self.highlight_missing_fields()

            QMessageBox.information(
                self,
                "OCR terminé",
                "Analyse OCR terminée.\nVeuillez vérifier les champs."
            )

        except Exception as e:
            QMessageBox.critical(
                self,
                "Erreur OCR",
                f"Une erreur est survenue pendant l'OCR :\n{e}"
            )   


    def fill_fields(self, data):
        """Remplit les champs à droite (data = InvoiceData)."""
        self.iban_input.setText(getattr(data, "iban", "") or "")
        self.bic_input.setText(getattr(data, "bic", "") or "")
        self.date_input.setText(getattr(data, "invoice_date", "") or "")
        self.invoice_number_input.setText(getattr(data, "invoice_number", "") or "")
        self.folder_number_input.setText(getattr(data, "folder_number", "") or "")

    # Optionnel : si tu redimensionnes la fenêtre, on ré-adapte l’image
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # re-scale l'image à la taille du label si un pdf est chargé
        if self.current_pdf_path and self.pdf_label.pixmap() is not None:
            self.display_pdf()

    def highlight_missing_fields(self):
        """
        Met en rouge clair les champs non trouvés par l'OCR
        """
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


    def fill_fields(self, data):
        """
        Remplit les champs avec les données OCR
        """
        self.iban_input.setText(data.iban or "")
        self.bic_input.setText(data.bic or "")
        self.date_input.setText(data.invoice_date or "")
        self.invoice_number_input.setText(data.invoice_number or "")
        self.folder_number_input.setText(data.folder_number or "")  

