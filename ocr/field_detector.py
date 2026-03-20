import re
import unicodedata
from typing import Any, Dict, List, Optional

from .folder_patterns import extract_folder_numbers_from_text, is_valid_folder_number


# ------------------------------------------------------------
# Dictionnaires multilingues
# ------------------------------------------------------------
FIELD_LABELS: Dict[str, List[str]] = {
    "iban": [
        "IBAN",
        "INTERNATIONAL BANK ACCOUNT NUMBER",
        "NUMERO DE COMPTE BANCAIRE INTERNATIONAL",
        "NUMERO INTERNACIONAL DE CUENTA BANCARIA",
        "NUMERO DI CONTO BANCARIO INTERNAZIONALE",
        "NUMERO INTERNACIONAL DE CONTA BANCARIA",
        "INTERNATIONALE BANKKONTONUMMER",
        "INTERNATIONAAL BANKREKENINGNUMMER",
        "MIEDZYNARODOWY NUMER RACHUNKU BANKOWEGO",
        "MEZINARODNI CISLO BANKOVNIHO UCTU",
        "MEDZINARODNE CISLO BANKOVEHO UCTU",
        "NEMZETKOZI BANKSZAMLASZAM",
        "RĒĶINA IBAN",
        "SASKAITOS IBAN",
    ],
    "bic": [
        "BIC",
        "SWIFT",
        "SWIFT CODE",
        "SWIFT/BIC",
        "BIC/SWIFT",
        "BANK IDENTIFIER CODE",
        "CODE SWIFT",
        "CODIGO SWIFT",
        "CODIGO BIC",
        "CODICE SWIFT",
        "CODIGO SWIFT BIC",
        "KOD SWIFT",
        "KOD BIC",
    ],
    "date": [
        "DATE",
        "INVOICE DATE",
        "DATE FACTURE",
        "DATE DE FACTURE",
        "FACTUURDATUM",
        "RECHNUNGSDATUM",
        "FECHA FACTURA",
        "FECHA DE FACTURA",
        "DATA FATTURA",
        "DATA DA FATURA",
        "DATUM FAKTURY",
        "DATUM VYSTAVENI",
        "DATUM VYSTAVENIA",
        "DATUM RACUNA",
        "DATUM RAČUNA",
        "ARVE KUUPAEV",
        "LASKUN PVM",
        "LASKUN PAIVA",
        "FAKTURADATUM",
        "FAKTURADATO",
        "SZAMLA KELTE",
        "REKINA DATUMS",
        "RĒĶINA DATUMS",
        "SASKAITOS DATA",
        "SĄSKAITOS DATA",
        "ΗΜΕΡΟΜΗΝΙΑ ΤΙΜΟΛΟΓΙΟΥ",
        "ДАТА ФАКТУРА",
        "ДАТА НА ФАКТУРА",
    ],
    "invoice_number": [
        "INVOICE NUMBER",
        "INVOICE NO",
        "INVOICE NR",
        "INVOICE #",
        "INV NO",
        "INV NR",
        "INV #",
        "FACTURE",
        "NUMERO FACTURE",
        "N FACTURE",
        "NO FACTURE",
        "NR FACTURE",
        "FACTUURNUMMER",
        "RECHNUNGSNUMMER",
        "BELEGNUMMER",
        "FACTURA NUMERO",
        "NUMERO FACTURA",
        "N FACTURA",
        "NO FACTURA",
        "NR FACTURA",
        "NUMERO FATTURA",
        "FATTURA NUMERO",
        "NUMERO DA FATURA",
        "FATURA NUMERO",
        "NUMER FAKTURY",
        "CISLO FAKTURY",
        "ČISLO FAKTURY",
        "CISLO DOKLADU",
        "ČÍSLO DOKLADU",
        "ARVE NR",
        "LASKUN NUMERO",
        "FAKTURANUMMER",
        "FAKTURANR",
        "SZAMLASZAM",
        "SZÁMLASZÁM",
        "NUMAR FACTURA",
        "NUMĂR FACTURĂ",
        "REKINA NUMURS",
        "RĒĶINA NUMURS",
        "SASKAITOS NUMERIS",
        "SĄSKAITOS NUMERIS",
        "ΑΡΙΘΜΟΣ ΤΙΜΟΛΟΓΙΟΥ",
        "НОМЕР НА ФАКТУРА",
    ],
    "folder_number": [
        "DOSSIER",
        "DOSSIER NUMBER",
        "DOSSIER NO",
        "DOSSIER NR",
        "DOSSIERNUMMER",
        "NUMERO DOSSIER",
        "NUMERO DE DOSSIER",
        "N DOSSIER",
        "NO DOSSIER",
        "NR DOSSIER",
        "FOLDER NUMBER",
        "FILE NUMBER",
        "CASE NUMBER",
        "REFERENCE",
        "REFERENCE CLIENT",
        "CUSTOMER REFERENCE",
        "SHIPMENT NUMBER",
        "SHIPMENT NO",
        "ORDER NUMBER",
        "ORDER NO",
        "BOOKING NUMBER",
        "BOOKING NO",
        "JOB NUMBER",
        "TOUR",
        "TOUR NR",
        "TOUR NO",
        "TOURNR",
        "TOURNUMMER",
        "TOUR N",
        "NUMERO DE EXPEDIENTE",
        "EXPEDIENTE",
        "NUMERO PRATICA",
        "NUMERO SPEDIZIONE",
        "SPEDIZIONE",
        "NUMER ZLECENIA",
        "ZLECENIE",
        "REFERENCIA CLIENTE",
        "REFERENCIA",
        "REFERENCIA DEL CLIENTE",
        "REFERENCIA TRANSPORTE",
        "ΑΡΙΘΜΟΣ ΦΑΚΕΛΟΥ",
        "НОМЕР НА ДОСИЕ",
    ],
}

