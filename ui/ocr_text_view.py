from PySide6.QtWidgets import QPlainTextEdit, QMenu
from PySide6.QtCore import Signal


class OcrTextView(QPlainTextEdit):
    assign_to_field = Signal(str, str)  
    # (texte sélectionné, nom du champ)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)

    def contextMenuEvent(self, event):
        cursor = self.textCursor()
        selected_text = cursor.selectedText().strip()

        # Menu standard si rien sélectionné
        if not selected_text:
            super().contextMenuEvent(event)
            return

        menu = QMenu(self)

        actions = {
            "IBAN": "iban",
            "BIC": "bic",
            "Date facture": "date",
            "N° facture": "invoice_number",
            "N° dossier": "folder_number",
        }

        for label, field_key in actions.items():
            action = menu.addAction(f"➡ Remplir : {label}")
            action.triggered.connect(
                lambda _, t=selected_text, k=field_key:
                self.assign_to_field.emit(t, k)
            )

        menu.exec(event.globalPos())
