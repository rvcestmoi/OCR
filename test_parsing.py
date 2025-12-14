from ocr.ocr_engine import extract_text_from_pdf
from ocr.invoice_parser import parse_invoice

pdf = r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles\2025-56994.pdf"

text = extract_text_from_pdf(pdf)
data = parse_invoice(text)

print(data)
