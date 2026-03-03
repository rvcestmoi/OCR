# ui/geb_search_dialog.py
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QDialogButtonBox, QLabel
)

class GebSearchDialog(QDialog):
    def __init__(self, geb_repo, parent=None):
        super().__init__(parent)
        self.geb_repo = geb_repo
        self.selected = None  # {"gebnr": "...", "bez": "..."}

        self.setWindowTitle("Ajouter un frais (XXAGeb)")
        self.resize(700, 450)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Recherche (GebNr ou désignation) :"))
        self.search_le = QLineEdit()
        self.search_le.setPlaceholderText("Tape pour filtrer…")
        layout.addWidget(self.search_le)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["GebNr", "Désignation"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        self.search_le.textChanged.connect(self.refresh)
        self.table.cellDoubleClicked.connect(lambda *_: self.accept_selected())
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)

        self.refresh("")

    def refresh(self, term: str):
        rows = self.geb_repo.search_gebs(term) or []
        self.table.setRowCount(0)

        for r in rows:
            gebnr = str(r.get("GebNr") or r.get("gebnr") or "").strip()
            bez = str(r.get("Bez") or r.get("bez") or "").strip()
            if not gebnr and not bez:
                continue

            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(gebnr))
            self.table.setItem(row, 1, QTableWidgetItem(bez))

        if self.table.rowCount() > 0:
            self.table.selectRow(0)

    def accept_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        gebnr = (self.table.item(row, 0).text() if self.table.item(row, 0) else "").strip()
        bez = (self.table.item(row, 1).text() if self.table.item(row, 1) else "").strip()
        if not gebnr:
            return
        self.selected = {"gebnr": gebnr, "bez": bez}
        self.accept()