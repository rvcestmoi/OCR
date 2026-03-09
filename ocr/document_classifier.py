import re

NON_INVOICE_HINTS = [
    r"\bCMR\b",
    r"\bDELIVERY\s+NOTE\b",
    r"\bDELIVERY\s+NO\b",
    r"\bPACKING\s+LIST\b",
    r"\bFORWARDER\b",
]

INVOICE_HINTS = [
    r"\bFACTURE\b",
    r"\bINVOICE\b",
    r"\bTOTAL\s+TVA\b",
    r"\bMONTANT\s+TVA\b",
    r"\bBASE\s+HT\b",
    r"\bTVA\b",
]

def classify_document_text(text: str) -> str:
    t = (text or "").upper()

    non_invoice = sum(bool(re.search(p, t, re.IGNORECASE)) for p in NON_INVOICE_HINTS)
    invoice = sum(bool(re.search(p, t, re.IGNORECASE)) for p in INVOICE_HINTS)

    if non_invoice >= 2 and invoice == 0:
        return "cmr"
    if invoice >= 2:
        return "invoice"
    return "unknown"