import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from collections import Counter

from .supplier_model import validate_iban
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
    "LOGISTIK", "TRANSPORT", "MODE", "REGLEMENT",
    "PAYMENT", "INVOICE", "FACTURE", "BANK", "IBAN", "BIC"
}

BASE_LABEL_RE = re.compile(r"(BASE\s*HT|TOTAL\s*HT|TOTAL\s*HT\s*NET)", re.IGNORECASE)
VAT_LABEL_RE = re.compile(r"(MONTANT\s*TVA|TOTAL\s*TVA)", re.IGNORECASE)
RATE_LABEL_RE = re.compile(r"(\bTAUX\b|%?\s*TVA\b)", re.IGNORECASE)

VAT_RATE_RE = re.compile(r"(?P<rate>\d{1,2}(?:[.,]\d{1,2})?)\s*%")
MONEY_RE = re.compile(r"\b\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\b")

ONLY_AMOUNT_RE = re.compile(
    r"^\s*\d+(?:[ \u00A0]\d{3})*(?:[.,]\d{2})\s*(?:€|EUR)?\s*$",
    re.IGNORECASE
)

VAT_BLOCK_HINT_RE = re.compile(
    r"(MONTANT\s*TVA|TOTAL\s*TVA|TAUX|BASE\s*HT|TVA)",
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
    # Ne pas remplacer les \n par des espaces ici
    src = (text or "").upper().replace("\u00A0", " ")

    def _norm_iban(s: str) -> str:
        return re.sub(r"[ \u00A0-]", "", s or "").upper().strip()

    # 1) priorité : proche du label IBAN
    for m in re.finditer(r"\bIBAN\b", src, flags=re.IGNORECASE):
        chunk = src[m.end(): m.end() + 260].replace(":", " ").replace("=", " ")
        for mm in IBAN_CANDIDATE_REGEX.finditer(chunk):
            iban = _norm_iban(mm.group(0))
            if 15 <= len(iban) <= 34:
                if validate_iban(iban):
                    return iban
                fixed = _fix_iban_ocr(iban)
                if fixed and validate_iban(fixed):
                    return fixed

    # 2) fallback global
    candidates = []
    for mm in IBAN_CANDIDATE_REGEX.finditer(src):
        iban = _norm_iban(mm.group(0))
        if 15 <= len(iban) <= 34:
            if validate_iban(iban):
                candidates.append(iban)
                continue

            fixed = _fix_iban_ocr(iban)
            if fixed and validate_iban(fixed):
                candidates.append(fixed)

    if not candidates:
        return ""

    counts = Counter(candidates)
    return sorted(counts.items(), key=lambda kv: (kv[1], len(kv[0])), reverse=True)[0][0]


def extract_bic(text: str) -> str:
    t = _normalize_ocr(text)

    bic = _find_best_match_near_label(
        t,
        label_patterns=[
            r"\bBIC\b",
            r"\bSWIFT\b",
            r"\bB\.?I\.?C\.?\b",
        ],
        value_regex=BIC_REGEX,
        window=120,
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
    match = re.search(r"\b\d{2}[./-]\d{2}[./-]\d{4}\b", text or "")
    return match.group() if match else ""


def extract_invoice_number(text: str) -> str:
    if not text:
        return ""

    bad = {
        "DESCRIPTION", "DATE", "FACTURE", "INVOICE", "TOTAL", "MONTANT", "BASE",
        "CLIENT", "REFERENCE", "RÉFÉRENCE", "QTE", "QTÉ", "PU", "HT", "TVA", "TTC",
    }

    label_rx = re.compile(
        r"\b(N[°O]\s*FACTURE|FACTURE\s*(N[°O]|NO\.?)|INVOICE\s*(NO\.?|NUMBER)|INV\.?\s*NO\.?)\b",
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
        r"(?:N[°O]\s*)?(?:INVOICE|INV\.?|FACTURE)\b[^\w]{0,20}([A-Z0-9\-_/\.]{3,})",
        text,
        re.IGNORECASE,
    )
    if m:
        cand = m.group(1).strip()
        if ok(cand):
            return cand

    return ""


# =========================
# TVA
# =========================

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

    # 2) format table TVA
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    label_table = _extract_vat_by_labels(lines)

    centers = [i for i, ln in enumerate(lines) if VAT_BLOCK_HINT_RE.search(ln)]
    if not centers:
        centers = [i for i, ln in enumerate(lines) if "TVA" in ln.upper()]

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
        rate = chosen["rate"]
        base = chosen["base"]
        vat = chosen["vat"]

        key = (rate.replace(",", "."), base, vat)
        if key not in seen:
            seen.add(key)
            lines_out.append({"rate": rate, "base": base, "vat": vat})

    if best_table:
        rate = best_table["rate"]
        base = best_table["base"]
        vat = best_table["vat"]

        key = (rate.replace(",", "."), base, vat)
        if key not in seen:
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
    for row in vat_lines:
        v = _to_float(row.get("vat", ""))
        if v is not None:
            vat_total += v
            has_any = True
    vat_total = vat_total if has_any else None

    folder_numbers = extract_folder_numbers(text)
    folder_number = folder_numbers[0] if folder_numbers else ""

    return InvoiceData(
        iban=extract_iban(text),
        bic=extract_bic(text),
        invoice_date=extract_date(text),
        invoice_number=extract_invoice_number(text),
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

    rate_cands = [a for a in amounts if 0.0 < a[0] <= 30.0]
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
    return {"rate": rstr, "base": bstr, "vat": vstr}


def _money_in_line_or_next(lines: list[str], idx: int) -> str:
    if idx is None or idx < 0 or idx >= len(lines):
        return ""

    ln = lines[idx].strip()

    m = MONEY_RE.search(ln)
    if m:
        return _norm_amount_str(m.group(0))

    if idx + 1 < len(lines):
        ln2 = (lines[idx + 1] or "").strip()
        if ONLY_AMOUNT_RE.match(ln2):
            m2 = MONEY_RE.search(ln2)
            if m2:
                return _norm_amount_str(m2.group(0))

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
            if v is not None and 0.0 < v <= 30.0:
                cands.append((v, _norm_amount_str(s)))
        if not cands:
            return ""
        cands.sort(key=lambda x: x[0], reverse=True)
        return cands[0][1].replace(",", ".")

    r = pick_rate_from_text(ln)
    if r:
        return r

    if idx + 1 < len(lines):
        ln2 = (lines[idx + 1] or "").strip()
        if ONLY_AMOUNT_RE.match(ln2):
            r2 = pick_rate_from_text(ln2)
            if r2:
                return r2

    return ""


def _extract_vat_by_labels(lines: list[str]) -> dict | None:
    idx_base = next((i for i, ln in enumerate(lines) if BASE_LABEL_RE.search(ln)), None)
    idx_vat = next((i for i, ln in enumerate(lines) if VAT_LABEL_RE.search(ln)), None)
    idx_rate = next(
        (
            i for i, ln in enumerate(lines)
            if RATE_LABEL_RE.search(ln) and "TOTAL TVA" not in ln.upper()
        ),
        None,
    )

    base = _money_in_line_or_next(lines, idx_base) if idx_base is not None else ""
    vat = _money_in_line_or_next(lines, idx_vat) if idx_vat is not None else ""
    rate = _rate_in_line_or_next(lines, idx_rate) if idx_rate is not None else ""

    bv = _to_float(base) if base else None
    vv = _to_float(vat) if vat else None
    rv = _to_float(rate) if rate else None

    if (bv is not None and bv > 0) and (vv is not None and vv > 0):
        if rv is None or rv <= 0 or rv > 30:
            rv = (vv / bv) * 100.0
            rate = f"{rv:.2f}"

    if bv is not None and vv is not None and rv is not None:
        expected = (bv * rv) / 100.0
        if abs(vv - expected) > max(0.06, expected * 0.03):
            return None

    if rate and base and vat:
        return {"rate": rate.replace(",", "."), "base": base, "vat": vat}

    return None