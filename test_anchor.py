from pdf2image import convert_from_path
import pytesseract
import re
from ocr.anchor_extractor import extract_iban

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
)

from ocr.anchor_extractor import ocr_words_with_positions, extract_by_anchor

POPPLER_PATH = r"C:\poppler\Library\bin"

# Mets ici un PDF de test
pdf_path = r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles\2025-56994.pdf"

# OCR page 1 -> image
images = convert_from_path(
    pdf_path,
    dpi=150,
    poppler_path=POPPLER_PATH,
    first_page=1,
    last_page=1
)

img = images[0]

words = ocr_words_with_positions(img)

# Exemple: chercher un truc du type "Date" puis extraire à droite
date_value = extract_by_anchor(
    words,
    anchor_texts=["date"],
    direction="below",
    max_distance=50,
    regex=r"\d{4}-\d{2}-\d{2}"
)

print("DATE TROUVÉE:", date_value)


invoice_number = extract_by_anchor(
    words,
    anchor_texts=["inv.", "invoice"],
    direction="below",
    max_distance=80,
    regex=r"\b\d{4}\s*[-/]\s*\d{4,}\b"
)



iban = extract_iban(words)
print("IBAN TROUVÉ:", iban)

from ocr.anchor_extractor import extract_bic

bic = extract_bic(words)
print("BIC TROUVÉ:", bic)

print("INVOICE TROUVÉ:", invoice_number)




