from __future__ import annotations

import os
import re
from typing import Callable

import fitz
import numpy as np
import pytesseract
from PIL import Image

try:
    import cv2
except Exception:
    cv2 = None

from app.settings import load_settings, get_ocr_value, get_path

_SETTINGS = load_settings()

pytesseract.pytesseract.tesseract_cmd = get_path(
    _SETTINGS,
    "tesseract_path",
    r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
)

_OCR_DPI = int(get_ocr_value(_SETTINGS, "dpi", 50) or 50)
_OCR_LANGS = str(get_ocr_value(_SETTINGS, "languages", "fra+eng+deu+spa+ita+nld") or "fra")

_BAD_OCR_CHARS = set("□■▪▫█▌▐▎▍▏|¦│┃┆┇")
_BAD_OCR_RE = re.compile(r"[□■▪▫█▌▐▎▍▏|¦│┃┆┇]+")
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def _clean_ocr_text(text: str) -> str:
    """Supprime les caractères parasites (carrés / barres) et les lignes quasi vides."""
    out = []
    for ln in (text or "").splitlines():
        raw = ln.rstrip("\n")
        if not raw.strip():
            continue

        bad_count = sum(1 for ch in raw if ch in _BAD_OCR_CHARS)
        if bad_count / max(1, len(raw)) > 0.45:
            continue

        ln2 = _BAD_OCR_RE.sub(" ", raw)
        ln2 = re.sub(r"\s{2,}", " ", ln2).strip()
        if ln2:
            out.append(ln2)
    return "\n".join(out)


def _preprocess_image_for_ocr(pil_img: Image.Image) -> Image.Image:
    """
    Enlève autant que possible les traits de tableaux avant OCR.
    Fallback simple si OpenCV n'est pas disponible.
    """
    gray = np.array(pil_img.convert("L"))

    if cv2 is None:
        thr = 200
        bw = (gray > thr).astype(np.uint8) * 255
        return Image.fromarray(bw)

    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = 255 - th

    h, w = inv.shape[:2]
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 35), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 35)))

    hori = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel, iterations=1)
    lines = cv2.bitwise_or(hori, vert)

    inv2 = cv2.bitwise_and(inv, cv2.bitwise_not(lines))
    cleaned = 255 - inv2
    return Image.fromarray(cleaned)


def _image_from_fitz_page(page: fitz.Page, dpi: int) -> Image.Image:
    scale = max(1.0, float(dpi) / 72.0)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return img


def _safe_tesseract_to_string(image: Image.Image, *, lang: str, config: str) -> str:
    try:
        return pytesseract.image_to_string(image, lang=lang, config=config)
    except pytesseract.TesseractError:
        # Si un pack de langues configuré manque, on retente en français.
        if lang != "fra":
            return pytesseract.image_to_string(image, lang="fra", config=config)
        raise


def _ocr_single_image(image: Image.Image, *, languages: str | None = None) -> str:
    langs = str(languages or _OCR_LANGS or "fra").strip() or "fra"
    img_pp = _preprocess_image_for_ocr(image)

    cfg_main = "--oem 3 --psm 6 -c preserve_interword_spaces=1"
    cfg_fallback = "--oem 3 --psm 11 -c preserve_interword_spaces=1"

    text = _safe_tesseract_to_string(img_pp, lang=langs, config=cfg_main)
    text = _clean_ocr_text(text)

    if len(text.strip()) < 50:
        text = _safe_tesseract_to_string(img_pp, lang=langs, config=cfg_fallback)
        text = _clean_ocr_text(text)

    if len(text.strip()) < 50:
        text = _safe_tesseract_to_string(image, lang=langs, config=cfg_fallback)
        text = _clean_ocr_text(text)

    return text.strip()


def extract_text_from_pdf(pdf_path: str, max_pages: int | None = None) -> str:
    """
    Extraction rapide du texte natif d'un PDF.
    Utilisé par le bouton OCR standard et la prélecture de classification.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"Document introuvable : {pdf_path}")

    doc = fitz.open(pdf_path)
    try:
        pages = list(doc)
        if max_pages is not None and max_pages >= 1:
            pages = pages[:max_pages]

        text = ""
        for page in pages:
            text += page.get_text()
        return text.strip()
    finally:
        doc.close()



def extract_text_with_tesseract(
    document_path: str,
    max_pages: int | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> str:
    """
    OCR profond forcé via Tesseract.
    - PDF : rasterisation des pages via PyMuPDF puis OCR page par page.
    - Image : OCR direct.
    progress_callback(current, total, label) permet de remonter l'avancement.
    """
    if not os.path.exists(document_path):
        raise FileNotFoundError(f"Document introuvable : {document_path}")

    ext = os.path.splitext(str(document_path or ""))[1].lower()

    if ext == ".pdf":
        doc = fitz.open(document_path)
        try:
            total_pages = len(doc)
            if max_pages is not None and max_pages >= 1:
                total_pages = min(total_pages, max_pages)

            if progress_callback:
                progress_callback(0, total_pages, f"OCR profond 0/{total_pages}")

            parts: list[str] = []
            for page_index in range(total_pages):
                page_no = page_index + 1
                page = doc.load_page(page_index)
                image = _image_from_fitz_page(page, _OCR_DPI)
                text = _ocr_single_image(image)
                if text:
                    parts.append(f"===== PAGE {page_no} =====\n{text}")
                if progress_callback:
                    progress_callback(page_no, total_pages, f"OCR profond page {page_no}/{total_pages}")
            return "\n\n".join(parts).strip()
        finally:
            doc.close()

    if ext in _IMAGE_EXTENSIONS:
        if progress_callback:
            progress_callback(0, 1, "OCR profond image 0/1")
        with Image.open(document_path) as img:
            text = _ocr_single_image(img.convert("RGB"))
        if progress_callback:
            progress_callback(1, 1, "OCR profond image 1/1")
        return text

    raise ValueError(f"Format non pris en charge pour l'OCR profond : {document_path}")
