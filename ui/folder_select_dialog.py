# ui/folder_select_dialog.py
from __future__ import annotations

from collections import defaultdict
from typing import Any

from PySide6.QtCore import Qt, QSize
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
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sélectionne une COMMANDE (AufNr) à rattacher. 1 CMR = 1 commande."))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Dossier", "Commande", "Chargement / livraison"])
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setUniformRowHeights(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)

        layout.addWidget(self.tree)

        details_rows = details_rows or []
        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]

        by_tour_auf = defaultdict(list)          # (tour, auf) -> rows
        info_by_tour_auf = {}                   # (tour, auf) -> infos affichage CMR

        for r in details_rows:
            tour = str(r.get("Dossier") or "").strip()
            auf  = str(r.get("AufNr") or "").strip()
            if not tour:
                continue
            if auf:
                by_tour_auf[(tour, auf)].append(r)
                if (tour, auf) not in info_by_tour_auf:
                    info_by_tour_auf[(tour, auf)] = r

        bold = QFont()
        bold.setBold(True)

        first_item = None

        for tour in tour_numbers:
            # noeud Dossier
            tour_item = QTreeWidgetItem([tour, "", ""])
            tour_item.setData(0, ROLE_TOUR, tour)
            tour_item.setData(0, ROLE_AUF, "")
            for c in range(3):
                tour_item.setFont(c, bold)

            self.tree.addTopLevelItem(tour_item)

            # commandes dans ce dossier
            aufnrs = sorted({auf for (t, auf) in by_tour_auf.keys() if t == tour})
            if not aufnrs:
                child = QTreeWidgetItem(["", "(aucune commande trouvée)", ""])
                child.setData(0, ROLE_TOUR, tour)
                child.setData(0, ROLE_AUF, "")
                tour_item.addChild(child)
                tour_item.setExpanded(True)
                continue

            for auf in aufnrs:
                info = info_by_tour_auf.get((tour, auf), {}) or {}
                charg_liv = self._format_chargement_livraison(info)
                tooltip = self._format_tooltip(info)
                auf_item = QTreeWidgetItem(["", auf, charg_liv])
                auf_item.setData(0, ROLE_TOUR, tour)
                auf_item.setData(0, ROLE_AUF, auf)
                auf_item.setToolTip(2, tooltip)
                for c in range(3):
                    auf_item.setFont(c, bold)
                    auf_item.setSizeHint(c, QSize(120 if c < 2 else 500, 64))
                    if tooltip:
                        auf_item.setToolTip(c, tooltip)

                tour_item.addChild(auf_item)
                if first_item is None:
                    first_item = auf_item

            tour_item.setExpanded(True)

        if first_item is not None:
            self.tree.setCurrentItem(first_item)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        self.tree.itemDoubleClicked.connect(lambda *_: self.accept_selected())
        buttons.accepted.connect(self.accept_selected)
        buttons.rejected.connect(self.reject)

    @staticmethod
    def _clean(v: Any) -> str:
        return str(v or "").strip()

    @classmethod
    def _format_date(cls, value: Any) -> str:
        s = cls._clean(value)
        if not s:
            return ""
        # SQL Server peut renvoyer dd/mm/yyyy, yyyy-mm-dd ou datetime texte.
        if " " in s:
            s = s.split(" ", 1)[0]
        if "T" in s:
            s = s.split("T", 1)[0]
        if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
            y, m, d = s[:10].split("-")
            return f"{d}/{m}/{y}"
        return s[:10]

    @classmethod
    def _format_place(cls, pays: Any, cp: Any, ville: Any) -> str:
        parts = [cls._clean(pays), cls._clean(cp), cls._clean(ville)]
        return " ".join([p for p in parts if p])

    @classmethod
    def _format_chargement_livraison(cls, info: dict[str, Any]) -> str:
        ch_date = cls._format_date(info.get("DateChargement"))
        ch_name = cls._clean(info.get("ChargementName1"))
        ch_place = cls._format_place(info.get("ChargementPays"), info.get("ChargementCP"), info.get("ChargementVille"))

        liv_date = cls._format_date(info.get("DateLivraison"))
        liv_name = cls._clean(info.get("LivraisonName1"))
        liv_place = cls._format_place(info.get("LivraisonPays"), info.get("LivraisonCP"), info.get("LivraisonVille"))

        ch_line = "Chargement : " + " · ".join([x for x in [ch_date, ch_name] if x])
        if ch_place:
            ch_line += f", {ch_place}" if (ch_date or ch_name) else ch_place

        liv_line = "Livraison : " + " · ".join([x for x in [liv_date, liv_name] if x])
        if liv_place:
            liv_line += f", {liv_place}" if (liv_date or liv_name) else liv_place

        return ch_line.rstrip() + "\n" + liv_line.rstrip()

    @classmethod
    def _format_tooltip(cls, info: dict[str, Any]) -> str:
        trajet = cls._clean(info.get("Trajet"))
        base = cls._format_chargement_livraison(info)
        return base + (("\n\nTrajet : " + trajet) if trajet else "")

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