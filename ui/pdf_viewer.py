# ui/pdf_viewer.py

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, Signal


class PdfViewer(QWidget):
    """
    Viewer PDF avec navigation par page.

    - PDF multipages
    - Une page affichée à la fois
    - Navigation page précédente / suivante
    - Auto-zoom fit largeur
    - Zoom manuel
    - Compatible API legacy
    """

    # Compatibilité legacy
    text_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._pixmaps: list[QPixmap] = []
        self._current_page: int = 0

        self._zoom_factor: float = 1.0
        self._auto_fit_width: bool = True

        self._init_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)

        self.scroll_area.setWidget(self.label)
        layout.addWidget(self.scroll_area)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_pages(self, pixmaps: list[QPixmap]) -> None:
        self._pixmaps = pixmaps or []
        self._current_page = 0

        if self._pixmaps:
            self.fit_to_width()
        else:
            self.label.clear()

    # Ancienne API mono-page
    def setPixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None:
            self.set_pages([])
        else:
            self.set_pages([pixmap])

    # ---------------- Navigation ----------------
    def next_page(self) -> None:
        if self._current_page < len(self._pixmaps) - 1:
            self._current_page += 1
            self._refresh()

    def previous_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._refresh()

    def go_to_page(self, index: int) -> None:
        if 0 <= index < len(self._pixmaps):
            self._current_page = index
            self._refresh()

    def page_count(self) -> int:
        return len(self._pixmaps)

    def current_page_index(self) -> int:
        return self._current_page

    # ---------------- Zoom ----------------
    def zoom_in(self) -> None:
        self._auto_fit_width = False
        self._zoom_factor *= 1.2
        self._refresh()

    def zoom_out(self) -> None:
        self._auto_fit_width = False
        self._zoom_factor /= 1.2
        self._refresh()

    def reset_zoom(self) -> None:
        self.fit_to_width()

    def fit_to_width(self) -> None:
        if not self._pixmaps:
            return

        viewport_width = self.scroll_area.viewport().width()
        page_width = self._pixmaps[self._current_page].width()

        if page_width > 0:
            self._zoom_factor = viewport_width / page_width
            self._auto_fit_width = True
            self._refresh()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        if not self._pixmaps:
            self.label.clear()
            return

        pixmap = self._pixmaps[self._current_page]

        scaled = pixmap.scaled(
            pixmap.size() * self._zoom_factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )

        self.label.setPixmap(scaled)

    # ------------------------------------------------------------------
    # Legacy no-op (zones / highlights supprimés)
    # ------------------------------------------------------------------
    def clear_highlights(self) -> None:
        pass

    def set_highlights(self, *args, **kwargs) -> None:
        pass

    def highlight_field(self, *args, **kwargs) -> None:
        pass
