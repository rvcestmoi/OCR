# OCR/ui/block_options_dialog.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QComboBox, QPushButton, QMessageBox,
    QPlainTextEdit
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
    def __init__(
        self,
        parent=None,
        *,
        document_name: str,
        blocked: bool = False,
        comment: str = "",
        reason: str = "",
        free_comment: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle("Options de blocage")
        self.resize(560, 280)

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
        current_reason = (reason or "").strip()
        current_free_comment = (free_comment or "").strip()

        if not current_reason and current_comment:
            for candidate in BLOCK_REASONS:
                prefix = f"{candidate} - "
                if current_comment == candidate:
                    current_reason = candidate
                    current_free_comment = ""
                    break
                if current_comment.startswith(prefix):
                    current_reason = candidate
                    current_free_comment = current_comment[len(prefix):].strip()
                    break

        idx = self.cbo_reason.findText(current_reason)
        self.cbo_reason.setCurrentIndex(idx if idx >= 0 else 0)
        root.addWidget(self.cbo_reason)

        root.addWidget(QLabel("Commentaire :"))
        self.txt_comment = QPlainTextEdit()
        self.txt_comment.setPlaceholderText("Commentaire libre…")
        self.txt_comment.setPlainText(current_free_comment)
        self.txt_comment.setMaximumHeight(100)
        root.addWidget(self.txt_comment)

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
        enabled = bool(blocked)
        self.cbo_reason.setEnabled(enabled)
        self.txt_comment.setEnabled(enabled)
        if not enabled:
            self.cbo_reason.setCurrentIndex(0)
            self.txt_comment.setPlainText("")

    def _on_accept(self):
        if self.chk_block.isChecked() and not (self.cbo_reason.currentText() or "").strip():
            QMessageBox.warning(self, "Blocage", "Veuillez choisir un motif de blocage.")
            return
        self.accept()

    def get_result(self) -> dict:
        blocked = self.chk_block.isChecked()
        reason = (self.cbo_reason.currentText() or "").strip() if blocked else ""
        free_comment = (self.txt_comment.toPlainText() or "").strip() if blocked else ""

        if reason and free_comment:
            comment = f"{reason} - {free_comment}"
        else:
            comment = reason or free_comment

        return {
            "blocked": blocked,
            "reason": reason,
            "free_comment": free_comment,
            "comment": comment,
        }
