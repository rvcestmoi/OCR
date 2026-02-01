import re
from dataclasses import dataclass


@dataclass
class InvoiceData:
    iban: str = ""
    bic: str = ""
    invoice_date: str = ""
    invoice_number: str = ""
    folder_number: str = ""


# =========================
# REGEX ROBUSTES
# =========================

IBAN_REGEX = re.compile(
    r'\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b'
)

BIC_REGEX = re.compile(
    r'\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?\b'
)

BIC_BLACKLIST = {
    "LOGISTIK", "TRANSPORT", "MODE", "REGLEMENT",
    "PAYMENT", "INVOICE", "FACTURE", "BANK", "IBAN", "BIC"
}


# =========================
# EXTRACTIONS
# =========================

def extract_iban(text: str) -> str:
    t = _normalize_ocr(text)

    # 1️⃣ priorité : IBAN proche du label "IBAN"
    for m in re.finditer(r'\bIBAN\b', t, flags=re.IGNORECASE):
        start = m.end()
        chunk = t[start:start + 200]

        # on enlève séparateurs courants
        chunk = chunk.replace(":", " ").replace("-", " ")

        candidates = IBAN_REGEX.findall(chunk)
        for iban in candidates:
            iban = iban.replace(" ", "")
            if 15 <= len(iban) <= 34:
                return iban

    # 2️⃣ fallback global (OCR-safe)
    cleaned = t.replace(" ", "").replace("-", "")
    matches = IBAN_REGEX.findall(cleaned)

    matches = [x for x in matches if 15 <= len(x) <= 34]
    if not matches:
        return ""

    # le plus long est quasi toujours le bon
    matches.sort(key=len, reverse=True)
    return matches[0]



def extract_bic(text: str) -> str:
    t = _normalize_ocr(text)

    # On ne cherche un BIC QUE s'il est explicitement labellisé
    bic = _find_best_match_near_label(
        t,
        label_patterns=[
            r'\bBIC\b',
            r'\bSWIFT\b',
            r'\bB\.?I\.?C\.?\b'
        ],
        value_regex=BIC_REGEX,
        window=120
    )

    if not bic:
        return ""

    # Nettoyage final
    bic = bic.strip()

    # Validation stricte ISO
    if len(bic) not in (8, 11):
        return ""

    # Blacklist finale ultra défensive
    if bic in BIC_BLACKLIST:
        return ""

    # Sécurité : un BIC commence toujours par 4 lettres (code banque)
    if not bic[:4].isalpha():
        return ""

    return bic



def extract_date(text: str) -> str:
    match = re.search(r'\b\d{2}[./-]\d{2}[./-]\d{4}\b', text)
    return match.group() if match else ""


def extract_invoice_number(text: str) -> str:
    match = re.search(
        r'(Invoice|Inv\.?|Facture)[^\w]{0,10}([A-Z0-9\-_/\.]{3,})',
        text,
        re.IGNORECASE
    )
    return match.group(2) if match else ""


def extract_folder_number(text: str) -> str:
    match = re.search(
        r'(N°|No\.?|LS:|Dossier)[^\d]{0,5}(\d{6,})',
        text,
        re.IGNORECASE
    )
    return match.group(2) if match else ""


# =========================
# PARSER PRINCIPAL
# =========================

def parse_invoice(text: str) -> InvoiceData:
    data = InvoiceData()

    data.iban = extract_iban(text)
    data.bic = extract_bic(text)
    data.invoice_date = extract_date(text)
    data.invoice_number = extract_invoice_number(text)
    data.folder_number = extract_folder_number(text)

    return data

def _normalize_ocr(text: str) -> str:
    """
    Normalisation légère du texte OCR :
    - majuscules
    - suppression caractères invisibles
    """
    t = text.upper()
    t = t.replace("\u00A0", " ")   # espace insécable
    t = t.replace("\n", " ")
    return t


def _find_best_match_near_label(
    text: str,
    label_patterns: list[str],
    value_regex: re.Pattern,
    *,
    window: int = 120
) -> str:
    """
    Cherche une valeur (IBAN/BIC) dans une fenêtre de caractères
    juste après un label (IBAN, BIC, SWIFT, etc.)
    """
    for pat in label_patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            start = m.end()
            chunk = text[start:start + window]

            # nettoyage léger
            chunk = chunk.replace(":", " ").replace("=", " ")

            mm = value_regex.search(chunk)
            if mm:
                return mm.group(0)

    return ""
