import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from collections import Counter

from .supplier_model import extract_iban_candidates, validate_iban, validate_bic
from .folder_patterns import (
    DOSSIER_PATTERN,
    DOSSIER_CANDIDATE,
    normalize_folder_candidate,
    is_valid_folder_number,
    extract_folder_numbers_from_text,
)
from app.settings import load_settings


@dataclass
class InvoiceData:
    iban: str = ""
    bic: str = ""
    invoice_date: str = ""
    invoice_number: str = ""
    folder_number: str = ""
    folder_numbers: List[str] = field(default_factory=list)
    vat_lines: List[Dict[str, str]] = field(default_factory=list)
    vat_total: Optional[float] = None


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

BASE_LABEL_RE = re.compile(r"(\bBASE\s*HT\b|\bBASE\s*H\.?T\.?\b|\bNET\s*HT\b|\bTAXABLE\s+BASE\b)", re.IGNORECASE)
VAT_LABEL_RE = re.compile(r"(\bMONTANT\s*(?:DE\s*)?TVA\b|\bTOTAL\s*TVA\b|\bVAT\s+AMOUNT\b|\bAMOUNT\s+VAT\b)", re.IGNORECASE)
RATE_LABEL_RE = re.compile(r"(\bTAUX(?:\s+DE)?\s*TVA\b|\bVAT\s+RATE\b|\bRATE\s+VAT\b|\bTVA\s*%\b)", re.IGNORECASE)

VAT_RATE_RE = re.compile(r"(?P<rate>\d{1,2}(?:[.,]\d{1,2})?)\s*%")
MONEY_RE = re.compile(r"\b\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\b")

ONLY_AMOUNT_RE = re.compile(
    r"^\s*\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\s*(?:€|EUR)?\s*$",
    re.IGNORECASE
)

VAT_BLOCK_HINT_RE = re.compile(
    r"(\bTAUX(?:\s+DE)?\s*TVA\b|\bBASE\s*HT\b|\bMONTANT\s*(?:DE\s*)?TVA\b|\bTOTAL\s*TVA\b|\bVAT\s+RATE\b|\bVAT\s+AMOUNT\b|\bTAXABLE\s+BASE\b)",
    re.IGNORECASE
)


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
        r"NUMER\s+FAKTURY"
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
        r"NUMER\s+FAKTURY"
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
# TVA
# =========================

# Variable globale pour les taux autorisés du fournisseur actuel
_current_allowed_vat_rates: set[float] | None = None

def _get_allowed_vat_rates() -> List[float]:
    settings = load_settings()
    allowed = []
    raw = (settings.get("vat") or {}).get("allowed_rates")
    if isinstance(raw, list):
        for r in raw:
            try:
                allowed.append(float(r))
            except Exception:
                pass
    if not allowed:
        allowed = [0.0, 20.0]
    return allowed


def _is_allowed_vat_rate(rate: float | str | None) -> bool:
    global _current_allowed_vat_rates
    if rate is None:
        return False
    try:
        r = float(str(rate).replace(",", "."))
    except Exception:
        return False
    
    if _current_allowed_vat_rates is not None:
        # Utiliser les taux du modèle fournisseur
        return r in _current_allowed_vat_rates
    else:
        # Fallback aux settings
        allowed = _get_allowed_vat_rates()
        # map with tolerance
        for ar in allowed:
            if abs(r - ar) < 1e-6:
                return True
        return False


def _norm_amount_str(s: str) -> str:
    return (s or "").replace("\u00A0", "").replace(" ", "").strip()


