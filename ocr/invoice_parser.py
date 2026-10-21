import re
from dataclasses import dataclass, field
from typing import List, Optional

from .supplier_model import extract_iban_candidates, validate_iban, validate_bic
from .folder_patterns import (
    DOSSIER_PATTERN,
    DOSSIER_CANDIDATE,
    normalize_folder_candidate,
    is_valid_folder_number,
    extract_folder_numbers_from_text,
)


@dataclass
class InvoiceData:
    iban: str = ""
    bic: str = ""
    invoice_date: str = ""
    invoice_number: str = ""
    folder_number: str = ""
    folder_numbers: List[str] = field(default_factory=list)


# =========================
# REGEX ROBUSTES
# =========================

IBAN_REGEX = re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b")
IBAN_CANDIDATE_REGEX = re.compile(
    r"\b[A-Z]{2}[ \u00A0-]*\d{2}(?:[ \u00A0-]*[A-Z0-9]){11,30}\b",
    re.IGNORECASE,
)

BIC_REGEX = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b")

BIC_BLACKLIST = {
    "LOGISTIK", "TRANSPORT", "MODE", "REGLEMENT", "PAYMENT", "INVOICE", "FACTURE",
    "BANK", "IBAN", "BIC", "SWIFT", "DETAILDE", "DETAILDU", "VIREMENT", "ECHEANCE"
}



# =========================
# DOSSIERS
# =========================

def _clean_dossier_candidate(s: str) -> str:
    return normalize_folder_candidate(s)


def extract_folder_numbers(text: str) -> List[str]:
    """
    Extrait tous les numéros de dossier valides.
    Source unique de vérité = ocr/folder_patterns.py
    """
    return extract_folder_numbers_from_text(text or "")


def extract_folder_number(text: str) -> Optional[str]:
    nums = extract_folder_numbers(text)
    return nums[0] if nums else None


def _normalize_for_folder_search(text: str) -> str:
    """
    Compat :
    - ne colle pas à travers les retours ligne
    - ne colle que les séparateurs visuels internes entre chiffres
    """
    t = (text or "").replace("\u00A0", " ")
    t = re.sub(r"(?<=\d)[ \u00A0-]+(?=\d)", "", t)
    return t


# =========================
# EXTRACTIONS
# =========================

OCR_DIGIT_FIX = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "G": "6",
    "T": "7",
}


def _fix_iban_ocr(iban: str) -> str:
    """
    Corrige les confusions OCR courantes, surtout sur les IBAN FR.
    Retourne "" si impossible.
    """
    s = (
        (iban or "")
        .replace(" ", "")
        .replace("\u00A0", "")
        .replace("-", "")
        .upper()
        .strip()
    )
    if len(s) < 6:
        return ""

    country = s[:2]

    if country == "FR":
        head = s[:4]
        rest = s[4:]
        rest2 = "".join(OCR_DIGIT_FIX.get(ch, ch) for ch in rest)
        if not rest2.isdigit():
            return ""
        return head + rest2

    return ""


def extract_iban(text: str) -> str:
    counts = extract_iban_candidates(text or "", prefer_labels=True)
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def extract_bic(text: str) -> str:
    t = _normalize_ocr(text)

    bic = _find_best_match_near_label(
        t,
        label_patterns=[
            r"\bBIC\b",
            r"\bSWIFT\b",
            r"\bB\.?I\.?C\.?\b",
            r"\bCODE\s+SWIFT\b",
            r"\bCOD[EF]\s+SWIFT\b",
            r"\bCONF\s+SWIFT\b",
        ],
        value_regex=BIC_REGEX,
        window=120,
    )

    if not bic:
        return ""

    bic = bic.strip().replace(" ", "")
    if bic in BIC_BLACKLIST:
        return ""
    if not validate_bic(bic):
        return ""

    return bic


def extract_date(text: str) -> str:
    src = text or ""
    if not src:
        return ""

    date_rx = re.compile(r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})\b")
    strong_label_rx = re.compile(
        r"(?:DATE\s+D['’]?[ÉE]MISSION|DATE\s+FACTURE|INVOICE\s+DATE|ISSUED?\s+DATE|DATUM)",
        re.IGNORECASE,
    )
    weak_label_rx = re.compile(r"\b(?:DATE|FACTURE|INVOICE)\b", re.IGNORECASE)
    due_hint_rx = re.compile(
        r"\b(?:[ÉE]CH[ÉE]ANCE|DUE\s+DATE|PAYMENT\s+DUE|PAYABLE|R[ÈE]GLEMENT|VIREMENT|MATURIT[ÉE])\b",
        re.IGNORECASE,
    )

    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]

    for idx, line in enumerate(lines):
        window = line
        if idx + 1 < len(lines):
            window += " " + lines[idx + 1]
        if strong_label_rx.search(window):
            m = date_rx.search(window)
            if m:
                return normalize_date_format(m.group(0))

    for idx, line in enumerate(lines):
        window = line
        if idx + 1 < len(lines):
            window += " " + lines[idx + 1]
        if due_hint_rx.search(window):
            continue
        if weak_label_rx.search(window):
            m = date_rx.search(window)
            if m:
                return normalize_date_format(m.group(0))

    fallback = ""
    for m in date_rx.finditer(src):
        ctx = src[max(0, m.start() - 80): min(len(src), m.end() + 80)]
        if due_hint_rx.search(ctx):
            if not fallback:
                fallback = m.group(0)
            continue
        return normalize_date_format(m.group(0))

    return normalize_date_format(fallback) if fallback else ""


