import os
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter

# --- Où sont stockés les modèles fournisseurs ---
try:
    # nouveau nom (refacto)
    from app.paths import SUPPLIER_MODELS_DIR
    MODEL_DIR = SUPPLIER_MODELS_DIR
except Exception:
    # fallback anciens noms si tu as encore du code legacy
    try:
        from app.paths import SUPPLIERS_DIR  # type: ignore
        MODEL_DIR = SUPPLIERS_DIR
    except Exception:
        # fallback ultime: dossier local du projet
        import os
        MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "suppliers")




def build_supplier_key(iban: str, bic: str) -> Optional[str]:
    iban = (iban or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()
    bic  = (bic  or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()

    if not iban or not bic:
        return None
    if not validate_iban(iban):
        return None
    if not validate_bic(bic):
        return None

    return f"{iban}_{bic}"


def _model_path(supplier_key: str) -> str:
    os.makedirs(MODEL_DIR, exist_ok=True)
    return os.path.join(MODEL_DIR, f"{supplier_key}.json")


def load_supplier_model(supplier_key: str) -> Optional[dict]:
    path = _model_path(supplier_key)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_supplier_model(supplier_key: str, data: dict) -> None:
    """Écriture atomique (évite les JSON tronqués en cas d'arrêt brutal)."""
    path = _model_path(supplier_key)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Learning / extraction "fiable" (textuel)
# - On apprend des REGEX "contextuelles" (sur une ligne) à partir de l'OCR.
# - On stocke plusieurs règles + un hit_count.
# - À l'application : on essaie d'abord ces règles, puis on fallback.
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_for_search(s: str) -> str:
    # suppressions pour panorama OCR de dates/numéros (espace, NBSP, tiret, slash, point)
    return re.sub(r"[\s\u00A0\-\./]", "", (s or "").upper())


def _find_line_with_value(text: str, value: str) -> Optional[str]:
    if not text or not value:
        return None
    v = _norm_for_search(value)
    if not v:
        return None

    best = None
    best_len = 10**9
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if v in _norm_for_search(line):
            if len(line) < best_len:
                best = line
                best_len = len(line)
    return best


def _extract_label_near_value(text: str, value: str, window: int = 50) -> Optional[str]:
    """Extrait le label réel près d'une valeur (cherche dans les lignes autour)."""
    if not text or not value:
        return None

    v_norm = _norm_for_search(value)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for i, line in enumerate(lines):
        line_norm = _norm_for_search(line)
        if v_norm in line_norm:
            # 1) Chercher un label sur la même ligne (valeur après label)
            
            value_idx = line_norm.find(v_norm)
            if value_idx > 0:
                left_part = line[:value_idx].strip()
                if ':' in left_part:
                    candidate = left_part.split(':')[0].strip()
                else:
                    candidate = left_part.strip()

                # normalisation minimale (pas juste chiffres / trop court)
                if candidate and len(candidate) > 2 and len(candidate) < 60 and re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", candidate):
                    return candidate

            # 2) Chercher un label dans les lignes précédentes (1-2 lignes)
            for j in range(max(0, i - 2), i):
                prev_line = lines[j]
                if ':' in prev_line:
                    label = prev_line.split(':')[0].strip()
                    if label and len(label) > 2 and len(label) < 60:
                        return label

            # 3) Chercher un label dans la ligne suivante (cas label en dessous)
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                if ':' in next_line:
                    label = next_line.split(':')[0].strip()
                    if label and len(label) > 2 and len(label) < 60:
                        return label

    return None


def _value_group_regex(field: str, value: str) -> str:
    v = (value or "").strip()

    if field == "invoice_date":
        return r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"

    vv = _norm_for_search(v)

    # ✅ spécial invoice_number : si c'est du type LETTRES + CHIFFRES (ex: F2511326)
    if field == "invoice_number":
        m = re.fullmatch(r"([A-Z]{1,6})(\d{3,})", vv)
        if m:
            prefix, digits = m.groups()
            n = len(digits)

            # cas exact demandé : F + 7 chiffres
            if prefix == "F" and n == 7:
                return r"F\d{7}"

            # sinon: même prefix, digits proche
            lo = max(3, n - 1)
            hi = min(20, n + 1)
            return rf"{re.escape(prefix)}\d{{{lo},{hi}}}"

        # fallback : au moins un alphanumérique + au moins un chiffre (permet de commencer par chiffre)
        return r"[A-Z0-9][A-Z0-9\-_/\.]*\d[A-Z0-9\-_/\.]*"

    if vv.isdigit():
        n = len(vv)
        lo = max(3, n - 2)
        hi = min(40, n + 2)
        return rf"\d{{{lo},{hi}}}"

    allowed = "A-Z0-9"
    if any(c in "-_/." for c in vv):
        allowed += r"\-_/\. "

    n = len(vv)
    lo = max(3, n - 2)
    hi = min(60, n + 2)
    return rf"[{allowed}]{{{lo},{hi}}}"


def _make_line_regex(line: str, field: str, value: str) -> Optional[str]:
    if not line or not value:
        return None

    group_re = _value_group_regex(field, value)

    esc = re.escape(line)
    esc_val = re.escape(value)

    if esc_val not in esc:
        # OCR : la valeur peut être espacée, on préfère un mode "near_label"
        return None

    esc = esc.replace(esc_val, f"({group_re})", 1)

    # espaces flexibles
    esc = esc.replace(r"\ ", r"\s+")

    # séparateurs tolérants
    esc = esc.replace(r"\:", r"\s*[:]\s*")
    esc = esc.replace(r"\=", r"\s*[=]\s*")

    # regex sur une ligne
    return rf"^{esc}$"


def _tolerant_exact_regex(value: str) -> Optional[str]:
    """Match une valeur exacte en autorisant espaces/NBSP/tirets entre blocs."""
    v = (value or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()
    if not v:
        return None

    parts = [v[i:i + 4] for i in range(0, len(v), 4)]
    sep = r"[\s\u00A0-]*"
    return r"\b" + sep.join(map(re.escape, parts)) + r"\b"


IBAN_LENGTHS: Dict[str, int] = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16,
    "BG": 22, "BH": 22, "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DO": 28, "EE": 20, "ES": 24, "FI": 18,
    "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23, "GL": 18, "GR": 27,
    "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27, "JO": 30,
    "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20,
    "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31,
    "MU": 30, "NL": 18, "NO": 15, "PK": 24, "PL": 28, "PS": 29, "PT": 25,
    "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31, "SE": 24, "SI": 19,
    "SK": 24, "SM": 27, "ST": 25, "SV": 28, "TL": 23, "TN": 24, "TR": 26,
    "UA": 29, "VA": 22, "VG": 24, "XK": 20,
}

IBAN_OCR_DIGIT_FIX = {
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1",
    "S": "5", "B": "8", "Z": "2", "G": "6", "T": "7",
}

_IBAN_START_RX = re.compile(
    r"(?<![A-Z0-9])([A-Z]{2})[\s\u00A0-]*(\d{2})(?=(?:[\s\u00A0-]*[A-Z0-9]){11,40})",
    re.IGNORECASE,
)


def _normalize_iban_candidate(value: str) -> str:
    return re.sub(r"[\s\u00A0-]+", "", (value or "").upper()).strip()


def _expected_iban_length(country_code: str) -> Optional[int]:
    return IBAN_LENGTHS.get((country_code or "").upper())


def _iban_variants(compact: str) -> List[str]:
    s = _normalize_iban_candidate(compact)
    if len(s) < 5:
        return []

    out = [s]
    fixed_rest = "".join(IBAN_OCR_DIGIT_FIX.get(ch, ch) for ch in s[4:])
    fixed = s[:4] + fixed_rest
    if fixed != s:
        out.append(fixed)
    return out


def _extract_iban_candidates_from_text(text: str, *, prefer_labels: bool = False) -> Counter:
    src = (text or "").upper().replace("\u00A0", " ")
    label_counts: Counter = Counter()
    seen_windows: set[tuple[int, int]] = set()

    def _ingest_fragment(fragment: str, target: Counter) -> None:
        for m in _IBAN_START_RX.finditer(fragment):
            start = m.start()
            country = m.group(1).upper()
            tail = fragment[start:start + 96]
            alnum = "".join(ch for ch in tail if ch.isalnum())
            if len(alnum) < 15:
                continue

            lengths: List[int] = []
            expected = _expected_iban_length(country)
            if expected:
                lengths.append(expected)
            for fallback_len in range(min(34, len(alnum)), 14, -1):
                if fallback_len not in lengths:
                    lengths.append(fallback_len)

            for candidate_len in lengths:
                if len(alnum) < candidate_len:
                    continue
                base = alnum[:candidate_len]
                for candidate in _iban_variants(base):
                    if validate_iban(candidate):
                        target[candidate] += 1
                        return

    if prefer_labels:
        for m in IBAN_LABEL_RE.finditer(src):
            window = src[m.end(): m.end() + 120]
            key = (m.end(), m.end() + 120)
            if key not in seen_windows:
                seen_windows.add(key)
                _ingest_fragment(window, label_counts)
        if label_counts:
            return label_counts

    all_counts: Counter = Counter()
    _ingest_fragment(src, all_counts)
    return all_counts


def extract_iban_candidates(text: str, *, prefer_labels: bool = False) -> Counter:
    return _extract_iban_candidates_from_text(text, prefer_labels=prefer_labels)


def validate_iban(iban: str) -> bool:
    s = _normalize_iban_candidate(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False

    expected = _expected_iban_length(s[:2])
    if expected and len(s) != expected:
        return False

    rearr = s[4:] + s[:4]
    digits = ""
    for ch in rearr:
        digits += ch if ch.isdigit() else str(ord(ch) - 55)  # A=10

    mod = 0
    for i in range(0, len(digits), 7):
        mod = int(str(mod) + digits[i:i + 7]) % 97
    return mod == 1


BIC_BLACKLIST = {
    "LOGISTIK", "TRANSPORT", "MODE", "REGLEMENT", "PAYMENT", "INVOICE", "FACTURE",
    "BANK", "IBAN", "BIC", "SWIFT", "TOTAL", "AMOUNT", "DETAILDE", "DETAILDU",
    "VIREMENT", "ECHEANCE", "COMPTE", "MONTANT", "GENERAL", "FACTUREN", "TOTALHT",
}

BIC_LABEL_RE = re.compile(
    r"(?:\b(?:CODE|COD[EF]|CONF)\s+)?(?:SWIFT|BIC)\b",
    re.IGNORECASE,
)

IBAN_LABEL_RE = re.compile(r"(?:\bI?B?AN\b|\b\\BAN\b)", re.IGNORECASE)


def validate_bic(bic: str) -> bool:
    s = (bic or "").replace(" ", "").replace(" ", "").replace("-", "").upper().strip()
    if not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?", s):
        return False
    if s in BIC_BLACKLIST:
        return False
    # code pays à la position 5-6 : au minimum deux lettres, et on rejette les mots OCR trop fréquents
    if not s[4:6].isalpha():
        return False
    if s.startswith(("IBAN", "SWIF", "BICB", "CODE")):
        return False
    return True


def learn_supplier_patterns(
    ocr_text: str,
    *,
    iban: str,
    bic: str,
    invoice_number: str,
    invoice_date: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Construit des règles à partir du texte OCR + valeurs validées."""
    patterns: Dict[str, List[Dict[str, Any]]] = {
        "iban": [],
        "bic": [],
        "invoice_number": [],
        "invoice_date": [],
    }

    # IBAN/BIC : match exact tolérant (fiable)
    iban_re = _tolerant_exact_regex(iban)
    if iban_re:
        patterns["iban"].append({
            "mode": "tolerant_exact",
            "regex": iban_re,
            "group": 0,
            "hit_count": 0,
            "created_at": _now_iso(),
        })

    bic_re = _tolerant_exact_regex(bic)
    if bic_re:
        patterns["bic"].append({
            "mode": "tolerant_exact",
            "regex": bic_re,
            "group": 0,
            "hit_count": 0,
            "created_at": _now_iso(),
        })

    # Invoice number : ligne + fallback label
    if invoice_number:
        line = _find_line_with_value(ocr_text, invoice_number)
        rx = _make_line_regex(line, "invoice_number", invoice_number) if line else None
        if rx:
            patterns["invoice_number"].append({
                "mode": "line_regex",
                "regex": rx,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

        # Apprendre le label spécifique trouvé près de la valeur
        specific_label = _extract_label_near_value(ocr_text, invoice_number)
        if specific_label:
            group_re = _value_group_regex("invoice_number", invoice_number)
            patterns["invoice_number"].append({
                "mode": "near_label",
                "label_regex": rf"\b{re.escape(specific_label)}\b",
                "value_regex": rf"(\b{group_re}\b)",
                "window": 200,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

        # Fallback générique
        patterns["invoice_number"].append({
            "mode": "near_label",
            "label_regex": r"\b(N[°O]\s*FACTURE|INVOICE\s*(NO\.?|NUMBER)|INV\.?\s*NO\.?)\b",
            "value_regex": r"([A-Z0-9][A-Z0-9\-_/\.]*\d[A-Z0-9\-_/\.]{1,})",
            "window": 120,
            "group": 1,
            "hit_count": 0,
            "created_at": _now_iso(),
        })

    # Date : ligne + fallback label
    if invoice_date:
        line = _find_line_with_value(ocr_text, invoice_date)
        rx = _make_line_regex(line, "invoice_date", invoice_date) if line else None
        if rx:
            patterns["invoice_date"].append({
                "mode": "line_regex",
                "regex": rx,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

        # Apprendre le label spécifique trouvé près de la valeur
        specific_label = _extract_label_near_value(ocr_text, invoice_date)
        if specific_label:
            patterns["invoice_date"].append({
                "mode": "near_label",
                "label_regex": rf"\b{re.escape(specific_label)}\b",
                "value_regex": r"(\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b)",
                "window": 120,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

        # Fallback générique
        patterns["invoice_date"].append({
            "mode": "near_label",
            "label_regex": r"\b(DATE|INVOICE\s+DATE|DATE\s+FACTURE|DATUM)\b",
            "value_regex": r"(\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b)",
            "window": 120,
            "group": 1,
            "hit_count": 0,
            "created_at": _now_iso(),
        })

    return {k: v for k, v in patterns.items() if v}


def merge_patterns(
    existing: Optional[Dict[str, List[Dict[str, Any]]]],
    new: Dict[str, List[Dict[str, Any]]],
    *,
    max_rules_per_field: int = 6,
) -> Dict[str, List[Dict[str, Any]]]:
    existing = existing or {}
    merged: Dict[str, List[Dict[str, Any]]] = {}

    for field in set(existing.keys()) | set(new.keys()):
        cur = list(existing.get(field, []) or [])
        add = list(new.get(field, []) or [])

        idx: Dict[str, Dict[str, Any]] = {}
        out: List[Dict[str, Any]] = []

        def key(rule: Dict[str, Any]) -> str:
            if rule.get("mode") == "near_label":
                return f"near|{rule.get('label_regex')}|{rule.get('value_regex')}|{rule.get('window')}|{rule.get('group')}"
            return f"{rule.get('mode')}|{rule.get('regex')}|{rule.get('group')}"

        for r in cur:
            k = key(r)
            idx[k] = r
            out.append(r)

        for r in add:
            k = key(r)
            if k in idx:
                idx[k]["hit_count"] = int(idx[k].get("hit_count", 0)) + 1
                idx[k]["last_seen"] = _now_iso()
            else:
                rr = dict(r)
                rr["hit_count"] = int(rr.get("hit_count", 0)) + 1
                rr["last_seen"] = _now_iso()
                out.append(rr)
                idx[k] = rr

        out.sort(key=lambda x: int(x.get("hit_count", 0)), reverse=True)
        merged[field] = out[:max_rules_per_field]

    return merged


def extract_fields_with_model(text: str, model: dict) -> Dict[str, str]:
    """Applique les patterns du modèle (si présents) et retourne les champs trouvés."""
    patterns = (model or {}).get("patterns") or {}
    out: Dict[str, str] = {}

    if not text or not patterns:
        return out

    src = (text or "").replace("\u00A0", " ")

    def apply_rule(field: str, rule: Dict[str, Any]) -> Optional[str]:
        mode = rule.get("mode")

        if mode == "line_regex":
            rx = re.compile(rule.get("regex", ""), re.IGNORECASE | re.MULTILINE)
            m = rx.search(src)
            if not m:
                return None
            g = int(rule.get("group", 0))
            return (m.group(g) if g else m.group(0) or "").strip()

        if mode == "near_label":
            label_rx = re.compile(rule.get("label_regex", ""), re.IGNORECASE)
            value_rx = re.compile(rule.get("value_regex", ""), re.IGNORECASE)
            window = int(rule.get("window", 120))
            group = int(rule.get("group", 0))

            for lm in label_rx.finditer(src):
                chunk = src[lm.end(): lm.end() + window]
                vm = value_rx.search(chunk)
                if vm:
                    return (vm.group(group) if group else vm.group(0) or "").strip()
            return None

        if mode == "tolerant_exact":
            rx = re.compile(rule.get("regex", ""), re.IGNORECASE)
            m = rx.search(src)
            return m.group(0) if m else None

        return None

    for field in ("iban", "bic", "invoice_date", "invoice_number"):
        rules = list(patterns.get(field, []) or [])
        rules.sort(key=lambda x: int(x.get("hit_count", 0)), reverse=True)

        for r in rules:
            val = apply_rule(field, r)
            if not val:
                continue

            if field in ("iban", "bic"):
                val = val.replace(" ", "").replace("\u00A0", "").upper().strip()
            else:
                val = val.strip()

            if field == "iban" and not validate_iban(val):
                continue
            if field == "bic" and not validate_bic(val):
                continue
            if field == "invoice_number":
                # rejette les dates du style 30/11/2025
                if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", val.strip()):
                    continue

            out[field] = val
            break

    return out




_IBAN_CAND_RX = re.compile(r"\b[A-Z]{2}\s*\d{2}(?:[\s\u00A0-]*[A-Z0-9]){11,30}\b", re.IGNORECASE)
_BIC_CAND_RX  = re.compile(r"\b[A-Z]{4}\s*[A-Z]{2}\s*[A-Z0-9]{2}(?:\s*[A-Z0-9]{3})?\b", re.IGNORECASE)

def _norm_iban(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()

def _norm_bic(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()

def _iter_context_chunks_for_bic(text: str) -> List[str]:
    txt = text or ""
    chunks: List[str] = []

    for m in BIC_LABEL_RE.finditer(txt):
        start = max(0, m.start() - 12)
        end = min(len(txt), m.end() + 40)
        chunks.append(txt[start:end])

    return chunks


def _contextual_bic_candidates(text: str) -> Counter:
    counts: Counter = Counter()
    for chunk in _iter_context_chunks_for_bic(text):
        for m in _BIC_CAND_RX.finditer(chunk):
            bic = _norm_bic(m.group(0))
            if validate_bic(bic):
                counts[bic] += 1
    return counts


def extract_best_bank_ids(
    ocr_text: str,
    *,
    prefer_iban: str = "",
    prefer_bic: str = "",
) -> Dict[str, Any]:
    """
    Retourne le meilleur IBAN/BIC trouvés dans le texte OCR (validation + scoring).
    prefer_* sert à pousser une valeur saisie/corrigée si elle est valide.
    """
    txt = ocr_text or ""
    txt_norm = _norm_for_search(txt)

    # 1) IBAN candidats -> longueur pays + validation mod97 + tolérance OCR
    iban_counts = extract_iban_candidates(txt, prefer_labels=True)

    # 2) BIC candidats -> validation + contexte bancaire strict
    # On ne garde pas les mots qui "ressemblent" à un BIC hors contexte.
    bic_counts = _contextual_bic_candidates(txt)

    # 3) Si user a déjà saisi une valeur valide, on la privilégie
    p_iban = _norm_iban(prefer_iban)
    if p_iban and validate_iban(p_iban):
        iban_counts[p_iban] += 1000  # boost énorme

    p_bic = _norm_bic(prefer_bic)
    if p_bic and validate_bic(p_bic):
        bic_counts[p_bic] += 1000

    # 4) choisir meilleur IBAN (fréquence + présence dans texte)
    best_iban = ""
    best_iban_score = -1
    for iban, c in iban_counts.items():
        occ = txt_norm.count(iban)  # texte normalisé sans espaces
        score = c * 10 + occ
        if score > best_iban_score:
            best_iban_score = score
            best_iban = iban

    # 5) choisir meilleur BIC (fréquence contexte + cohérence pays avec IBAN si dispo)
    best_bic = ""
    best_bic_score = -1
    iban_cc = best_iban[:2] if best_iban else ""

    for bic, c in bic_counts.items():
        occ = txt_norm.count(bic)
        score = c * 100 + occ
        # cohérence pays : BIC[4:6] doit matcher IBAN country (souvent vrai, très utile)
        if iban_cc and bic[4:6] == iban_cc:
            score += 50
        if score > best_bic_score:
            best_bic_score = score
            best_bic = bic

    return {
        "iban": best_iban or "",
        "bic": best_bic or "",
        "iban_candidates": iban_counts.most_common(5),
        "bic_candidates": bic_counts.most_common(5),
    }