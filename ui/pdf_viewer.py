from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, QRect, Signal
from PySide6.QtGui import QPainter, QPen, QImage, QColor
import pytesseract
from PIL import Image


class PdfViewer(QLabel):
    text_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAlignment(Qt.AlignCenter)
        self.setScaledContents(True)

        self.start_pos = None
        self.end_pos = None
        self.selecting = False

        self.original_image = None  # QImage du PDF
        self.highlights = []        # [{rect, color}]

        # Injecté depuis MainWindow
        self.active_field = None
        self.field_colors = {}

    # =========================
    # Image handling
    # =========================
    def setPixmap(self, pixmap):
        super().setPixmap(pixmap)
        self.original_image = pixmap.toImage()

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
    # OCR sur zone sélectionnée
    # =========================
    def process_selection(self, rect: QRect):
        if self.original_image is None:
            return

        label_w, label_h = self.width(), self.height()
        img_w, img_h = self.original_image.width(), self.original_image.height()

        scale_x = img_w / label_w
        scale_y = img_h / label_h

        img_rect = QRect(
            int(rect.x() * scale_x),
            int(rect.y() * scale_y),
            int(rect.width() * scale_x),
            int(rect.height() * scale_y),
        )

        cropped = self.original_image.copy(img_rect)
        if cropped.isNull():
            return

        cropped = cropped.convertToFormat(QImage.Format_RGB888)
        buffer = cropped.bits().tobytes()

        pil_image = Image.frombytes(
            "RGB",
            (cropped.width(), cropped.height()),
            buffer
        )

        text = pytesseract.image_to_string(
            pil_image,
            lang="fra+eng+deu+spa+ita+nld",
            config="--psm 7"
        ).strip()

        # Highlight
        color = self.field_colors.get(
            self.active_field,
            QColor(255, 255, 0, 80)
        )

        self.highlights.append({
            "rect": rect,
            "color": color
        })

        if text:
            self.text_selected.emit(text)

        self.update()

    # =========================
    # Drawing
    # =========================
    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)

        for h in self.highlights:
            painter.setBrush(h["color"])
            painter.setPen(Qt.NoPen)
            painter.drawRect(h["rect"])

        if self.selecting and self.start_pos and self.end_pos:
            painter.setPen(QPen(Qt.red, 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRect(self.start_pos, self.end_pos))

    def clear_highlights(self):
        self.highlights.clear()
        self.update()
