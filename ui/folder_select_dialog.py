# ui/folder_select_dialog.py
from __future__ import annotations

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QLabel, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QDialogButtonBox, QHeaderView
)

class FolderSelectDialog(QDialog):
    """Sélection d'un dossier (TourNr) avec détails palettes/poids/trajet."""

    def __init__(
        self,
        tour_numbers: list[str],
        details_rows: list[dict[str, Any]] | None = None,
        parent=None,
        title: str = "Rattacher CMR à un dossier",
    ):
        super().__init__(parent)
        self.selected_tour_nr: str | None = None

        self.setWindowTitle(title)
        self.resize(980, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sélectionne le dossier (TourNr). Détails palettes/poids/trajet :"))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Dossier", "Trajet", "VPE", "Palettes", "Poids"])
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        layout.addWidget(self.tree)

        details_rows = details_rows or []
        by_dossier = defaultdict(list)
        trajet_by_dossier: dict[str, str] = {}

        for r in details_rows:
            dossier = str(r.get("Dossier") or r.get("dossier") or "").strip()
            if not dossier:
                continue
            by_dossier[dossier].append(r)
            trajet = str(r.get("Trajet") or r.get("trajet") or "").strip()
            if trajet and dossier not in trajet_by_dossier:
                trajet_by_dossier[dossier] = trajet

        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]

        bold = QFont()
        bold.setBold(True)

        first_item = None

        for tour_nr in tour_numbers:
            rows = by_dossier.get(tour_nr, [])
            trajet = trajet_by_dossier.get(tour_nr, "")

            total_pal = 0.0
            total_poids = 0.0
            for rr in rows:
                try:
                    total_pal += float(rr.get("Palettes") or rr.get("palettes") or 0)
                except Exception:
                    pass
                try:
                    total_poids += float(rr.get("Poids") or rr.get("poids") or 0)
                except Exception:
                    pass

            parent_item = QTreeWidgetItem(
                [tour_nr, trajet, "TOTAL", self._fmt_num(total_pal), self._fmt_num(total_poids)]
            )
            parent_item.setData(0, Qt.UserRole, tour_nr)
            for c in range(5):
                parent_item.setFont(c, bold)

            self.tree.addTopLevelItem(parent_item)
            if first_item is None:
                first_item = parent_item

            if rows:
                for rr in rows:
                    vpe = str(rr.get("VPE") or rr.get("vpe") or "").strip()
                    pal = self._fmt_num(rr.get("Palettes") or rr.get("palettes") or "")
                    poids = self._fmt_num(rr.get("Poids") or rr.get("poids") or "")
                    child = QTreeWidgetItem(["", "", vpe, pal, poids])
                    child.setData(0, Qt.UserRole, tour_nr)  # important: sélection enfant => même dossier
                    parent_item.addChild(child)
            else:
                child = QTreeWidgetItem(["", "", "(aucun détail)", "", ""])
                child.setData(0, Qt.UserRole, tour_nr)
                parent_item.addChild(child)

            parent_item.setExpanded(True)

        if first_item is not None:
            self.tree.setCurrentItem(first_item)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        self.tree.itemDoubleClicked.connect(lambda *_: self.accept_selected())
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)

    @staticmethod
    def _fmt_num(v: Any) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        if s == "":
            return ""
        try:
            f = float(str(v).replace(",", "."))
            if abs(f - int(f)) < 1e-9:
                return str(int(f))
            return f"{f:.4f}".rstrip("0").rstrip(".")
        except Exception:
            return s

    def accept_selected(self):
        it = self.tree.currentItem()
        if not it:
            return
        tour_nr = str(it.data(0, Qt.UserRole) or "").strip()
        if not tour_nr:
            tour_nr = (it.text(0) or "").strip()
        if not tour_nr:
            return
        self.selected_tour_nr = tour_nr
        self.accept()