IBAN_EXACT_RE = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}")
IBAN_CANDIDATE_RE = re.compile(
    r"\b[A-Z]{2}[ \u00A0-]*\d{2}(?:[ \u00A0-]*[A-Z0-9]){11,30}\b",
    re.IGNORECASE,
)
BIC_EXACT_RE = re.compile(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?")
DATE_RE = re.compile(
    r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})\b"
)
INVOICE_TOKEN_RE = re.compile(r"\b[A-Z0-9][A-Z0-9\-_/\.]{2,40}\b", re.IGNORECASE)

BIC_BLACKLIST = {
    "LOGISTIK", "TRANSPORT", "MODE", "REGLEMENT", "PAYMENT", "INVOICE",
    "FACTURE", "BANK", "IBAN", "BIC", "SWIFT", "TOTAL", "AMOUNT",
}

NON_VALUE_TOKENS = {
    "DATE", "FACTURE", "INVOICE", "TOTAL", "MONTANT", "BASE", "CLIENT",
    "REFERENCE", "RÉFÉRENCE", "REFERENCIA", "REFERENZ", "REFERINTA",
    "NUMBER", "NUMERO", "NUMÉRO", "NUMER", "NUMMER", "NR", "NO", "N",
    "BIC", "IBAN", "SWIFT", "VAT", "TVA", "IVA", "MWST", "BTW",
    "FACTURA", "FATTURA", "FATURA", "RECHNUNG", "ARVE", "LASKU",
}


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(ch)
    )


def _normalize_for_alias_match(text: str) -> str:
    s = _strip_accents((text or "").upper())
    s = s.replace("№", " N ")
    s = s.replace("N°", " N ")
    s = s.replace("Nº", " N ")
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return f" {s} " if s else " "


def _compact(text: str) -> str:
    return re.sub(r"[\s\u00A0-]+", "", (text or "").upper()).strip()


def _normalize_iban(value: str) -> str:
    return _compact(value)


def _normalize_bic(value: str) -> str:
    return _compact(value)


def _validate_iban(iban: str) -> bool:
    s = _normalize_iban(iban)
    if not IBAN_EXACT_RE.fullmatch(s):
        return False

    rearranged = s[4:] + s[:4]
    digits = ""
    for ch in rearranged:
        digits += ch if ch.isdigit() else str(ord(ch) - 55)

    mod = 0
    for i in range(0, len(digits), 7):
        mod = int(str(mod) + digits[i:i + 7]) % 97
    return mod == 1


def _validate_bic(bic: str) -> bool:
    s = _normalize_bic(bic)
    if s in BIC_BLACKLIST:
        return False
    return bool(BIC_EXACT_RE.fullmatch(s))


def _contains_alias(text: str, aliases: List[str]) -> bool:
    norm = _normalize_for_alias_match(text)
    for alias in aliases:
        if _normalize_for_alias_match(alias) in norm:
            return True
    return False


def _line_has_alias(line: str, field: str) -> bool:
    return _contains_alias(line, FIELD_LABELS.get(field, []))


def _extract_iban_candidates(text: str) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()

    for m in IBAN_CANDIDATE_RE.finditer((text or "").upper()):
        iban = _normalize_iban(m.group(0))
        if iban not in seen and _validate_iban(iban):
            seen.add(iban)
            found.append(iban)

    return found


def _extract_bic_candidates(text: str) -> List[str]:
    src = (text or "").upper().replace("\u00A0", " ")
    found: List[str] = []
    seen: set[str] = set()

    for m in BIC_EXACT_RE.finditer(src):
        bic = _normalize_bic(m.group(0))
        if bic not in seen and _validate_bic(bic):
            seen.add(bic)
            found.append(bic)

    return found


def _extract_first_date(text: str) -> str:
    m = DATE_RE.search(text or "")
    return m.group(0) if m else ""


