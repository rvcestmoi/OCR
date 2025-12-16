import re

def guess_field(text: str) -> str | None:
    text = text.strip().replace(" ", "").upper()
    scores = {}

    def add(field, value):
        scores[field] = scores.get(field, 0) + value

    # IBAN
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", text):
        add("iban", 100)

    # BIC
    if re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", text):
        add("bic", 90)

    # Date
    if re.search(r"\d{2}[./-]\d{2}[./-]\d{2,4}", text):
        add("date", 70)

    # Invoice number hints
    if re.search(r"(INV|INVOICE|FACT)", text):
        add("invoice_number", 40)

    # Numeric-only
    if text.isdigit():
        add("folder_number", 20)
        add("invoice_number", 20)

    # Length hints
    if 5 <= len(text) <= 12:
        add("invoice_number", 10)

    return max(scores, key=scores.get) if scores else None