def normalize_date_format(date_str: str) -> str:
    """
    Normalise une date au format jj/mm/aaaa.
    Accepte les formats courants : jj/mm/aaaa, jj.mm.aaaa, jj-mm-aaaa, aaaa-mm-jj, etc.
    """
    if not date_str:
        return ""
    
    # Nettoyer et splitter
    date_str = date_str.strip()
    separators = ['/', '.', '-']
    sep = None
    for s in separators:
        if s in date_str:
            sep = s
            break
    
    if not sep:
        return date_str
    
    parts = date_str.split(sep)
    if len(parts) != 3:
        return date_str
    
    try:
        p1, p2, p3 = [int(x.strip()) for x in parts]
    except ValueError:
        return date_str
    
    # Déterminer le format
    if p1 > 31:  # aaaa-mm-jj
        year, month, day = p1, p2, p3
    elif p3 > 31:  # jj-mm-aaaa ou mm-jj-aaaa
        year, month, day = p3, p2, p1
    else:  # jj-mm-aaaa ou mm-jj-aaaa
        if p2 <= 12:
            day, month, year = p1, p2, p3
        else:
            month, day, year = p1, p2, p3
    
    # Validation basique
    if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
        return date_str
    
    return f"{day:02d}/{month:02d}/{year:04d}"


def extract_invoice_number(text: str) -> str:
    if not text:
        return ""

    bad = {
        "DESCRIPTION", "DATE", "FACTURE", "INVOICE", "TOTAL", "MONTANT", "BASE",
        "CLIENT", "REFERENCE", "RÉFÉRENCE", "QTE", "QTÉ", "PU", "HT", "TVA", "TTC",
    }


    label_rx = re.compile(
        r"\b("
        r"NUM[EÉ]RO\s+DE\s+FACTURE|"
        r"NUMERO\s+DE\s+FACTURE|"
        r"N[°O]\s*FACTURE|"
        r"FACTURE\s*(N[°O]|NO\.?)|"
        r"INVOICE\s*(NO\.?|NUMBER)|"
        r"INV\.?\s*NO\.?|"
        r"NUMER\s+FAKTURY|"
        r"RECHNUNGS\s*[-.]?\s*(?:NR|NO|NUMMER)|"
        r"RECHNUNGSNUMMER|"
        r"BELEGNUMMER"
        r")\b",
        re.IGNORECASE,
    )

    token_rx = re.compile(r"\b[A-Z0-9][A-Z0-9\-_/\.]{2,}\b", re.IGNORECASE)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def ok(tok: str) -> bool:
        t = (tok or "").strip()
        if not t:
            return False
        if not any(ch.isdigit() for ch in t):
            return False
        if t.upper() in bad:
            return False
        if len(t) < 4 or len(t) > 40:
            return False
        return True

    for i, ln in enumerate(lines):
        m = label_rx.search(ln)
        if not m:
            continue

        after = ln[m.end():]
        for tok in token_rx.findall(after):
            if ok(tok):
                return tok

        for j in range(i + 1, min(i + 6, len(lines))):
            for tok in token_rx.findall(lines[j]):
                if ok(tok):
                    return tok

    m = re.search(
        r"(?:"
        r"NUM[EÉ]RO\s+DE\s+FACTURE|"
        r"NUMERO\s+DE\s+FACTURE|"
        r"N[°O]\s*FACTURE|"
        r"FACTURE\s*(?:N[°O]|NO\.?)|"
        r"INVOICE\s*(?:NO\.?|NUMBER)|"
        r"INV\.?\s*NO\.?|"
        r"NUMER\s+FAKTURY|"
        r"RECHNUNGS\s*[-.]?\s*(?:NR|NO|NUMMER)|"
        r"RECHNUNGSNUMMER|"
        r"BELEGNUMMER"
        r")\b[^\w]{0,20}([A-Z0-9\-_/\.]{3,})",
        text,
        re.IGNORECASE,
    )


    if m:
        cand = m.group(1).strip()
        if ok(cand):
            return cand
        
    try:
        from .field_detector import detect_fields_multilingual
        detected = detect_fields_multilingual(text or "")
        candidate = str((detected or {}).get("invoice_number") or "").strip()
        if candidate:
            return candidate
    except Exception:
        pass

    return ""


# =========================
# PARSER PRINCIPAL
# =========================

def parse_invoice(text: str) -> InvoiceData:
    # Extraire IBAN/BIC pour information uniquement. Le modèle transporteur
    # n'est plus chargé depuis IBAN/BIC : l'UI l'applique ensuite via le
    # KundenNr du premier dossier (xxatour.FFNR).
    iban = extract_iban(text)
    bic = extract_bic(text)

    folder_numbers = extract_folder_numbers(text)
    folder_number = folder_numbers[0] if folder_numbers else ""

    invoice_date = extract_date(text)
    invoice_number = extract_invoice_number(text)

    return InvoiceData(
        iban=iban,
        bic=bic,
        invoice_date=invoice_date,
        invoice_number=invoice_number,
        folder_number=folder_number,
        folder_numbers=folder_numbers,
    )


# =========================
# HELPERS
# =========================

def _normalize_ocr(text: str) -> str:
    t = (text or "").upper()
    t = t.replace("\u00A0", " ")
    t = t.replace("\n", " ")
    return t


def _find_best_match_near_label(
    text: str,
    label_patterns: list[str],
    value_regex: re.Pattern,
    *,
    window: int = 120,
) -> str:
    for pat in label_patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            start = m.end()
            chunk = text[start:start + window]
            chunk = chunk.replace(":", " ").replace("=", " ")

            mm = value_regex.search(chunk)
            if mm:
                return mm.group(0)

    return ""
