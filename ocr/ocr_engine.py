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

_OCR_DPI = int(get_ocr_value(_SETTINGS, "dpi", 200) or 200)
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

# =========================
# POSITIONS VISUELLES OCR
# =========================

_FIELD_POS_CLEAN_RE = re.compile(r"[^A-Z0-9]+")


def _normalize_field_position_value(value: str) -> str:
    return _FIELD_POS_CLEAN_RE.sub("", str(value or "").upper())




def _field_position_value_variants(field_key: str, value: str) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []

    variants = [raw]

    # Date : l'écran normalise souvent en jj/mm/aaaa alors que le PDF peut
    # contenir aaaa-mm-jj. On essaie les deux sens sans modifier la valeur UI.
    if str(field_key or "").strip() in {"invoice_date", "date"}:
        parts = re.findall(r"\d+", raw)
        if len(parts) == 3:
            p1, p2, p3 = parts
            if len(p1) == 4:  # aaaa mm jj
                variants.append(f"{p3}/{p2}/{p1}")
                variants.append(f"{p1}-{p2}-{p3}")
            elif len(p3) in {2, 4}:  # jj mm aa/aaaa
                year = p3 if len(p3) == 4 else ("20" + p3)
                variants.append(f"{year}-{p2}-{p1}")
                variants.append(f"{p1}.{p2}.{p3}")
                variants.append(f"{p1}-{p2}-{p3}")

    out: list[str] = []
    seen = set()
    for v in variants:
        norm = _normalize_field_position_value(v)
        if len(norm) >= 4 and norm not in seen:
            seen.add(norm)
            out.append(v)
    return out

def _rect_to_normalized_position(page_index: int, rect: tuple[float, float, float, float], page_w: float, page_h: float, *, source: str, value: str) -> dict:
    x0, y0, x1, y1 = rect
    page_w = max(1.0, float(page_w or 1.0))
    page_h = max(1.0, float(page_h or 1.0))

    x0 = max(0.0, min(page_w, float(x0)))
    y0 = max(0.0, min(page_h, float(y0)))
    x1 = max(0.0, min(page_w, float(x1)))
    y1 = max(0.0, min(page_h, float(y1)))

    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0

    return {
        "page": int(page_index),
        "page_number": int(page_index) + 1,
        "x": x0 / page_w,
        "y": y0 / page_h,
        "w": max(0.0, (x1 - x0) / page_w),
        "h": max(0.0, (y1 - y0) / page_h),
        "source": source,
        "value": str(value or ""),
    }


def _find_value_rect_in_words(words: list[dict], value: str) -> tuple[float, float, float, float] | None:
    """Retrouve une valeur dans une liste de mots positionnés.

    La comparaison ignore espaces, tirets, slashs, points, etc. Cela permet
    de matcher un IBAN affiché en groupes, une date avec séparateurs, ou un
    numéro de facture contenant des tirets.
    """
    target = _normalize_field_position_value(value)
    if len(target) < 4:
        return None

    # 1) Recherche exacte/contiguë sur séquences de mots.
    best: tuple[int, float, tuple[float, float, float, float]] | None = None
    max_words = 18
    for i in range(len(words)):
        combined = ""
        x0 = y0 = x1 = y1 = None

        for j in range(i, min(len(words), i + max_words)):
            word = words[j]
            wnorm = _normalize_field_position_value(word.get("text", ""))
            if not wnorm:
                continue

            combined += wnorm
            wx0, wy0, wx1, wy1 = word["x0"], word["y0"], word["x1"], word["y1"]
            x0 = wx0 if x0 is None else min(x0, wx0)
            y0 = wy0 if y0 is None else min(y0, wy0)
            x1 = wx1 if x1 is None else max(x1, wx1)
            y1 = wy1 if y1 is None else max(y1, wy1)

            if not combined:
                continue

            exact = combined == target
            contains = target in combined and len(combined) <= len(target) + 8
            contained = combined in target and len(combined) >= max(6, int(len(target) * 0.75))
            if exact or contains or contained:
                rect = (float(x0), float(y0), float(x1), float(y1))
                word_count = j - i + 1
                area = max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
                score = 0 if exact else (1 if contains else 2)
                candidate = (score * 1000 + word_count, area, rect)
                if best is None or candidate < best:
                    best = candidate
                break

            # Si on a déjà largement dépassé la cible, inutile de continuer.
            if len(combined) > len(target) + 12:
                break

    if best is not None:
        return best[2]

    # 2) Fallback : valeur incluse dans un seul mot long.
    for word in words:
        wnorm = _normalize_field_position_value(word.get("text", ""))
        if target and target in wnorm:
            return (float(word["x0"]), float(word["y0"]), float(word["x1"]), float(word["y1"]))

    return None


def _pdf_native_words_for_page(page: fitz.Page) -> list[dict]:
    raw_words = page.get_text("words") or []
    # PyMuPDF : x0, y0, x1, y1, word, block_no, line_no, word_no
    raw_words = sorted(raw_words, key=lambda w: (w[5], w[6], w[7], w[1], w[0]))
    out: list[dict] = []
    for w in raw_words:
        try:
            txt = str(w[4] or "").strip()
            if not txt:
                continue
            out.append({"x0": float(w[0]), "y0": float(w[1]), "x1": float(w[2]), "y1": float(w[3]), "text": txt})
        except Exception:
            continue
    return out


