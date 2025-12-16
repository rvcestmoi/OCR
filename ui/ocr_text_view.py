from PySide6.QtWidgets import QPlainTextEdit, QMenu
from PySide6.QtCore import Signal
from ocr.field_detector import guess_field





class OcrTextView(QPlainTextEdit):
    assign_to_field = Signal(str, str)  
    # (texte sélectionné, nom du champ)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)

    def contextMenuEvent(self, event):
        cursor = self.textCursor()
        selected_text = cursor.selectedText().strip()

        if not selected_text:
            return  # ❌ on n'appelle PAS le menu standard

        menu = QMenu(self)

        actions = {
            "IBAN": "iban",
            "BIC": "bic",
            "Date facture": "date",
            "N° facture": "invoice_number",
            "N° dossier": "folder_number",
        }

        from ocr.field_detector import guess_field
        suggested = guess_field(selected_text)

        for label, field_key in actions.items():
            prefix = "⭐ " if field_key == suggested else ""
            action = menu.addAction(f"{prefix}Remplir : {label}")
            action.triggered.connect(
                lambda _, t=selected_text, k=field_key:
                self.assign_to_field.emit(t, k)
            )

        menu.exec(event.globalPos())

