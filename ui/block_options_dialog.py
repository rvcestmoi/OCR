# OCR/ui/block_options_dialog.py
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QCheckBox, QPlainTextEdit, QPushButton
)


class BlockOptionsDialog(QDialog):
    def __init__(self, parent=None, *, document_name: str, blocked: bool = False, comment: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Options de blocage")
        self.resize(520, 240)

        root = QVBoxLayout(self)

        root.addWidget(QLabel(f"Document : {document_name}"))

        self.chk_block = QCheckBox("Bloquer ce document")
        self.chk_block.setChecked(True)
        root.addWidget(self.chk_block)

        root.addWidget(QLabel("Commentaire :"))
        self.txt_comment = QPlainTextEdit()
        self.txt_comment.setPlaceholderText("Pourquoi ce document est bloqué…")
        self.txt_comment.setPlainText(comment or "")
        self.txt_comment.setMinimumHeight(110)
        root.addWidget(self.txt_comment, 1)

        btns = QHBoxLayout()
        btns.addStretch()

        self.btn_cancel = QPushButton("Annuler")
        self.btn_ok = QPushButton("OK")

        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        root.addLayout(btns)

        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)

    def get_result(self) -> dict:
        return {
            "blocked": self.chk_block.isChecked(),
            "comment": (self.txt_comment.toPlainText() or "").strip()
        }