def _tesseract_words_for_image(image: Image.Image, *, languages: str | None = None) -> tuple[list[dict], int, int]:
    langs = str(languages or _OCR_LANGS or "fra").strip() or "fra"
    img_pp = _preprocess_image_for_ocr(image)
    cfg = "--oem 3 --psm 6 -c preserve_interword_spaces=1"

    try:
        data = pytesseract.image_to_data(img_pp, lang=langs, config=cfg, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        if langs != "fra":
            data = pytesseract.image_to_data(img_pp, lang="fra", config=cfg, output_type=pytesseract.Output.DICT)
        else:
            raise

    words: list[dict] = []
    n = len(data.get("text", []) or [])
    for i in range(n):
        txt = str(data.get("text", [""])[i] or "").strip()
        if not txt:
            continue
        try:
            conf = float(data.get("conf", ["-1"])[i])
            if conf < 0:
                continue
        except Exception:
            pass
        try:
            x = float(data.get("left", [0])[i])
            y = float(data.get("top", [0])[i])
            w = float(data.get("width", [0])[i])
            h = float(data.get("height", [0])[i])
            if w <= 0 or h <= 0:
                continue
            words.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "text": txt})
        except Exception:
            continue
    return words, int(img_pp.width), int(img_pp.height)


def find_field_positions(
    document_path: str,
    field_values: dict[str, str],
    *,
    allow_tesseract_fallback: bool = False,
    max_pages: int | None = None,
) -> dict[str, dict]:
    """Retourne les positions visuelles des champs principaux.

    Format de retour volontairement optionnel et rétrocompatible : les anciens
    JSON peuvent ne pas avoir `field_positions`; l'écran l'ignore simplement.

    Coordonnées normalisées par rapport à la page non tournée :
    {"iban": {"page": 0, "x": 0.12, "y": 0.34, "w": 0.20, "h": 0.02}}
    """
    if not document_path or not os.path.exists(document_path):
        return {}

    wanted: dict[str, dict] = {}
    for k, v in (field_values or {}).items():
        key = str(k or "").strip()
        value = str(v or "").strip()
        if not key or not value:
            continue
        variants = _field_position_value_variants(key, value)
        if variants:
            wanted[key] = {"value": value, "variants": variants}
    if not wanted:
        return {}

    found: dict[str, dict] = {}
    ext = os.path.splitext(str(document_path or ""))[1].lower()

    if ext == ".pdf":
        doc = fitz.open(document_path)
        try:
            total_pages = len(doc)
            if max_pages is not None and max_pages >= 1:
                total_pages = min(total_pages, max_pages)

            # 1) PDF texte natif : rapide et fiable.
            for page_index in range(total_pages):
                missing = {k: v for k, v in wanted.items() if k not in found}
                if not missing:
                    break
                page = doc.load_page(page_index)
                page_w = float(page.rect.width or 1.0)
                page_h = float(page.rect.height or 1.0)
                words = _pdf_native_words_for_page(page)
                if not words:
                    continue
                for key, spec in missing.items():
                    original_value = str((spec or {}).get("value") or "")
                    for variant in list((spec or {}).get("variants") or []):
                        rect = _find_value_rect_in_words(words, variant)
                        if rect:
                            found[key] = _rect_to_normalized_position(page_index, rect, page_w, page_h, source="pdf_text", value=original_value)
                            break

            # 2) PDF image/scanné : fallback Tesseract uniquement si demandé.
            if allow_tesseract_fallback and len(found) < len(wanted):
                for page_index in range(total_pages):
                    missing = {k: v for k, v in wanted.items() if k not in found}
                    if not missing:
                        break
                    page = doc.load_page(page_index)
                    image = _image_from_fitz_page(page, _OCR_DPI)
                    words, img_w, img_h = _tesseract_words_for_image(image)
                    if not words:
                        continue
                    for key, spec in missing.items():
                        original_value = str((spec or {}).get("value") or "")
                        for variant in list((spec or {}).get("variants") or []):
                            rect = _find_value_rect_in_words(words, variant)
                            if rect:
                                found[key] = _rect_to_normalized_position(page_index, rect, img_w, img_h, source="tesseract", value=original_value)
                                break
        finally:
            doc.close()

    elif ext in _IMAGE_EXTENSIONS and allow_tesseract_fallback:
        with Image.open(document_path) as img:
            words, img_w, img_h = _tesseract_words_for_image(img.convert("RGB"))
        for key, spec in wanted.items():
            original_value = str((spec or {}).get("value") or "")
            for variant in list((spec or {}).get("variants") or []):
                rect = _find_value_rect_in_words(words, variant)
                if rect:
                    found[key] = _rect_to_normalized_position(0, rect, img_w, img_h, source="tesseract", value=original_value)
                    break

    return found

