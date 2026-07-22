from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QScrollArea,
)
from PySide6.QtGui import QPixmap, QTransform, QPainter, QPen, QColor, QImage
from PySide6.QtCore import Qt, Signal, QRectF, QTimer

try:
    import fitz
except Exception:  # pragma: no cover - dépend de l'installation client
    fitz = None


class PdfViewer(QWidget):
    """
    Viewer PDF/image avec navigation par page, zoom, rotation et surlignage.

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
        self._pdf_path: str | None = None
        self._pdf_page_count: int = 0
        self._render_cache: dict[int, QPixmap] = {}
        self._render_scale: float = 2.0
        self._max_cached_pages: int = 6
        self._current_page: int = 0

        self._zoom_factor: float = 1.0
        self._auto_fit_width: bool = True
        self._rotation_degrees: int = 0

        # Highlights optionnels, format compatible JSON :
        # {"iban": {"page": 0, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.04}}
        # Les coordonnées sont normalisées par rapport à la page non tournée.
        self._highlights: dict[str, dict] = {}
        self._active_highlight_key: str | None = None
        # Décorations de page (cadre couleur, libellé discret en haut, etc.)
        # Format: {page_index: {"border_color": ..., "label_text": ..., "label_color": ...}}
        self._page_decorations: dict[int, dict] = {}

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
        """Charge une liste de pixmaps déjà rendues.

        Cette API est conservée pour les images et la compatibilité. Pour les
        PDF multipages, `set_pdf_file()` est plus rapide car il ne rend que la
        page affichée.
        """
        self._pdf_path = None
        self._pdf_page_count = 0
        self._render_cache = {}
        self._pixmaps = pixmaps or []
        self._current_page = 0
        self._rotation_degrees = 0
        self._zoom_factor = 1.0
        self._auto_fit_width = True
        self.clear_highlights(refresh=False)
        self.clear_page_decorations(refresh=False)

        if self.page_count() > 0:
            self.fit_to_width()
        else:
            self.label.clear()
            self.view_changed.emit()

    def set_pdf_file(self, pdf_path: str) -> None:
        """Charge un PDF en rendu paresseux.

        Avant, l'application rendait toutes les pages à l'ouverture. Sur les
        gros PDF, c'était très coûteux alors qu'une seule page est affichée.
        Ici on garde le nombre de pages, puis on rend uniquement la page
        courante, avec un petit cache pour la navigation.
        """
        if fitz is None:
            raise RuntimeError("PyMuPDF/fitz indisponible : impossible d'afficher le PDF.")

        path = str(pdf_path or "").strip()
        if not path:
            self.set_pages([])
            return

        with fitz.open(path) as doc:
            page_count = int(doc.page_count or 0)

        self._pixmaps = []
        self._pdf_path = path
        self._pdf_page_count = max(0, page_count)
        self._render_cache = {}
        self._current_page = 0
        self._rotation_degrees = 0
        self._zoom_factor = 1.0
        self._auto_fit_width = True
        self.clear_highlights(refresh=False)
        self.clear_page_decorations(refresh=False)

        if self.page_count() > 0:
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
        if self._current_page < self.page_count() - 1:
            self._current_page += 1
            self._refresh()

    def previous_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._refresh()

    def go_to_page(self, index: int) -> None:
        if 0 <= index < self.page_count():
            self._current_page = index
            self._refresh()

    def page_count(self) -> int:
        if self._pdf_path:
            return int(self._pdf_page_count or 0)
        return len(self._pixmaps)

    def current_page_index(self) -> int:
        return self._current_page

    # ---------------- Zoom ----------------
    def zoom_in(self) -> None:
        if self.page_count() <= 0:
            return
        self._auto_fit_width = False
        self._zoom_factor *= 1.2
        self._refresh()

    def zoom_out(self) -> None:
        if self.page_count() <= 0:
            return
        self._auto_fit_width = False
        self._zoom_factor /= 1.2
        self._zoom_factor = max(self._zoom_factor, 0.05)
        self._refresh()

    def reset_zoom(self) -> None:
        self.fit_to_width()

    def fit_to_width(self) -> None:
        if self.page_count() <= 0:
            return

        base = self._get_page_pixmap(self._current_page)
        if base is None or base.isNull():
            return
        rotated = self._get_rotated_pixmap(base)
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
        if self.page_count() <= 0:
            return
        self._rotation_degrees = (self._rotation_degrees - 90) % 360
        if self._auto_fit_width:
            self.fit_to_width()
        else:
            self._refresh()

    def rotate_right(self) -> None:
        if self.page_count() <= 0:
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
        if self.page_count() > 0 and self._auto_fit_width:
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
    # Highlights
    # ------------------------------------------------------------------
    def clear_highlights(self, refresh: bool = True) -> None:
        self._highlights = {}
        self._active_highlight_key = None
        if refresh:
            self._refresh()

    def clear_page_decorations(self, refresh: bool = True) -> None:
        self._page_decorations = {}
        if refresh:
            self._refresh()

    def set_page_decorations(self, decorations=None) -> None:
        cleaned: dict[int, dict] = {}
        if isinstance(decorations, list):
            iterable = decorations
        elif isinstance(decorations, dict):
            iterable = []
            for k, v in decorations.items():
                if isinstance(v, dict):
                    item = dict(v)
                    item.setdefault("page", k)
                    iterable.append(item)
        else:
            iterable = []

        for item in iterable:
            if not isinstance(item, dict):
                continue
            try:
                page = int(item.get("page", item.get("page_index", 0)) or 0)
            except Exception:
                page = 0
            page = max(0, page)
            deco = {
                "border_color": item.get("border_color"),
                "label_text": str(item.get("label_text") or "").strip(),
                "label_color": item.get("label_color"),
                "label_background": item.get("label_background"),
            }
            if deco["border_color"] or deco["label_text"]:
                cleaned[page] = deco

        self._page_decorations = cleaned
        self._refresh()

    def set_highlights(self, highlights=None, active_key: str | None = None, **kwargs) -> None:
        if highlights is None:
            highlights = kwargs.get("field_positions") or kwargs.get("positions") or {}
        if not isinstance(highlights, dict):
            highlights = {}

        cleaned: dict[str, dict] = {}
        for key, pos in highlights.items():
            if not isinstance(pos, dict):
                continue
            norm = self._normalize_highlight_position(pos)
            if norm:
                cleaned[str(key)] = norm

        self._highlights = cleaned
        self._active_highlight_key = str(active_key) if active_key else None
        self._refresh()

    def highlight_field(self, field_key=None, position=None, **kwargs) -> None:
        """Affiche le rectangle du champ demandé.

        Accepte les deux formes :
        - highlight_field("iban", position_dict)
        - highlight_field(field_key="iban", position=position_dict)
        """
        key = str(field_key or kwargs.get("key") or kwargs.get("field") or "").strip()
        pos = position or kwargs.get("position") or kwargs.get("rect")

        if isinstance(pos, dict):
            norm = self._normalize_highlight_position(pos)
            if norm:
                self._highlights[key or "active"] = norm
                self._active_highlight_key = key or "active"
                page = self._position_page_index(norm)
                if page is not None and 0 <= page < self.page_count():
                    self._current_page = page
                self._refresh()
                return

        # fallback : si seule la clé est donnée, on tente de l'utiliser dans les highlights existants
        if key and key in self._highlights:
            self._active_highlight_key = key
            page = self._position_page_index(self._highlights[key])
            if page is not None and 0 <= page < self.page_count():
                self._current_page = page
            self._refresh()
        else:
            self._active_highlight_key = None
            self._refresh()

    def _normalize_highlight_position(self, pos: dict) -> dict | None:
        try:
            x = float(pos.get("x", pos.get("left", pos.get("x0", 0))))
            y = float(pos.get("y", pos.get("top", pos.get("y0", 0))))
            w = float(pos.get("w", pos.get("width", 0)))
            h = float(pos.get("h", pos.get("height", 0)))

            # Compat éventuelle avec x0/y0/x1/y1 normalisés.
            if (w <= 0 or h <= 0) and {"x0", "y0", "x1", "y1"}.issubset(pos.keys()):
                x0 = float(pos.get("x0") or 0)
                y0 = float(pos.get("y0") or 0)
                x1 = float(pos.get("x1") or 0)
                y1 = float(pos.get("y1") or 0)
                x, y, w, h = x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)

            if w <= 0 or h <= 0:
                return None

            page = pos.get("page", pos.get("page_index", 0))
            try:
                page = int(page)
            except Exception:
                page = 0
            if page >= 1 and bool(pos.get("page_number")):
                page -= 1

            out = dict(pos)
            out.update({
                "page": max(0, page),
                "x": max(0.0, min(1.0, x)),
                "y": max(0.0, min(1.0, y)),
                "w": max(0.0, min(1.0, w)),
                "h": max(0.0, min(1.0, h)),
            })
            return out
        except Exception:
            return None

    def _to_qcolor(self, value, fallback: QColor) -> QColor:
        if isinstance(value, QColor):
            return QColor(value)
        if isinstance(value, (tuple, list)) and len(value) in (3, 4):
            try:
                if len(value) == 3:
                    return QColor(int(value[0]), int(value[1]), int(value[2]))
                return QColor(int(value[0]), int(value[1]), int(value[2]), int(value[3]))
            except Exception:
                return QColor(fallback)
        if isinstance(value, str) and value.strip():
            c = QColor(value.strip())
            if c.isValid():
                return c
        return QColor(fallback)

    def _position_page_index(self, pos: dict | None) -> int | None:
        if not isinstance(pos, dict):
            return None
        try:
            return max(0, int(pos.get("page", pos.get("page_index", 0)) or 0))
        except Exception:
            return None

    def _paint_page_decorations(self, pixmap: QPixmap, page_index: int) -> QPixmap:
        if pixmap.isNull() or not self._page_decorations:
            return pixmap

        deco = self._page_decorations.get(int(page_index))
        if not isinstance(deco, dict):
            return pixmap

        page_w = max(1, pixmap.width())
        page_h = max(1, pixmap.height())
        out = QPixmap(pixmap)
        painter = QPainter(out)
        try:
            border = deco.get("border_color")
            if border:
                color = self._to_qcolor(border, QColor(255, 140, 0, 215))
                pen_width = max(5, int(min(page_w, page_h) * 0.0065))
                painter.setPen(QPen(color, pen_width, Qt.SolidLine))
                inset = max(4.0, pen_width * 0.8)
                painter.drawRect(QRectF(inset, inset, max(1.0, page_w - inset * 2), max(1.0, page_h - inset * 2)))

            label_text = str(deco.get("label_text") or "").strip()
            if label_text:
                label_color = self._to_qcolor(deco.get("label_color"), QColor(255, 140, 0, 235))
                bg_color = self._to_qcolor(deco.get("label_background"), QColor(255, 255, 255, 208))

                font = painter.font()
                font_size = max(8, int(min(page_w, page_h) * 0.018))
                font.setPointSize(font_size)
                painter.setFont(font)
                fm = painter.fontMetrics()
                pad_x = max(8, int(font_size * 0.8))
                pad_y = max(4, int(font_size * 0.35))
                text_w = fm.horizontalAdvance(label_text)
                rect_w = min(page_w - 20, text_w + pad_x * 2)
                rect_h = fm.height() + pad_y * 2
                rect_x = max(10.0, (page_w - rect_w) / 2.0)
                rect_y = 10.0
                painter.setPen(Qt.NoPen)
                painter.setBrush(bg_color)
                painter.drawRoundedRect(QRectF(rect_x, rect_y, rect_w, rect_h), 8, 8)
                painter.setPen(QPen(label_color, 1))
                painter.drawText(QRectF(rect_x + pad_x, rect_y + pad_y / 2.0, rect_w - pad_x * 2, rect_h), Qt.AlignCenter | Qt.AlignVCenter, label_text)
        finally:
            painter.end()

        return out

    def _paint_highlights(self, pixmap: QPixmap, page_index: int) -> QPixmap:
        if pixmap.isNull() or not self._highlights:
            return pixmap

        page_w = max(1, pixmap.width())
        page_h = max(1, pixmap.height())

        to_paint: list[tuple[str, dict]] = []
        for key, pos in self._highlights.items():
            if self._position_page_index(pos) == page_index:
                to_paint.append((key, pos))

        if not to_paint:
            return pixmap

        out = QPixmap(pixmap)
        painter = QPainter(out)
        try:
            # Un seul champ actif à la fois dans l'étape 1.
            for key, pos in to_paint:
                active = bool(self._active_highlight_key and key == self._active_highlight_key)
                color = QColor(220, 0, 0, 230) if active else QColor(255, 140, 0, 210)
                pen_width = max(4 if active else 3, int(min(page_w, page_h) * (0.004 if active else 0.003)))
                painter.setPen(QPen(color, pen_width, Qt.SolidLine))

                x = float(pos.get("x", 0)) * page_w
                y = float(pos.get("y", 0)) * page_h
                w = float(pos.get("w", 0)) * page_w
                h = float(pos.get("h", 0)) * page_h

                pad = max(2.0, pen_width * 1.5)
                rect = QRectF(
                    max(0.0, x - pad),
                    max(0.0, y - pad),
                    min(float(page_w), w + pad * 2),
                    min(float(page_h), h + pad * 2),
                )
                painter.drawRect(rect)
        finally:
            painter.end()

        return out

    def _scroll_to_highlight(self, pos: dict | None) -> None:
        """Centre approximativement la zone active après changement de page.

        La peinture se fait avant rotation ; cette méthode reste volontairement
        simple pour ne jamais bloquer l'affichage si la page est tournée.
        """
        if not isinstance(pos, dict) or not self.label.pixmap():
            return
        try:
            scaled = self.label.pixmap()
            if scaled.isNull():
                return
            x = float(pos.get("x", 0)) * scaled.width()
            y = float(pos.get("y", 0)) * scaled.height()
            self.scroll_area.horizontalScrollBar().setValue(max(0, int(x) - self.scroll_area.viewport().width() // 2))
            self.scroll_area.verticalScrollBar().setValue(max(0, int(y) - self.scroll_area.viewport().height() // 2))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _get_page_pixmap(self, index: int) -> QPixmap | None:
        if index < 0 or index >= self.page_count():
            return None

        if self._pdf_path:
            if index in self._render_cache:
                return self._render_cache[index]
            if fitz is None:
                return None

            try:
                with fitz.open(self._pdf_path) as doc:
                    page = doc.load_page(index)
                    pix = page.get_pixmap(matrix=fitz.Matrix(self._render_scale, self._render_scale), alpha=False)
                    fmt = QImage.Format_RGB888 if pix.n < 4 else QImage.Format_RGBA8888
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()
                    qpix = QPixmap.fromImage(img)
            except Exception:
                return None

            self._render_cache[index] = qpix
            if len(self._render_cache) > self._max_cached_pages:
                # petit LRU simplifié : on garde la page courante et les pages proches
                keep = {index, index - 1, index + 1}
                for old_idx in list(self._render_cache.keys()):
                    if len(self._render_cache) <= self._max_cached_pages:
                        break
                    if old_idx not in keep:
                        self._render_cache.pop(old_idx, None)
            return qpix

        try:
            return self._pixmaps[index]
        except Exception:
            return None

    def _get_rotated_pixmap(self, pixmap: QPixmap) -> QPixmap:
        if self._rotation_degrees % 360 == 0:
            return pixmap
        transform = QTransform().rotate(self._rotation_degrees)
        return pixmap.transformed(transform, Qt.SmoothTransformation)

    def _refresh(self) -> None:
        if self.page_count() <= 0:
            self.label.clear()
            self.view_changed.emit()
            return

        base_pixmap = self._get_page_pixmap(self._current_page)
        if base_pixmap is None or base_pixmap.isNull():
            self.label.clear()
            self.view_changed.emit()
            return

        painted = self._paint_page_decorations(base_pixmap, self._current_page)
        painted = self._paint_highlights(painted, self._current_page)
        pixmap = self._get_rotated_pixmap(painted)

        scaled = pixmap.scaled(
            pixmap.size() * self._zoom_factor,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

        self.label.setPixmap(scaled)
        self.label.adjustSize()
        self.view_changed.emit()

    def get_current_page_number(self) -> int:
        return int(getattr(self, "_current_page", 0)) + 1