def _to_float(s: str):
    s = _norm_amount_str(s).replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _clean_vat_number(raw: str) -> str:
    s = (raw or "").strip()
    s = s.replace("€", "").replace("EUR", "").replace("eur", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_plausible_money_value(raw: str) -> bool:
    s = _clean_vat_number(raw)
    if not s:
        return False
    compact = re.sub(r"[\s\u00A0]", "", s)
    if re.fullmatch(r"\d{8,}", compact):
        return False
    if not any(ch in s for ch in ",.") and len(compact) > 6:
        return False
    v = _to_float(s)
    if v is None or v < 0:
        return False
    return True


def _build_valid_vat_line(rate: str = "", base: str = "", vat: str = ""):
    rate = (rate or "").replace("%", "").strip().replace(",", ".")
    base = _clean_vat_number(base)
    vat = _clean_vat_number(vat)

    rv = _to_float(rate) if rate else None
    bv = _to_float(base) if base else None
    vv = _to_float(vat) if vat else None

    if rv is not None and not _is_allowed_vat_rate(rv):
        return None
    if base and not _is_plausible_money_value(base):
        return None
    if vat and not _is_plausible_money_value(vat):
        return None

    if rv is not None and bv is not None and vv is None and rv > 0:
        vv = round((bv * rv) / 100.0, 2)
        vat = f"{vv:.2f}".replace(".", ",")
    elif rv is not None and vv is not None and bv is None and rv > 0:
        bv = round((vv * 100.0) / rv, 2)
        base = f"{bv:.2f}".replace(".", ",")

    if rv is None or bv is None or vv is None:
        return None
    if bv <= 0:
        return None

    expected = (bv * rv) / 100.0
    tol = max(0.06, expected * 0.03)
    if abs(vv - expected) > tol:
        return None

    return {
        "rate": (f"{rv:.2f}".rstrip("0").rstrip(".")).replace(".", "."),
        "base": base,
        "vat": vat,
    }


def _is_structural_vat_label(line: str) -> bool:
    up = (line or "").upper()
    if not up:
        return False
    bad = ("N° TVA", "NO TVA", "TVA INTRA", "INTRACOMMUNAUTAIRE", "VAT NO", "VAT N°", "N TVA")
    if any(b in up for b in bad):
        return False
    return bool(VAT_BLOCK_HINT_RE.search(up))


def _extract_vat_vertical_table(lines: list[str]) -> dict | None:
    n = len(lines)
    for i in range(n - 5):
        l1, l2, l3 = lines[i], lines[i + 1], lines[i + 2]
        if RATE_LABEL_RE.search(l1) and VAT_LABEL_RE.search(l2) and BASE_LABEL_RE.search(l3):
            return _build_valid_vat_line(lines[i + 3], lines[i + 5], lines[i + 4])
        if BASE_LABEL_RE.search(l1) and VAT_LABEL_RE.search(l2) and RATE_LABEL_RE.search(l3):
            return _build_valid_vat_line(lines[i + 5], lines[i + 3], lines[i + 4])
        if RATE_LABEL_RE.search(l1) and BASE_LABEL_RE.search(l2) and VAT_LABEL_RE.search(l3):
            return _build_valid_vat_line(lines[i + 3], lines[i + 4], lines[i + 5])
    return None


def parse_vat_lines(text: str, allowed_vat_rates: set[float] | None = None, model: dict | None = None):
    global _current_allowed_vat_rates
    _current_allowed_vat_rates = allowed_vat_rates
    lines_out = []
    seen = set()

    # 0) Utiliser les patterns du modèle supplier si disponible
    if model and "patterns" in model:
        patterns = model["patterns"]
        vat_rate_patterns = patterns.get("vat_rate", [])
        vat_base_patterns = patterns.get("vat_base", [])
        vat_amount_patterns = patterns.get("vat_amount", [])

        if vat_rate_patterns or vat_base_patterns or vat_amount_patterns:
            from .supplier_model import extract_fields_with_model
            found = extract_fields_with_model(text, model)

            rate = found.get("vat_rate", "")
            base = found.get("vat_base", "")
            vat = found.get("vat_amount", "")

            if rate or base or vat:
                row = _build_valid_vat_line(rate, base, vat)
                if row:
                    lines_out.append(row)

    # 1) format ligne TVA directe
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        if "tva" not in line.lower():
            continue

        m_rate = VAT_RATE_RE.search(line)
        if not m_rate:
            continue

        rate = m_rate.group("rate").replace(",", ".")
        if not _is_allowed_vat_rate(rate):
            continue

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

        row = _build_valid_vat_line(rate, base, vat)
        if not row:
            continue
        key = (row["rate"], row["base"], row["vat"])
        if key in seen:
            continue
        seen.add(key)

        lines_out.append(row)

    # 2) format table TVA
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    vertical_table = _extract_vat_vertical_table(lines)
    label_table = vertical_table or _extract_vat_by_labels(lines)

    centers = [i for i, ln in enumerate(lines) if _is_structural_vat_label(ln)]

    best_table = None
    best_score = -1

    for c in centers[:40]:
        res = _infer_vat_line_from_block(lines, c, window=25)
        if not res:
            continue

        score = 0
        score += 10 if res.get("rate") else 0
        score += 10 if res.get("base") else 0
        score += 10 if res.get("vat") else 0

        if score > best_score:
            best_score = score
            best_table = res

    chosen = label_table if label_table else best_table

    if chosen:
        row = _build_valid_vat_line(chosen.get("rate", ""), chosen.get("base", ""), chosen.get("vat", ""))
        if row:
            key = (row["rate"], row["base"], row["vat"])
            if key not in seen:
                seen.add(key)
                lines_out.append(row)

    if best_table:
        row = _build_valid_vat_line(best_table.get("rate", ""), best_table.get("base", ""), best_table.get("vat", ""))
        if row:
            key = (row["rate"], row["base"], row["vat"])
            if key not in seen:
                seen.add(key)
                lines_out.append(row)

    # 3) Fusionner les lignes TVA avec le même taux
    lines_out = _merge_duplicate_vat_lines(lines_out)

    return lines_out


# =========================
# PARSER PRINCIPAL
# =========================

def parse_invoice(text: str) -> InvoiceData:
    # Extraire IBAN/BIC d'abord pour charger le modèle fournisseur
    iban = extract_iban(text)
    bic = extract_bic(text)
    
    # Charger modèle fournisseur si possible
    supplier_key = None
    allowed_vat_rates = None
    model = None
    if iban or bic:
        from .supplier_model import build_supplier_key, load_supplier_model
        supplier_key = build_supplier_key(iban, bic)
        if supplier_key:
            model = load_supplier_model(supplier_key)
            if model and "patterns" in model:
                vat_rates_patterns = model["patterns"].get("vat_rates", [])
                if vat_rates_patterns:
                    allowed_vat_rates = {p["rate"] for p in vat_rates_patterns}
    
    # Parser TVA avec filtrage par modèle fournisseur
    vat_lines = parse_vat_lines(text, allowed_vat_rates=allowed_vat_rates, model=model)
    
    vat_total = 0.0
    has_any = False
    for row in vat_lines:
        v = _to_float(row.get("vat", ""))
        if v is not None:
            vat_total += v
            has_any = True
    vat_total = vat_total if has_any else None

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
        vat_lines=vat_lines,
        vat_total=vat_total,
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


def _infer_vat_line_from_block(lines: list[str], center: int, window: int = 25):
    start = max(0, center - window)
    end = min(len(lines), center + window + 1)

    idx_taux = next((i for i in range(start, end) if "TAUX" in lines[i].upper()), None)
    idx_base = next((i for i in range(start, end) if "BASE" in lines[i].upper()), None)
    idx_mtv = next(
        (
            i for i in range(start, end)
            if "MONTANT TVA" in lines[i].upper() or "TOTAL TVA" in lines[i].upper()
        ),
        None,
    )

    amounts = []
    for i in range(start, end):
        ln = (lines[i] or "").strip()
        if not ln:
            continue
        for s in MONEY_RE.findall(ln):
            v = _to_float(s)
            if v is None:
                continue
            amounts.append((v, _norm_amount_str(s), i, bool(ONLY_AMOUNT_RE.match(ln))))

    if not amounts:
        return None

    rate_cands = [a for a in amounts if _is_allowed_vat_rate(a[0])]
    if not rate_cands:
        return None

    best = None
    for rv, rstr, ri, r_only in rate_cands:
        for bv, bstr, bi, b_only in amounts:
            if bv <= 0 or bv < 10:
                continue

            expected = (bv * rv) / 100.0

            for vv, vstr, vi, v_only in amounts:
                if vv <= 0:
                    continue

                diff = abs(vv - expected)
                tol = max(0.06, expected * 0.03)
                if diff > tol:
                    continue

                score = 0
                score += max(0, 60 - diff * 200)
                score += 25 if r_only else 0
                score += 25 if b_only else 0
                score += 35 if v_only else 0

                if idx_taux is not None:
                    score += max(0, 20 - abs(ri - idx_taux) * 2)
                if idx_base is not None:
                    score += max(0, 20 - abs(bi - idx_base) * 2)
                if idx_mtv is not None:
                    score += max(0, 30 - abs(vi - idx_mtv) * 2)

                cand = (score, rstr, bstr, vstr)
                if best is None or cand[0] > best[0]:
                    best = cand

    if not best:
        return None

    _, rstr, bstr, vstr = best
    return _build_valid_vat_line(rstr, bstr, vstr)


def _money_in_line_or_next(lines: list[str], idx: int) -> str:
    if idx is None or idx < 0 or idx >= len(lines):
        return ""

    ln = lines[idx].strip()

    m = MONEY_RE.search(ln)
    if m:
        return _norm_amount_str(m.group(0))

    # Chercher dans les lignes suivantes (jusqu'à 4 lignes plus loin)
    for offset in range(1, 5):
        if idx + offset >= len(lines):
            break
        ln_next = (lines[idx + offset] or "").strip()
        m_next = MONEY_RE.search(ln_next)
        if m_next:
            return _norm_amount_str(m_next.group(0))

    return ""


def _rate_in_line_or_next(lines: list[str], idx: int) -> str:
    if idx is None or idx < 0 or idx >= len(lines):
        return ""

    ln = lines[idx].strip()

    m = VAT_RATE_RE.search(ln)
    if m:
        return m.group("rate").replace(",", ".")

    def pick_rate_from_text(t: str) -> str:
        cands = []
        for s in MONEY_RE.findall(t):
            v = _to_float(s)
            if v is not None and _is_allowed_vat_rate(v):
                cands.append((v, _norm_amount_str(s)))
        if not cands:
            return ""
        cands.sort(key=lambda x: x[0], reverse=True)
        return cands[0][1].replace(",", ".")

    r = pick_rate_from_text(ln)
    if r:
        return r

    # Chercher dans les lignes suivantes (jusqu'à 4 lignes plus loin)
    for offset in range(1, 5):
        if idx + offset >= len(lines):
            break
        ln_next = (lines[idx + offset] or "").strip()
        r_next = pick_rate_from_text(ln_next)
        if r_next:
            return r_next

    return ""


def _merge_duplicate_vat_lines(lines: list[dict]) -> list[dict]:
    """Fusionne les lignes TVA avec le même taux, gardant celle avec le plus d'informations."""
    if not lines:
        return lines

    # Grouper par taux
    by_rate: dict[str, list[dict]] = {}
    for line in lines:
        rate = line.get("rate", "").strip()
        if rate:
            if rate not in by_rate:
                by_rate[rate] = []
            by_rate[rate].append(line)

    merged = []
    for rate, group in by_rate.items():
        if len(group) == 1:
            merged.append(group[0])
            continue

        # Plusieurs lignes avec le même taux - garder la meilleure
        best_line = None
        best_score = -1

        for line in group:
            score = 0
            if line.get("base", "").strip():
                score += 2  # Base HT présente = très important
            if line.get("vat", "").strip():
                score += 2  # Montant TVA présent = très important
            if line.get("rate", "").strip():
                score += 1  # Taux présent = important

            # Bonus si les valeurs sont cohérentes
            base_val = _to_float(line.get("base", ""))
            vat_val = _to_float(line.get("vat", ""))
            rate_val = _to_float(line.get("rate", ""))

            if base_val and vat_val and rate_val:
                expected_vat = (base_val * rate_val) / 100.0
                if abs(vat_val - expected_vat) < 0.01:  # Tolérance de 1 centime
                    score += 3  # Valeurs cohérentes = excellent

            if score > best_score:
                best_score = score
                best_line = line

        if best_line:
            merged.append(best_line)

    return merged


def _extract_vat_by_labels(lines: list[str]) -> dict | None:
    if not lines:
        return None

    vertical = _extract_vat_vertical_table(lines)
    if vertical:
        return vertical

    rate_idxs = [i for i, ln in enumerate(lines) if RATE_LABEL_RE.search(ln) and _is_structural_vat_label(ln)]
    base_idxs = [i for i, ln in enumerate(lines) if BASE_LABEL_RE.search(ln) and _is_structural_vat_label(ln)]
    vat_idxs = [i for i, ln in enumerate(lines) if VAT_LABEL_RE.search(ln) and _is_structural_vat_label(ln)]

    best = None
    best_span = 10**9
    for ri in rate_idxs:
        for bi in base_idxs:
            for vi in vat_idxs:
                span = max(ri, bi, vi) - min(ri, bi, vi)
                if span > 8:
                    continue
                if span < best_span:
                    best_span = span
                    best = (ri, bi, vi)

    if not best:
        return None

    ri, bi, vi = best
    rate = _rate_in_line_or_next(lines, ri)
    base = _money_in_line_or_next(lines, bi)
    vat = _money_in_line_or_next(lines, vi)

    if not rate:
        search_window = " ".join(lines[min(ri, bi, vi): min(len(lines), max(ri, bi, vi) + 4)])
        m = VAT_RATE_RE.search(search_window)
        if m:
            rate = m.group("rate").replace(",", ".")

    return _build_valid_vat_line(rate, base, vat)
