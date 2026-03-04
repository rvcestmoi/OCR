import os
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from collections import Counter


MODEL_DIR = r"C:\git\OCR\OCR\models\suppliers"


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
    return re.sub(r"[\s\u00A0]", "", (s or "").upper())


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

        # fallback : au moins une lettre + au moins un chiffre
        return r"[A-Z][A-Z0-9\-_/\.]*\d[A-Z0-9\-_/\.]*"

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


def validate_iban(iban: str) -> bool:
    s = (iban or "").replace(" ", "").upper().strip()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", s):
        return False

    rearr = s[4:] + s[:4]
    digits = ""
    for ch in rearr:
        digits += ch if ch.isdigit() else str(ord(ch) - 55)  # A=10

    mod = 0
    for i in range(0, len(digits), 7):
        mod = int(str(mod) + digits[i:i + 7]) % 97
    return mod == 1


def validate_bic(bic: str) -> bool:
    s = (bic or "").replace(" ", "").upper().strip()
    return bool(re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", s))


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
            # ✅ Invoice number : near_label basé sur le FORMAT de l'exemple
            # (évite de capturer 30/11/2025)
            group_re = _value_group_regex("invoice_number", invoice_number)  # ex: F\d{7}
            patterns["invoice_number"].append({
                "mode": "near_label",
                "label_regex": r"\b(N[°O]\s*FACTURE|INVOICE\s*(NO\.?|NUMBER)|INV\.?\s*NO\.?)\b",
                "value_regex": rf"(\b{group_re}\b)",
                "window": 200,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

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
                "example_line": line,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

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

    # 1) IBAN candidats -> validation mod97
    iban_raw = [m.group(0) for m in _IBAN_CAND_RX.finditer(txt)]
    iban_norm = [_norm_iban(x) for x in iban_raw]
    iban_valid = [x for x in iban_norm if validate_iban(x)]
    iban_counts = Counter(iban_valid)

    # 2) BIC candidats -> validation format
    bic_raw = [m.group(0) for m in _BIC_CAND_RX.finditer(txt)]
    bic_norm = [_norm_bic(x) for x in bic_raw]
    bic_valid = [x for x in bic_norm if validate_bic(x)]
    bic_counts = Counter(bic_valid)

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

    # 5) choisir meilleur BIC (fréquence + cohérence pays avec IBAN si dispo)
    best_bic = ""
    best_bic_score = -1
    iban_cc = best_iban[:2] if best_iban else ""

    for bic, c in bic_counts.items():
        occ = txt_norm.count(bic)
        score = c * 10 + occ
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