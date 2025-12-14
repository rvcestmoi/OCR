import pytesseract
from pdf2image import convert_from_path
import os
import tempfile

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
)

POPPLER_PATH = r"C:\poppler\Library\bin"


def extract_text_from_pdf(pdf_path: str) -> str:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF introuvable : {pdf_path}")

    full_text = ""

    with tempfile.TemporaryDirectory() as temp_dir:
        images = convert_from_path(
            pdf_path,
            dpi=300,
            output_folder=temp_dir,
            fmt="png",
            poppler_path=POPPLER_PATH
        )

        for idx, image in enumerate(images):
            text = pytesseract.image_to_string(
                image,
                lang="fra+eng+deu+spa+ita+nld",
                config="--psm 6"
            )
            full_text += f"\n\n===== PAGE {idx + 1} =====\n{text}"

    return full_text
