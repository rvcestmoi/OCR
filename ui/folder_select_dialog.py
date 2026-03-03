# ui/folder_select_dialog.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialogButtonBox, QLabel
)

class FolderSelectDialog(QDialog):
    """Popup de sélection d'un dossier (TourNr) à partir d'une liste fournie."""

    def __init__(self, folders: list[dict], parent=None, title="Rattacher CMR à un dossier"):
        super().__init__(parent)
        self.selected_tour_nr: str | None = None
        self.setWindowTitle(title)
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sélectionne le dossier (TourNr) :"))

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["TourNr", "Montant HT (OCR)"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)

        for f in folders:
            tournr = str(f.get("tour_nr") or "").strip()
            amount = str(f.get("amount_ht_ocr") or "").strip()
            if not tournr:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(tournr))
            self.table.setItem(row, 1, QTableWidgetItem(amount))

        if self.table.rowCount():
            self.table.selectRow(0)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        self.table.cellDoubleClicked.connect(lambda *_: self.accept_selected())
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)

    def accept_selected(self):
        r = self.table.currentRow()
        if r < 0:
            return
        it = self.table.item(r, 0)
        tournr = (it.text() if it else "").strip()
        if not tournr:
            return
        self.selected_tour_nr = tournr
        self.accept()