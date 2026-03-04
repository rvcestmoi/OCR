# ui/folder_select_dialog.py
from __future__ import annotations

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QLabel, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QDialogButtonBox, QHeaderView, QMessageBox
)

ROLE_TOUR = Qt.UserRole
ROLE_AUF  = Qt.UserRole + 1

class FolderSelectDialog(QDialog):
    """Sélection d'une commande (AufNr) dans un dossier (TourNr) avec détails palettes/poids/trajet."""

    def __init__(
        self,
        tour_numbers: list[str],
        details_rows: list[dict[str, Any]] | None = None,
        parent=None,
        title: str = "Rattacher CMR à une commande",
    ):
        super().__init__(parent)
        self.selected_tour_nr: str | None = None
        self.selected_auf_nr: str | None = None

        self.setWindowTitle(title)
        self.resize(1050, 560)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sélectionne une COMMANDE (AufNr) à rattacher. 1 CMR = 1 commande."))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Dossier", "Commande", "Trajet", "VPE", "Palettes", "Poids"])
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)

        layout.addWidget(self.tree)

        details_rows = details_rows or []
        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]

        by_tour_auf = defaultdict(list)          # (tour, auf) -> rows
        trajet_by_tour = {}                      # tour -> trajet

        for r in details_rows:
            tour = str(r.get("Dossier") or "").strip()
            auf  = str(r.get("AufNr") or "").strip()
            if not tour:
                continue
            if tour not in trajet_by_tour:
                tr = str(r.get("Trajet") or "").strip()
                if tr:
                    trajet_by_tour[tour] = tr
            if auf:
                by_tour_auf[(tour, auf)].append(r)

        bold = QFont()
        bold.setBold(True)

        first_item = None

        for tour in tour_numbers:
            trajet = trajet_by_tour.get(tour, "")

            # noeud Dossier
            tour_item = QTreeWidgetItem([tour, "", trajet, "TOTAL", "", ""])
            tour_item.setData(0, ROLE_TOUR, tour)
            tour_item.setData(0, ROLE_AUF, "")
            for c in range(6):
                tour_item.setFont(c, bold)

            self.tree.addTopLevelItem(tour_item)
            if first_item is None:
                first_item = tour_item

            # commandes dans ce dossier
            aufnrs = sorted({auf for (t, auf) in by_tour_auf.keys() if t == tour})
            if not aufnrs:
                child = QTreeWidgetItem(["", "(aucune commande trouvée)", "", "", "", ""])
                child.setData(0, ROLE_TOUR, tour)
                child.setData(0, ROLE_AUF, "")
                tour_item.addChild(child)
                tour_item.setExpanded(True)
                continue

            for auf in aufnrs:
                rows = by_tour_auf.get((tour, auf), [])

                total_pal = 0.0
                total_poids = 0.0
                for rr in rows:
                    try: total_pal += float(rr.get("Palettes") or 0)
                    except Exception: pass
                    try: total_poids += float(rr.get("Poids") or 0)
                    except Exception: pass

                auf_item = QTreeWidgetItem(["", auf, "", "TOTAL", self._fmt_num(total_pal), self._fmt_num(total_poids)])
                auf_item.setData(0, ROLE_TOUR, tour)
                auf_item.setData(0, ROLE_AUF, auf)
                for c in range(6):
                    auf_item.setFont(c, bold)

                tour_item.addChild(auf_item)

                # VPE lignes
                for rr in rows:
                    vpe = str(rr.get("VPE") or "").strip()
                    pal = self._fmt_num(rr.get("Palettes") or "")
                    poids = self._fmt_num(rr.get("Poids") or "")
                    vpe_item = QTreeWidgetItem(["", "", "", vpe, pal, poids])
                    vpe_item.setData(0, ROLE_TOUR, tour)
                    vpe_item.setData(0, ROLE_AUF, auf)
                    auf_item.addChild(vpe_item)

                auf_item.setExpanded(True)

            tour_item.setExpanded(True)

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

        tour = str(it.data(0, ROLE_TOUR) or "").strip()
        auf  = str(it.data(0, ROLE_AUF) or "").strip()

        if not tour:
            return

        # ✅ on exige une commande
        if not auf:
            QMessageBox.information(self, "Rattacher CMR", "Sélectionne une COMMANDE (AufNr), pas uniquement le dossier.")
            return

        self.selected_tour_nr = tour
        self.selected_auf_nr = auf
        self.accept()