import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class InvoiceData:
    iban: str = ""
    bic: str = ""
    invoice_date: str = ""
    invoice_number: str = ""   # ✅ (pas de virgule)
    folder_number: str = ""    # compat (1er dossier)
    folder_numbers: List[str] = field(default_factory=list)  # ✅ multi-dossiers
    vat_lines: List[Dict[str, str]] = field(default_factory=list)
    vat_total: Optional[float] = None


# =========================
# REGEX ROBUSTES
# =========================

IBAN_REGEX = re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b")

BIC_REGEX = re.compile(r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?\b")

BIC_BLACKLIST = {
    "LOGISTIK", "TRANSPORT", "MODE", "REGLEMENT",
    "PAYMENT", "INVOICE", "FACTURE", "BANK", "IBAN", "BIC"
}

# =========================
# DOSSIERS (multi) - ROBUSTE
# =========================
# Règles:
# - 1 + 8 chiffres => 9
# - ou préfixes 84/25/35/44/64/67/69/72/78 + 6..8 chiffres => 8..10
DOSSIER_PATTERN = re.compile(
    r"(?<!\d)(?:1\d{8}|(?:84|25|35|44|64|67|69|72|78)\d{6,8})(?!\d)"
)

# Candidats potentiellement séparés par espace/NBSP/tiret (mais PAS \n)
DOSSIER_CANDIDATE = re.compile(
    r"(?<!\d)(?:1|84|25|35|44|64|67|69|72|78)[0-9 \u00A0-]{6,20}\d(?!\d)"
)


def _clean_dossier_candidate(s: str) -> str:
    return re.sub(r"[ \u00A0-]", "", s or "").strip()


def extract_folder_numbers(text: str) -> List[str]:
    """
    Extrait TOUS les numéros de dossier de façon robuste, sans casser le cas:
    2506710166\n35093233 (ne doit PAS être collé en 250671016635093233)
    """
    src = text or ""

    seen = set()
    out: List[str] = []

    # 1) match direct sur texte brut
    for m in DOSSIER_PATTERN.findall(src):
        if m not in seen:
            seen.add(m)
            out.append(m)

    # 2) match "candidat" avec séparateurs internes (espace/NBSP/tiret), sans traverser les \n
    for mm in DOSSIER_CANDIDATE.finditer(src):
        cleaned = _clean_dossier_candidate(mm.group(0))
        if DOSSIER_PATTERN.fullmatch(cleaned) and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)

    return out


def extract_folder_number(text: str) -> Optional[str]:
    nums = extract_folder_numbers(text)
    return nums[0] if nums else None


def _normalize_for_folder_search(text: str) -> str:
    """
    ✅ Conservée (compat), MAIS corrigée:
    - on NE colle PLUS à travers les retours ligne, uniquement espaces/NBSP/tirets.
    """
    t = (text or "").replace("\u00A0", " ")
    # colle seulement les séparateurs "visuels", pas \n
    t = re.sub(r"(?<=\d)[ \u00A0-]+(?=\d)", "", t)
    return t


# =========================
# EXTRACTIONS
# =========================

def extract_iban(text: str) -> str:
    t = _normalize_ocr(text)

    # 1️⃣ priorité : IBAN proche du label "IBAN"
    for m in re.finditer(r"\bIBAN\b", t, flags=re.IGNORECASE):
        start = m.end()
        chunk = t[start:start + 200]
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

    matches.sort(key=len, reverse=True)
    return matches[0]


def extract_bic(text: str) -> str:
    t = _normalize_ocr(text)

    bic = _find_best_match_near_label(
        t,
        label_patterns=[
            r"\bBIC\b",
            r"\bSWIFT\b",
            r"\bB\.?I\.?C\.?\b"
        ],
        value_regex=BIC_REGEX,
        window=120
    )

    if not bic:
        return ""

    bic = bic.strip()

    if len(bic) not in (8, 11):
        return ""

    if bic in BIC_BLACKLIST:
        return ""

    if not bic[:4].isalpha():
        return ""

    return bic


def extract_date(text: str) -> str:
    match = re.search(r"\b\d{2}[./-]\d{2}[./-]\d{4}\b", text)
    return match.group() if match else ""


def extract_invoice_number(text: str) -> str:
    match = re.search(
        r"(Invoice|Inv\.?|Facture)[^\w]{0,10}([A-Z0-9\-_/\.]{3,})",
        text,
        re.IGNORECASE
    )
    return match.group(2) if match else ""


# =========================
# TVA
# =========================

VAT_RATE_RE = re.compile(r"(?P<rate>\d{1,2}(?:[.,]\d{1,2})?)\s*%")
MONEY_RE = re.compile(r"\b\d{1,3}(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\b")


def _norm_amount_str(s: str) -> str:
    return (s or "").replace("\u00A0", "").replace(" ", "").strip()


def _to_float(s: str):
    s = _norm_amount_str(s).replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def parse_vat_lines(text: str):
    lines_out = []
    seen = set()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        low = line.lower()
        if "tva" not in low:
            continue

        m_rate = VAT_RATE_RE.search(line)
        if not m_rate:
            continue

        rate = m_rate.group("rate").replace(",", ".")
        amounts = MONEY_RE.findall(line)

        base = ""
        vat = ""

        if len(amounts) >= 2:
            base = _norm_amount_str(amounts[-2])
            vat = _norm_amount_str(amounts[-1])
        elif len(amounts) == 1:
            vat = _norm_amount_str(amounts[-1])
        else:
            continue

        key = (rate, base, vat)
        if key in seen:
            continue
        seen.add(key)

        lines_out.append({"rate": rate, "base": base, "vat": vat})

    return lines_out


# =========================
# PARSER PRINCIPAL
# =========================

def parse_invoice(text: str) -> InvoiceData:
    vat_lines = parse_vat_lines(text)

    vat_total = 0.0
    has_any = False
    for r in vat_lines:
        v = _to_float(r.get("vat", ""))
        if v is not None:
            vat_total += v
            has_any = True
    vat_total = vat_total if has_any else None

    folder_numbers = extract_folder_numbers(text)
    folder_number = folder_numbers[0] if folder_numbers else ""

    data = InvoiceData(
        iban=extract_iban(text),
        bic=extract_bic(text),
        invoice_date=extract_date(text),
        invoice_number=extract_invoice_number(text),
        folder_number=folder_number,
        folder_numbers=folder_numbers,
        vat_lines=vat_lines,
        vat_total=vat_total,
    )

    return data


# =========================
# HELPERS
# =========================

def _normalize_ocr(text: str) -> str:
    """
    Normalisation légère du texte OCR :
    - majuscules
    - suppression caractères invisibles
    """
    t = (text or "").upper()
    t = t.replace("\u00A0", " ")
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
            chunk = chunk.replace(":", " ").replace("=", " ")

            mm = value_regex.search(chunk)
            if mm:
                return mm.group(0)

    return ""