# ui/pallet_details_dialog.py
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QSpinBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget
)


class PalletDetailsDialog(QDialog):
    """
    Dialog d'édition des détails palettes par dossier (TourNr).
    - Charge VPE / Palettes / Poids depuis TourRepository.get_palette_details_by_tournr
    - Permet de saisir "delivered" (entier) par ligne VPE
    - Retourne un dict: {tour_nr: [ {vpe, palettes, poids, delivered}, ...], ...}
    """

    def __init__(self, parent=None, *, tour_numbers: List[str], tour_repo, existing_saved: Dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Détails palettes")
        self.resize(900, 520)

        self.tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]
        self.tour_repo = tour_repo
        self.existing_saved = existing_saved or {}

        self._tables: Dict[str, QTableWidget] = {}

        main = QVBoxLayout(self)

        info = QLabel("Saisis la colonne 'Livré' (delivered). Les autres colonnes viennent de la base.")
        info.setWordWrap(True)
        main.addWidget(info)

        self.tabs = QTabWidget()
        main.addWidget(self.tabs, 1)

        self._build_tabs()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons)

    # ---------- UI ----------
    def _build_tabs(self):
        self.tabs.clear()
        self._tables.clear()

        for tour_nr in self.tour_numbers:
            w = QWidget()
            lay = QVBoxLayout(w)

            table = QTableWidget()
            table.setColumnCount(4)
            table.setHorizontalHeaderLabels(["VPE", "Palettes", "Poids", "Livré"])
            table.verticalHeader().setVisible(False)
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QTableWidget.SelectRows)

            lay.addWidget(table)

            self.tabs.addTab(w, tour_nr)
            self._tables[tour_nr] = table

            self._load_one_tour(tour_nr, table)

            # colonnes
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)

    def _load_one_tour(self, tour_nr: str, table: QTableWidget):
        # lignes SQL
        try:
            rows = self.tour_repo.get_palette_details_by_tournr(tour_nr) or []
        except Exception:
            rows = []

        # mapping existant (déjà sauvegardé) : vpe -> delivered
        saved = self.existing_saved.get(tour_nr, []) or []
        saved_map = {}
        for sl in saved:
            vpe = str(sl.get("vpe") or sl.get("VPE") or "").strip()
            if not vpe:
                continue
            try:
                saved_map[vpe.upper()] = int(sl.get("delivered") or 0)
            except Exception:
                saved_map[vpe.upper()] = 0

        table.setRowCount(0)

        for r in rows:
            vpe = str(r.get("VPE") or r.get("vpe") or "").strip()
            palettes = r.get("Palettes") if "Palettes" in r else r.get("palettes")
            poids = r.get("Poids") if "Poids" in r else r.get("poids")

            delivered = saved_map.get(vpe.upper(), 0)

            row = table.rowCount()
            table.insertRow(row)

            it_vpe = QTableWidgetItem(vpe)
            it_vpe.setFlags(it_vpe.flags() & ~Qt.ItemIsEditable)

            it_pal = QTableWidgetItem("" if palettes is None else str(palettes))
            it_pal.setFlags(it_pal.flags() & ~Qt.ItemIsEditable)

            it_poids = QTableWidgetItem("" if poids is None else str(poids))
            it_poids.setFlags(it_poids.flags() & ~Qt.ItemIsEditable)

            # delivered avec spinbox
            spin = QSpinBox()
            spin.setMinimum(0)
            spin.setMaximum(999999)
            spin.setValue(int(delivered) if delivered is not None else 0)

            table.setItem(row, 0, it_vpe)
            table.setItem(row, 1, it_pal)
            table.setItem(row, 2, it_poids)
            table.setCellWidget(row, 3, spin)

    # ---------- Result ----------
    def get_result(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}

        for tour_nr, table in self._tables.items():
            items: List[Dict[str, Any]] = []
            for row in range(table.rowCount()):
                vpe = (table.item(row, 0).text() if table.item(row, 0) else "").strip()
                palettes = (table.item(row, 1).text() if table.item(row, 1) else "").strip()
                poids = (table.item(row, 2).text() if table.item(row, 2) else "").strip()

                w = table.cellWidget(row, 3)
                delivered = w.value() if isinstance(w, QSpinBox) else 0

                if vpe:
                    items.append({
                        "vpe": vpe,
                        "palettes": palettes,
                        "poids": poids,
                        "delivered": int(delivered),
                    })
            out[tour_nr] = items

        return out