def _is_probable_invoice_value(token: str) -> bool:
    t = (token or "").strip().upper()
    if not t:
        return False
    if t in NON_VALUE_TOKENS:
        return False
    if len(t) < 4 or len(t) > 40:
        return False
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", t):
        return False
    if re.fullmatch(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", t):
        return False
    if not any(ch.isdigit() for ch in t):
        return False
    return True


def _extract_value_near_label(lines: List[str], field: str) -> str:
    for i, line in enumerate(lines):
        if not _line_has_alias(line, field):
            continue

        same_line = line.split(":", 1)[-1] if ":" in line else line

        if field == "date":
            m = DATE_RE.search(same_line)
            if m:
                return m.group(0)
        elif field == "folder_number":
            folders = extract_folder_numbers_from_text(line)
            if folders:
                return folders[0]
        else:
            for token in INVOICE_TOKEN_RE.findall(same_line.upper()):
                if _is_probable_invoice_value(token):
                    return token

        for j in range(i + 1, min(i + 4, len(lines))):
            next_line = lines[j].strip()
            if not next_line:
                continue

            if field == "date":
                m = DATE_RE.search(next_line)
                if m:
                    return m.group(0)
            elif field == "folder_number":
                folders = extract_folder_numbers_from_text(next_line)
                if folders:
                    return folders[0]
            else:
                for token in INVOICE_TOKEN_RE.findall(next_line.upper()):
                    if _is_probable_invoice_value(token):
                        return token

    return ""


def detect_fields_multilingual(text: str) -> Dict[str, Any]:
    src = text or ""
    lines = [ln.strip() for ln in src.splitlines() if ln.strip()]
    out: Dict[str, Any] = {}

    ibans = _extract_iban_candidates(src)
    if ibans:
        out["iban"] = ibans[0]

    bic = ""
    for i, line in enumerate(lines):
        if _line_has_alias(line, "bic"):
            window = " ".join(lines[i:i + 3])
            bics = _extract_bic_candidates(window)
            if bics:
                bic = bics[0]
                break
    if not bic:
        bics = _extract_bic_candidates(src)
        if bics:
            bic = bics[0]
    if bic:
        out["bic"] = bic

    date_value = _extract_value_near_label(lines, "date")
    if not date_value:
        date_value = _extract_first_date(src)
    if date_value:
        out["date"] = date_value

    invoice_number = _extract_value_near_label(lines, "invoice_number")
    if invoice_number:
        out["invoice_number"] = invoice_number

    folder_number = _extract_value_near_label(lines, "folder_number")
    if not folder_number:
        folders = extract_folder_numbers_from_text(src)
        if folders:
            folder_number = folders[0]
    if folder_number:
        out["folder_number"] = folder_number

    return out


def guess_field(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    compact = _compact(raw)
    scores: Dict[str, int] = {}

    def add(field: str, value: int) -> None:
        scores[field] = scores.get(field, 0) + value

    if _validate_iban(compact):
        add("iban", 250)

    if _validate_bic(compact):
        add("bic", 220)

    if DATE_RE.fullmatch(raw):
        add("date", 180)
    elif DATE_RE.search(raw):
        add("date", 70)

    if is_valid_folder_number(compact):
        add("folder_number", 220)

    if _is_probable_invoice_value(compact):
        add("invoice_number", 35)
        if compact.isdigit():
            add("folder_number", 20)

    detected = detect_fields_multilingual(raw)
    for key in detected:
        if key == "iban":
            add("iban", 160)
        elif key == "bic":
            add("bic", 150)
        elif key == "date":
            add("date", 120)
        elif key == "invoice_number":
            add("invoice_number", 140)
        elif key == "folder_number":
            add("folder_number", 150)

    for field in ("iban", "bic", "date", "invoice_number", "folder_number"):
        if _line_has_alias(raw, field):
            if field == "date":
                add(field, 80)
            elif field in {"invoice_number", "folder_number"}:
                add(field, 110)
            else:
                add(field, 100)

    if "invoice_number" in scores and "folder_number" in scores:
        if _line_has_alias(raw, "invoice_number"):
            add("invoice_number", 40)
        if _line_has_alias(raw, "folder_number"):
            add("folder_number", 40)

    return max(scores, key=scores.get) if scores else None


class FieldDetector:
    """
    Compatibilité ascendante :
    - si `field_patterns` est fourni, on applique d'abord les regex custom
    - ensuite on complète avec le fallback multilingue
    """

    def __init__(self, field_patterns: Optional[Dict[str, str]] = None):
        self.field_patterns = {
            name: re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            for name, pattern in (field_patterns or {}).items()
        }

    def detect(self, text: str, anchors: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        src = text or ""

        for field_name, pattern in self.field_patterns.items():
            matches = pattern.findall(src)
            if not matches:
                continue

            values = self._normalize(matches)
            result[field_name] = values[0] if len(values) == 1 else values

        detected = detect_fields_multilingual(src)
        for key, value in detected.items():
            result.setdefault(key, value)

        if anchors:
            self._merge_with_anchors(result, anchors)

        return result

    def _normalize(self, matches: List[Any]) -> List[Any]:
        cleaned: List[Any] = []

        for m in matches:
            if isinstance(m, tuple):
                m = " ".join(str(x) for x in m if str(x).strip())
            m = str(m).strip()

            if re.fullmatch(r"\d+(?:[.,]\d+)?", m):
                cleaned.append(float(m.replace(",", ".")))
            else:
                cleaned.append(m)

        return cleaned

    def _merge_with_anchors(self, result: Dict[str, Any], anchors: Dict[str, List[str]]) -> None:
        for key, values in anchors.items():
            if key not in result and values:
                result[key] = values[0] if len(values) == 1 else values