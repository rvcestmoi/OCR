from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea,
)
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtCore import Qt, Signal


class PdfViewer(QWidget):
    """
    Viewer PDF/image avec navigation par page, zoom et rotation.

    - PDF multipages
    - Une page affichée à la fois
    - Navigation page précédente / suivante
    - Auto-zoom fit largeur
    - Zoom manuel
    - Rotation 90° gauche / droite
    - Compatible API legacy
    """

    # Compatibilité legacy
    text_selected = Signal(str)
    view_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._pixmaps: list[QPixmap] = []
        self._current_page: int = 0

        self._zoom_factor: float = 1.0
        self._auto_fit_width: bool = True
        self._rotation_degrees: int = 0

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

        self.scroll_area.viewport().installEventFilter(self)
        self.label.installEventFilter(self)

        self.scroll_area.setWidget(self.label)
        layout.addWidget(self.scroll_area)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_pages(self, pixmaps: list[QPixmap]) -> None:
        self._pixmaps = pixmaps or []
        self._current_page = 0
        self._rotation_degrees = 0
        self._zoom_factor = 1.0
        self._auto_fit_width = True

        if self._pixmaps:
            self.fit_to_width()
        else:
            self.label.clear()
            self.view_changed.emit()

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
        if not self._pixmaps:
            return
        self._auto_fit_width = False
        self._zoom_factor *= 1.2
        self._refresh()

    def zoom_out(self) -> None:
        if not self._pixmaps:
            return
        self._auto_fit_width = False
        self._zoom_factor /= 1.2
        self._zoom_factor = max(self._zoom_factor, 0.05)
        self._refresh()

    def reset_zoom(self) -> None:
        self.fit_to_width()

    def fit_to_width(self) -> None:
        if not self._pixmaps:
            return

        rotated = self._get_rotated_pixmap(self._pixmaps[self._current_page])
        page_width = rotated.width()
        viewport_width = max(1, self.scroll_area.viewport().width() - 10)

        if page_width > 0:
            self._zoom_factor = viewport_width / page_width
            self._auto_fit_width = True
            self._refresh()

    def get_zoom_percent(self) -> int:
        return max(1, int(round(self._zoom_factor * 100)))

    # ---------------- Rotation ----------------
    def rotate_left(self) -> None:
        if not self._pixmaps:
            return
        self._rotation_degrees = (self._rotation_degrees - 90) % 360
        if self._auto_fit_width:
            self.fit_to_width()
        else:
            self._refresh()

    def rotate_right(self) -> None:
        if not self._pixmaps:
            return
        self._rotation_degrees = (self._rotation_degrees + 90) % 360
        if self._auto_fit_width:
            self.fit_to_width()
        else:
            self._refresh()

    def reset_rotation(self) -> None:
        self._rotation_degrees = 0
        if self._auto_fit_width:
            self.fit_to_width()
        else:
            self._refresh()

    def rotation_degrees(self) -> int:
        return self._rotation_degrees

    def reset_view(self) -> None:
        self._rotation_degrees = 0
        self.fit_to_width()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pixmaps and self._auto_fit_width:
            self.fit_to_width()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            angle = event.angleDelta().y()
            if angle > 0:
                self.zoom_in()
            elif angle < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def eventFilter(self, obj, event):
        if obj in (self.scroll_area.viewport(), self.label) and event.type() == event.Type.Wheel:
            if event.modifiers() & Qt.ControlModifier:
                angle = event.angleDelta().y()
                if angle > 0:
                    self.zoom_in()
                elif angle < 0:
                    self.zoom_out()
                event.accept()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _get_rotated_pixmap(self, pixmap: QPixmap) -> QPixmap:
        if self._rotation_degrees % 360 == 0:
            return pixmap
        transform = QTransform().rotate(self._rotation_degrees)
        return pixmap.transformed(transform, Qt.SmoothTransformation)

    def _refresh(self) -> None:
        if not self._pixmaps:
            self.label.clear()
            self.view_changed.emit()
            return

        pixmap = self._get_rotated_pixmap(self._pixmaps[self._current_page])

        scaled = pixmap.scaled(
            pixmap.size() * self._zoom_factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.label.setPixmap(scaled)
        self.label.adjustSize()
        self.view_changed.emit()

    # ------------------------------------------------------------------
    # Legacy no-op (zones / highlights supprimés)
    # ------------------------------------------------------------------
    def clear_highlights(self) -> None:
        pass

    def set_highlights(self, *args, **kwargs) -> None:
        pass

    def highlight_field(self, *args, **kwargs) -> None:
        pass

    def get_current_page_number(self) -> int:
        return int(getattr(self, "_current_page", 0)) + 1
