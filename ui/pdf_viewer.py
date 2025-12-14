from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPainter, QPen, QImage
import pytesseract
from PySide6.QtGui import QColor


class PdfViewer(QLabel):
    text_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)

        self.start_pos = None
        self.end_pos = None
        self.selecting = False

        self.original_image = None   # QImage source (PDF rendu)
        self.displayed_pixmap = None
        self.highlights = []  # liste de dicts: {rect, color, field}
        
        # 🔴 DEBUG HARD : rectangle forcé
        self.highlights.append({
            "rect": QRect(50, 50, 300, 120),
            "color": QColor(255, 0, 0, 200),
            "field": None
        })
        self.update()
        self.setScaledContents(True)
        self.focused_field = None  # champ actuellement sélectionné




    # =========================
    # Image handling
    # =========================
    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)
        self.displayed_pixmap = pixmap
        self.original_image = pixmap.toImage()

        # 🔴 mémorise le rectangle réel du pixmap affiché
        self._pixmap_rect = self._compute_pixmap_rect()


    # =========================
    # Mouse events
    # =========================

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_pos = event.position().toPoint()
            self.end_pos = self.start_pos
            self.selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.selecting:
            self.end_pos = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.selecting:
            self.selecting = False
            rect = QRect(self.start_pos, self.end_pos).normalized()
            self.process_selection(rect)
            self.update()

    # =========================
    # Drawing
    # =========================


    # =========================
    # OCR on selected zone
    # =========================
    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)

        for h in self.highlights:
            if self.focused_field and h["field"] != self.focused_field:
                continue

            painter.setBrush(h["color"])
            painter.setPen(Qt.NoPen)
            painter.drawRect(h["rect"])


        # Rectangle de sélection en cours
        if self.selecting and self.start_pos and self.end_pos:
            painter.setPen(QPen(Qt.red, 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRect(self.start_pos, self.end_pos))





    def process_selection(self, rect: QRect):
        print("DEBUG process_selection", rect)
        # Convertir rect QLabel -> rect RELATIF AU PIXMAP
        rect = rect.translated(-self._pixmap_rect.topLeft())

        if self.original_image is None:
            return

        # Conversion coordonnées QLabel -> image réelle
        label_w = self.width()
        label_h = self.height()
        img_w = self.original_image.width()
        img_h = self.original_image.height()

        scale_x = img_w / label_w
        scale_y = img_h / label_h

        img_rect = QRect(
            int(rect.x() * scale_x),
            int(rect.y() * scale_y),
            int(rect.width() * scale_x),
            int(rect.height() * scale_y),
        )

        cropped_qimage = self.original_image.copy(img_rect)
        if cropped_qimage.isNull():
            return

        # =========================
        # QImage -> PIL (Qt6 SAFE)
        # =========================
        cropped_qimage = cropped_qimage.convertToFormat(QImage.Format_RGB888)

        width = cropped_qimage.width()
        height = cropped_qimage.height()

        buffer = cropped_qimage.bits().tobytes()

        from PIL import Image
        pil_image = Image.frombytes(
            "RGB",
            (width, height),
            buffer
        )

        # =========================
        # OCR sur la zone sélectionnée
        # =========================
        if hasattr(self, "active_field") and self.active_field:
            color = self.field_colors.get(self.active_field)
            if color:
                self.add_highlight(rect, color, self.active_field)

        text = pytesseract.image_to_string(
            pil_image,
            lang="fra+eng",
            config="--psm 7"
        )
        cleaned = text.strip()
        if cleaned:
            self.text_selected.emit(cleaned)


    def add_highlight(self, rect, color, field):
        self.highlights.append({
            "rect": rect,
            "color": color,
            "field": field
        })
        self.update()

    def clear_highlights(self):
        self.highlights.clear()
        self.update()

    def pixmap_rect(self):
        if not self.pixmap():
            return QRect()

        pm = self.pixmap()
        lw, lh = self.width(), self.height()
        pw, ph = pm.width(), pm.height()

        x = (lw - pw) // 2
        y = (lh - ph) // 2

        return QRect(x, y, pw, ph)

    def _compute_pixmap_rect(self):
        if not self.pixmap():
            return QRect()

        pm = self.pixmap()
        lw, lh = self.width(), self.height()
        pw, ph = pm.width(), pm.height()

        x = (lw - pw) // 2
        y = (lh - ph) // 2

        return QRect(x, y, pw, ph)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._pixmap_rect = self._compute_pixmap_rect()


    def focus_on_field(self, field):
        """
        Affiche uniquement les surlignages liés à un champ
        """
        self.focused_field = field
        self.update()


