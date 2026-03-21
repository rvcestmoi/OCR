# OCR/ui/block_options_dialog.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QComboBox, QPushButton, QMessageBox
)


BLOCK_REASONS = [
    "CMR manquant",
    "Doc DOUANE manquant",
    "Réserve sur CMR",
    "Litige",
    "IBAN",
    "Avoir demandé",
    "A bloquer",
]


class BlockOptionsDialog(QDialog):
    def __init__(self, parent=None, *, document_name: str, blocked: bool = False, comment: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Options de blocage")
        self.resize(520, 200)

        root = QVBoxLayout(self)

        root.addWidget(QLabel(f"Document : {document_name}"))

        self.chk_block = QCheckBox("Bloquer ce document")
        self.chk_block.setChecked(bool(blocked))
        root.addWidget(self.chk_block)

        root.addWidget(QLabel("Motif :"))
        self.cbo_reason = QComboBox()
        self.cbo_reason.addItem("")
        self.cbo_reason.addItems(BLOCK_REASONS)
        self.cbo_reason.setEditable(False)

        current_comment = (comment or "").strip()
        idx = self.cbo_reason.findText(current_comment)
        if idx >= 0:
            self.cbo_reason.setCurrentIndex(idx)
        else:
            self.cbo_reason.setCurrentIndex(0)

        root.addWidget(self.cbo_reason)

        btns = QHBoxLayout()
        btns.addStretch()

        self.btn_cancel = QPushButton("Annuler")
        self.btn_ok = QPushButton("OK")

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        root.addLayout(btns)

        self.chk_block.toggled.connect(self._update_state)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self._on_accept)

        self._update_state(self.chk_block.isChecked())

    def _update_state(self, blocked: bool):
        self.cbo_reason.setEnabled(bool(blocked))
        if not blocked:
            self.cbo_reason.setCurrentIndex(0)

    def _on_accept(self):
        if self.chk_block.isChecked() and not (self.cbo_reason.currentText() or "").strip():
            QMessageBox.warning(self, "Blocage", "Veuillez choisir un motif de blocage.")
            return
        self.accept()

    def get_result(self) -> dict:
        comment = (self.cbo_reason.currentText() or "").strip() if self.chk_block.isChecked() else ""
        return {
            "blocked": self.chk_block.isChecked(),
            "comment": comment,
        }
