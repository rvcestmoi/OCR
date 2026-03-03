from __future__ import annotations

from typing import Dict, List, Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QWidget, QTableWidget, QTableWidgetItem, QSpinBox, QMessageBox
)



class PalletDetailsDialog(QDialog):
    """
    Affiche 1 onglet par dossier (TourNr).
    Colonnes grises : VPE, sumVPE
    Colonne éditable : Palettes rendues
    """
    def __init__(
        self,
        parent,
        *,
        tour_numbers: List[str],
        tour_repo,
        existing_saved: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Détails palettes")
        self.resize(650, 420)

        self.tour_numbers = tour_numbers
        self.tour_repo = tour_repo
        self.existing_saved = existing_saved or {}

        self.tables: Dict[str, QTableWidget] = {}

        root = QVBoxLayout(self)

        root.addWidget(QLabel("Palettes par dossier :"))

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_refresh = QPushButton("🔄 Recharger")
        self.btn_save = QPushButton("💾 Sauvegarder")
        self.btn_close = QPushButton("Fermer")
        btns.addWidget(self.btn_refresh)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_close)
        root.addLayout(btns)

        self.btn_close.clicked.connect(self.reject)
        self.btn_refresh.clicked.connect(self._reload_all)
        self.btn_save.clicked.connect(self.accept)

        self._build_tabs()
        self._reload_all()

    def _build_tabs(self):
        self.tabs.clear()
        self.tables.clear()

        for tour_nr in self.tour_numbers:
            w = QWidget()
            lay = QVBoxLayout(w)
            table = QTableWidget(0, 4)
            table.setHorizontalHeaderLabels(["VPE", "Palettes", "Poids", "Palettes rendues"])
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.setSelectionBehavior(QTableWidget.SelectRows)
            table.setEditTriggers(QTableWidget.NoEditTriggers)

            lay.addWidget(table)
            self.tabs.addTab(w, tour_nr)
            self.tables[tour_nr] = table

    def _set_gray_item(self, item: QTableWidgetItem):
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setForeground(QColor("gray"))

    def _reload_all(self):
        for tour_nr, table in self.tables.items():
            table.setRowCount(0)

            try:
                rows = self.tour_repo.get_palette_details_by_tournr(tour_nr) or []
            except Exception as e:
                QMessageBox.warning(self, "SQL", f"Erreur requête palettes pour {tour_nr}:\n{e}")
                rows = []

            # mapping existant (déjà sauvegardé) : (vpe -> delivered)
            saved_lines = self.existing_saved.get(tour_nr, []) or []
            saved_map = {}
            for sl in saved_lines:
                v = str(sl.get("vpe", "")).strip()
                if v:
                    saved_map[v] = sl.get("delivered", None)

            for r in rows:
                # ✅ c'est VPE (pas "Pal")
                vpe = str(r.get("VPE", "") or "").strip()
                palettes = r.get("Palettes", 0)
                poids = r.get("Poids", 0)

                row_idx = table.rowCount()
                table.insertRow(row_idx)

                it_vpe = QTableWidgetItem(vpe)
                it_pal = QTableWidgetItem(str(palettes))
                it_poids = QTableWidgetItem(str(poids))

                self._set_gray_item(it_vpe)
                self._set_gray_item(it_pal)
                self._set_gray_item(it_poids)

                table.setItem(row_idx, 0, it_vpe)
                table.setItem(row_idx, 1, it_pal)
                table.setItem(row_idx, 2, it_poids)

                # ✅ colonne 3 = palettes rendues
                sp = QSpinBox()
                sp.setMinimum(0)
                try:
                    sp.setMaximum(int(palettes) if palettes is not None else 999999)
                except Exception:
                    sp.setMaximum(999999)

                # restore saved delivered if present
                if vpe in saved_map and saved_map[vpe] is not None:
                    try:
                        sp.setValue(int(saved_map[vpe]))
                    except Exception:
                        pass

                table.setCellWidget(row_idx, 3, sp)

    def get_result(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retourne:
          { TourNr: [ {vpe, sumVPE, delivered}, ... ] }
        """
        out: Dict[str, List[Dict[str, Any]]] = {}

        for tour_nr, table in self.tables.items():
            lines: List[Dict[str, Any]] = []
            for r in range(table.rowCount()):
                vpe_item = table.item(r, 0)
                pal_item = table.item(r, 1)
                poids_item = table.item(r, 2)
                sp = table.cellWidget(r, 3)

                vpe = (vpe_item.text() if vpe_item else "").strip()
                palettes = (pal_item.text() if pal_item else "").strip()
                poids = (poids_item.text() if poids_item else "").strip()

                delivered = None
                if isinstance(sp, QSpinBox):
                    delivered = sp.value()

                if vpe or palettes or poids or delivered is not None:
                    lines.append({
                        "vpe": vpe,
                        "palettes": palettes,
                        "poids": poids,
                        "delivered": delivered,
                    })              
            out[tour_nr] = lines

        return out