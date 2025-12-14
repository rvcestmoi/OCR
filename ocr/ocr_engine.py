import pytesseract
from pdf2image import convert_from_path
import os
import tempfile
import fitz

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
)

POPPLER_PATH = r"C:\poppler\Library\bin"


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
        images = convert_from_path(
            pdf_path,
            dpi=150,  # ⚡ plus rapide que 300
            output_folder=temp_dir,
            fmt="png",
            poppler_path=POPPLER_PATH
        )

        for idx, image in enumerate(images):

            # Tentative rapide en anglais d'abord
            text = pytesseract.image_to_string(
                image,
                lang="fra",
                config="--psm 11"
            )

            # Si résultat pauvre → fallback multi-langues
            if len(text.strip()) < 50:
                text = pytesseract.image_to_string(
                    image,
                    lang="fra+eng+deu+spa+ita+nld",
                    config="--psm 11"
                )

            full_text += f"\n\n===== PAGE {idx + 1} =====\n{text}"

    return full_text.strip()




def extract_text_fast(pdf_path: str) -> str:
    doc = fitz.open(pdf_path)
    text = ""

    for page in doc:
        text += page.get_text()

    doc.close()
    return text.strip()

