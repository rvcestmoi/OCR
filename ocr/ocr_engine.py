import pytesseract
from pdf2image import convert_from_path
import os
import tempfile
import fitz
import re
import numpy as np
from PIL import Image

try:
    import cv2
except Exception:
    cv2 = None

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
)

POPPLER_PATH = r"C:\poppler\Library\bin"

_BAD_OCR_CHARS = set("□■▪▫█▌▐▎▍▏|¦│┃┆┇")
_BAD_OCR_RE = re.compile(r"[□■▪▫█▌▐▎▍▏|¦│┃┆┇]+")

def _clean_ocr_text(text: str) -> str:
    """Supprime les caractères parasites (carrés / barres) et les lignes quasi vides."""
    out = []
    for ln in (text or "").splitlines():
        raw = ln.rstrip("\n")
        if not raw.strip():
            continue

        bad_count = sum(1 for ch in raw if ch in _BAD_OCR_CHARS)
        if bad_count / max(1, len(raw)) > 0.45:
            # ligne majoritairement composée de barres/carrés -> on jette
            continue

        ln2 = _BAD_OCR_RE.sub(" ", raw)
        ln2 = re.sub(r"\s{2,}", " ", ln2).strip()
        if ln2:
            out.append(ln2)
    return "\n".join(out)


def _preprocess_image_for_ocr(pil_img: Image.Image) -> Image.Image:
    """
    Enlève les traits de tableaux (vertical/horizontal) pour éviter les "□" dans l'OCR.
    Si OpenCV n'est pas dispo, fallback simple.
    """
    gray = np.array(pil_img.convert("L"))

    if cv2 is None:
        # fallback simple : binarisation basique
        # (moins bon que la suppression de lignes)
        thr = 200
        bw = (gray > thr).astype(np.uint8) * 255
        return Image.fromarray(bw)

    # Binarisation Otsu
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Inversion pour que le texte/traits soient en "blanc" sur fond noir
    inv = 255 - th

    h, w = inv.shape[:2]

    # Détection traits horizontaux/verticaux via ouverture morpho
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, w // 35), 1))
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, h // 35)))

    hori = cv2.morphologyEx(inv, cv2.MORPH_OPEN, h_kernel, iterations=1)
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN, v_kernel, iterations=1)

    lines = cv2.bitwise_or(hori, vert)

    # Supprimer les lignes détectées
    inv2 = cv2.bitwise_and(inv, cv2.bitwise_not(lines))

    # Remettre en noir sur blanc
    cleaned = 255 - inv2
    return Image.fromarray(cleaned)


def extract_text_from_pdf(pdf_path: str) -> str:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF introuvable : {pdf_path}")

    # =========================
    # 1️⃣ Tentative extraction TEXTE NATIF (rapide)
    # =========================
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()

        # Si le PDF contient déjà du texte exploitable → on s'arrête là
        if len(text.strip()) > 100:
            return text.strip()

    except Exception:
        pass  # fallback OCR image

    # =========================
    # 2️⃣ OCR IMAGE (fallback)
    # =========================
    full_text = ""

    with tempfile.TemporaryDirectory() as temp_dir:
        convert_kwargs = {
            "pdf_path": pdf_path,
            "dpi": 200,
            "output_folder": temp_dir,
            "fmt": "png",
            "poppler_path": POPPLER_PATH,
        }
        if max_pages is not None and max_pages >= 1:
            convert_kwargs["first_page"] = 1
            convert_kwargs["last_page"] = max_pages

        images = convert_from_path(**convert_kwargs)

        for idx, image in enumerate(images):

            # Tentative rapide en anglais d'abord
            # ✅ Pré-traitement : enlève les traits (évite les carrés)
            img_pp = _preprocess_image_for_ocr(image)

            # ✅ config plus adaptée aux tableaux
            cfg_main = "--oem 3 --psm 6 -c preserve_interword_spaces=1"

            text = pytesseract.image_to_string(
                img_pp,
                lang="fra",
                config=cfg_main
            )

            # Si résultat pauvre → fallback multi-langues + psm plus permissif
            if len(text.strip()) < 50:
                text = pytesseract.image_to_string(
                    img_pp,
                    lang="fra+eng+deu+spa+ita+nld",
                    config="--oem 3 --psm 11 -c preserve_interword_spaces=1"
                )

            # ✅ Nettoyage des barres/carrés
            text = _clean_ocr_text(text)

            # Si résultat pauvre → fallback multi-langues
            if len(text.strip()) < 50:
                text = pytesseract.image_to_string(
                    image,
                    lang="fra+eng+deu+spa+ita+nld",
                    config="--psm 11"
                )

            full_text += f"\n\n===== PAGE {idx + 1} =====\n{text}"

    return full_text.strip()




def extract_text_from_pdf(pdf_path: str, max_pages: int | None = None) -> str:
    doc = fitz.open(pdf_path)
    text = ""

    pages = list(doc)
    if max_pages is not None:
        pages = pages[:max_pages]

    for page in pages:
        text += page.get_text()

    doc.close()
    return text.strip()

