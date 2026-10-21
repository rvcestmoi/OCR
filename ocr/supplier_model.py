import os
import json
import re
import unicodedata
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

    if not bic:
        return None
    if iban and not validate_iban(iban):
        iban = ""
    if not validate_bic(bic):
        return None

    return f"{iban}_{bic}"


def build_supplier_key_by_kundennr(kundennr: str) -> Optional[str]:
    """Clé canonique des modèles transporteurs basée sur le KundenNr/FFNR.

    Historiquement les modèles étaient nommés avec IBAN_BIC. Pour éviter les
    erreurs dues à un IBAN/BIC mal OCRisé ou absent, les nouveaux modèles sont
    maintenant rattachés au transporteur WinSped trouvé via le dossier
    (xxatour.FFNR). Le préfixe évite toute collision avec les anciens fichiers.
    """
    value = str(kundennr or "").replace(" ", "").replace("\u00A0", "").strip().upper()
    value = re.sub(r"[^A-Z0-9_-]", "", value)
    if not value:
        return None
    return f"KUNDENNR_{value}"


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


def _normalize_model_date(value: str) -> str:
    """Normalise une date en jj/mm/aaaa sans dépendre de invoice_parser.

    ``invoice_parser`` importe ce module : on garde donc cette petite fonction ici
    pour éviter une boucle d'import.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    m = re.fullmatch(r"\s*(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\s*", raw)
    if not m:
        return raw
    try:
        p1, p2, p3 = [int(x) for x in m.groups()]
    except Exception:
        return raw

    if p1 > 31:  # aaaa-mm-jj
        year, month, day = p1, p2, p3
    elif p3 > 31:  # jj-mm-aaaa
        day, month, year = p1, p2, p3
    else:  # année sur 2 chiffres
        day, month, year = p1, p2, 2000 + p3 if p3 < 100 else p3

    if not (1 <= day <= 31 and 1 <= month <= 12 and 1900 <= year <= 2100):
        return raw
    return f"{day:02d}/{month:02d}/{year:04d}"


def _date_search_variants(value: str) -> List[str]:
    d = _normalize_model_date(value)
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    if not m:
        return [str(value or "").strip()] if value else []
    day, month, year = m.groups()
    yy = year[-2:]
    return [
        f"{day}/{month}/{year}", f"{day}-{month}-{year}", f"{day}.{month}.{year}",
        f"{day}/{month}/{yy}", f"{day}-{month}-{yy}", f"{day}.{month}.{yy}",
        f"{year}-{month}-{day}", f"{year}/{month}/{day}", f"{year}.{month}.{day}",
    ]


def _strip_accents(s: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(ch)
    )


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


def _is_plausible_learned_label(label: str, field: str = "") -> bool:
    """Vérifie qu'une ligne proche ressemble bien à un libellé et pas à une valeur.

    Important pour les factures où le label est seul sur une ligne, par exemple :
    Rechnungs-Nr
    137549

    Les anciens JSON restent compatibles : cette fonction ne fait qu'améliorer
    l'apprentissage des nouveaux near_label.
    """
    lab = (label or "").strip().strip(" :;=-")
    if not lab or len(lab) < 3 or len(lab) > 80:
        return False
    if not re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", lab):
        return False

    up = lab.upper()
    compact = _strip_accents(_norm_for_search(lab))

    # Évite d'apprendre une valeur comme label.
    if re.fullmatch(r"[A-Z]{0,4}\d[A-Z0-9/_\-.]{2,}", up):
        return False
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", lab):
        return False

    if field == "invoice_number":
        if _is_bad_invoice_number_label_text(lab):
            return False
        return any(k in compact for k in (
            "FACTURE", "INVOICE", "INV", "FAKTUR", "RECHNUNG",
            "RECHNUNGSNR", "RECHNUNGSNUMMER", "NUMER", "NUMERO",
            "NUMBER", "NUMMER", "BELEG"
        ))
    if field == "invoice_date":
        if _is_bad_invoice_date_label_text(lab):
            return False
        return any(k in compact for k in (
            "DATE", "DATUM", "RECHNUNGSDATUM", "FACTURE", "INVOICE", "FAKTUR"
        ))

    return True


def _extract_label_near_value(text: str, value: str, window: int = 50, field: str = "") -> Optional[str]:
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
                # Si la valeur brute est présente dans la ligne, on utilise son index
                # réel. L'index normalisé peut être décalé par les espaces/tirets/points
                # (ex: "Rechnungs-Datum 06.03.2026").
                raw_match = re.search(re.escape(value.strip()), line, re.IGNORECASE) if value.strip() else None
                if raw_match:
                    left_part = line[:raw_match.start()].strip()
                else:
                    left_part = line[:value_idx].strip()

                if ':' in left_part:
                    candidate = left_part.split(':')[0].strip()
                else:
                    candidate = left_part.strip()

                if _is_plausible_learned_label(candidate, field):
                    return candidate

            # 2) Chercher un label dans les lignes précédentes (1-2 lignes).
            # Avant on ne retenait que les lignes avec ':', ce qui excluait
            # les factures allemandes du type "Rechnungs-Nr" puis valeur en dessous.
            for j in range(max(0, i - 2), i):
                prev_line = lines[j]
                label = prev_line.split(':', 1)[0].strip() if ':' in prev_line else prev_line.strip()
                if _is_plausible_learned_label(label, field):
                    return label

            # 3) Chercher un label dans la ligne suivante (cas label en dessous)
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                label = next_line.split(':', 1)[0].strip() if ':' in next_line else next_line.strip()
                if _is_plausible_learned_label(label, field):
                    return label

    return None


def _value_group_regex(field: str, value: str) -> str:
    v = (value or "").strip()

    if field == "invoice_date":
        return r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"

    vv = _norm_for_search(v)

    # ✅ spécial invoice_number : si c'est du type LETTRES + CHIFFRES (ex: F2511326)
    if field == "invoice_number":
        raw = (value or "").strip().upper().replace("\u00A0", " ")
        raw = re.sub(r"\s+", "", raw)

        # cas simple type F2511326
        m = re.fullmatch(r"([A-Z]{1,6})(\d{3,})", raw)
        if m:
            prefix, digits = m.groups()
            n = len(digits)

            if prefix == "F" and n == 7:
                return r"F\d{7}"

            lo = max(3, n - 1)
            hi = min(20, n + 1)
            return rf"{re.escape(prefix)}\d{{{lo},{hi}}}"

        # cas avec séparateurs conservés : FV1450/02/2026, INV-41-03, F.2026.001, etc.
        if any(sep in raw for sep in "/-_."):
            parts = re.split(r"([/_\-.])", raw)
            chunks = []

            for part in parts:
                if not part:
                    continue

                if re.fullmatch(r"[/_\-.]", part):
                    chunks.append(re.escape(part))
                elif part.isdigit():
                    chunks.append(rf"\d{{{len(part)}}}")
                elif re.fullmatch(r"[A-Z]{1,6}\d+", part):
                    m2 = re.fullmatch(r"([A-Z]{1,6})(\d+)", part)
                    prefix2, digits2 = m2.groups()
                    chunks.append(re.escape(prefix2) + rf"\d{{{len(digits2)}}}")
                elif re.fullmatch(r"[A-Z]+", part):
                    chunks.append(re.escape(part))
                elif re.fullmatch(r"[A-Z0-9]+", part):
                    chunks.append(rf"[A-Z0-9]{{{len(part)}}}")
                else:
                    chunks.append(re.escape(part))

            return "".join(chunks)

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


_INVOICE_DATE_LABEL_HINT_RE = re.compile(
    r"(?:DATE\s+D['’]?[ÉE]MISSION|DATE\s+FACTURE|INVOICE\s+DATE|ISSUED?\s+DATE|RECHNUNGS\s*[-.]?\s*DATUM|RECHNUNGSDATUM|DATUM)",
    re.IGNORECASE,
)

_DUE_DATE_HINT_RE = re.compile(
    r"(?:[ÉE]CH[ÉE]ANCE|DUE\s+DATE|PAYMENT\s+DUE|PAYABLE|R[ÈE]GLEMENT|VIREMENT|MATURIT[ÉE])",
    re.IGNORECASE,
)

_ORDER_DATE_HINT_RE = re.compile(
    r"(?:AUFTRAGS\s*[-.]?\s*DATUM|ORDER\s+DATE|DATE\s+COMMANDE)",
    re.IGNORECASE,
)

_BAD_INVOICE_DATE_LABEL_HINT_RE = re.compile(
    r"(?:DELIVERY\s+DATE|DATE\s+LIVRAISON|LIVRAISON|CHARGEMENT|LOAD(?:ING)?\s+DATE|UNLOAD(?:ING)?\s+DATE|PICKUP\s+DATE|PICK\s*UP\s+DATE|COLLECTION\s+DATE|DUE\s+DATE|PAYMENT\s+DUE|[ÉE]CH[ÉE]ANCE|ORDER\s+DATE|DATE\s+COMMANDE|AUFTRAGS\s*[-.]?\s*DATUM)",
    re.IGNORECASE,
)


def _is_bad_invoice_date_label_text(label: str) -> bool:
    raw = str(label or "").strip()
    if not raw:
        return False
    if _BAD_INVOICE_DATE_LABEL_HINT_RE.search(raw):
        return True

    # Les patterns stockés peuvent contenir des séquences regex échappées
    # (ex: Delivery\s+date). On teste aussi une version compacte.
    compact = _strip_accents(re.sub(r"[^A-Z0-9]", "", raw.upper()))
    if any(token in compact for token in (
        "DUEDATE", "PAYMENTDUE", "ECHEANCE", "ECHAENCE",
        "ORDERDATE", "DATECOMMANDE", "AUFTRAGSDATUM",
        "DATELIVRAISON", "LIVRAISON", "CHARGEMENT",
        "LOADINGDATE", "UNLOADINGDATE", "PICKUPDATE", "COLLECTIONDATE",
    )):
        return True
    if "DELIVERY" in compact and "DATE" in compact:
        return True
    if "LOAD" in compact and "DATE" in compact:
        return True
    if "PICKUP" in compact and "DATE" in compact:
        return True
    return False


def _is_bad_invoice_date_rule(rule: Dict[str, Any]) -> bool:
    txt = " ".join([
        str((rule or {}).get("label_regex") or ""),
        str((rule or {}).get("regex") or ""),
    ])
    return _is_bad_invoice_date_label_text(txt)


def _score_invoice_date_candidate(rule: Dict[str, Any], context: str) -> int:
    score = int(rule.get("hit_count", 0)) * 10
    mode = (rule.get("mode") or "").strip().lower()
    rule_text = " ".join([
        str(rule.get("label_regex") or ""),
        str(rule.get("regex") or ""),
    ])
    haystack = f"{rule_text}\n{context or ''}"

    if mode == "near_label":
        score += 80
    elif mode == "line_regex":
        score += 30

    if _INVOICE_DATE_LABEL_HINT_RE.search(haystack):
        score += 180
    if _DUE_DATE_HINT_RE.search(haystack):
        score -= 220
    if _ORDER_DATE_HINT_RE.search(haystack):
        score -= 160
    if _BAD_INVOICE_DATE_LABEL_HINT_RE.search(haystack):
        score -= 260

    if re.search(r"DATE\s+D['’]?[ÉE]MISSION", haystack, re.IGNORECASE):
        score += 140
    if re.search(r"RECHNUNGS\s*[-.]?\s*DATUM|RECHNUNGSDATUM", haystack, re.IGNORECASE):
        score += 180
    if re.search(r"\bFACTURE\b|\bINVOICE\b", haystack, re.IGNORECASE):
        score += 40

    return score


def _model_ts(value: Any) -> float:
    """Timestamp robuste pour trier les règles apprises par fraîcheur.

    Les modèles existants stockent des dates ISO simples, sans timezone.
    En cas de valeur absente/invalide, on retourne 0 pour garder la compatibilité.
    """
    try:
        s = str(value or "").strip()
        if not s:
            return 0.0
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _rule_last_known_ts(rule: Dict[str, Any]) -> float:
    return max(_model_ts(rule.get("last_seen")), _model_ts(rule.get("created_at")))


def _invoice_rule_specificity(rule: Dict[str, Any]) -> int:
    """Score de spécificité d'une règle n° facture.

    Objectif : éviter qu'une règle très large apprise anciennement ou utilisée
    souvent capte une petite valeur parasite (ex: "27") alors qu'une règle plus
    récente et précise existe (ex: F\d{4}-\d{4}).
    """
    txt = " ".join([
        str(rule.get("label_regex") or ""),
        str(rule.get("value_regex") or ""),
        str(rule.get("regex") or ""),
    ]).upper()
    score = 0
    if "INVOICE" in txt or "FACTURE" in txt or "RECHNUNG" in txt or "FAKTUR" in txt:
        score += 30
    if re.search(r"F\\D*\\d|[A-Z]\\\\D|\\\\D\\{", txt):
        score += 45
    if re.search(r"\\\\D\{\d", txt) or re.search(r"\[A-Z", txt):
        score += 20
    if "COMPANY" in txt or "VAT" in txt or "CUSTOMER" in txt or "CLIENT" in txt:
        score -= 120
    if _is_bad_invoice_number_rule(rule):
        score -= 500
    if "[A-Z0-9]" in txt and "*" in txt:
        score -= 15
    return score



def _invoice_rule_text(rule: Dict[str, Any]) -> str:
    return " ".join([
        str(rule.get("label_regex") or ""),
        str(rule.get("value_regex") or ""),
        str(rule.get("regex") or ""),
    ])


def _is_bad_invoice_number_label_text(label: str) -> bool:
    """Rejette les faux libellés appris pour les numéros de facture.

    Exemple réel : ``invoice@ed-trans com`` contient le mot ``invoice`` mais
    c'est une adresse e-mail ; la règle apprenait ensuite ``FR83453080848``
    comme numéro de facture.
    """
    raw = str(label or "").strip()
    up = raw.upper()
    compact = re.sub(r"[^A-Z0-9@]", "", up)
    if not raw:
        return False

    email_like = (
        "@" in raw
        or "\\@" in raw
        or "EMAIL" in up
        or "E-MAIL" in up
        or ("MAIL" in up and ("@" in raw or "INVOICE" in up or "FACTURE" in up))
        or re.search(r"[A-Z0-9._%+\-]+\s*@", up) is not None
    )
    if email_like:
        return True

    bad_words = (
        "SIRET", "SIREN", "APE", "CAPITAL", "INTRACOM",
        "TVA", "VAT", "USTID", "UST-ID",
        "COMPANYNUMBER", "CUSTOMERNUMBER", "CLIENTNUMBER", "CODECLIENT",
        "IBAN", "BIC", "SWIFT",
        # Très important : un modèle invoice_number ne doit jamais apprendre
        # des libellés de dates. Exemple réel : label "Invoice date" qui
        # renvoie ensuite 16/03/2026 comme numéro de facture.
        "DATE", "DELIVERYDATE", "DUEDATE", "ECHEANCE", "ECHAENCE", "DATELIVRAISON",
    )
    if any(w in compact for w in bad_words):
        return True

    if "EDTRANS" in compact and ("INVOICE" in compact or "FACTURE" in compact):
        return True

    return False


def _is_bad_invoice_number_rule(rule: Dict[str, Any]) -> bool:
    return _is_bad_invoice_number_label_text(_invoice_rule_text(rule))


def _is_bad_invoice_number_value(value: str) -> bool:
    raw = str(value or "").strip()
    # Une date ne doit jamais devenir un numéro de facture appris/appliqué.
    # Exemple réel : une règle invoice_number apprenait "Invoice date" et
    # ressortait ensuite 16/03/2026 au lieu du vrai numéro.
    if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", raw):
        return True

    v = re.sub(r"[\s\u00A0\-_.]", "", raw.upper())
    if not v:
        return True

    # TVA intracom FR : FR + 2 caractères de clé + 9 chiffres
    # ou variantes OCR compactes FR + 10/12 chiffres.
    if re.fullmatch(r"FR[A-Z0-9]{2}\d{9}", v):
        return True
    if re.fullmatch(r"FR\d{10,12}", v):
        return True

    # TVA intracom d'autres pays : deux lettres pays + longue suite alphanum.
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{8,14}", v) and re.search(r"\d{6,}", v):
        return True

    return False

def _invoice_number_rule_sort_key(rule: Dict[str, Any]) -> tuple:
    mode = (rule.get("mode") or "").strip().lower()
    return (
        _rule_last_known_ts(rule),
        _model_ts(rule.get("created_at")),
        1 if mode == "near_label" else 0,
        _invoice_rule_specificity(rule),
        int(rule.get("hit_count", 0) or 0),
    )


def _is_weak_invoice_number_value(value: str) -> bool:
    v = (value or "").strip().upper()
    compact = re.sub(r"[^A-Z0-9]", "", v)
    if not compact:
        return True
    # Cas typique du bug : une règle large près de "Invoice number" récupère
    # un petit nombre parasite comme 27 au lieu de F2026-9833.
    if compact.isdigit() and len(compact) <= 3:
        return True
    if len(compact) <= 2:
        return True
    return False


def _confirmed_value_sort_key(item: Dict[str, Any]) -> tuple:
    return (
        _model_ts((item or {}).get("last_seen")),
        _model_ts((item or {}).get("created_at")),
        int((item or {}).get("hit_count", 0) or 0),
    )


def _confirmed_value_variants(field: str, value: str) -> List[str]:
    v = str(value or "").strip()
    if not v:
        return []
    if field == "invoice_date":
        return _date_search_variants(v)
    if field in ("iban", "bic"):
        compact = re.sub(r"[\s\u00A0-]", "", v.upper())
        return [v, compact]
    return [v]


def _text_contains_confirmed_value(text: str, value: str, field: str = "") -> bool:
    if not text or not value:
        return False
    haystack = _norm_for_search(text)
    for candidate in _confirmed_value_variants(field, value):
        if candidate and _norm_for_search(candidate) in haystack:
            return True
    return False


def _clean_confirmed_value_for_field(field: str, value: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if field == "invoice_number":
        return "" if _is_bad_invoice_number_value(v) else v
    if field == "invoice_date":
        d = _normalize_model_date(v)
        return d if re.fullmatch(r"\d{2}/\d{2}/\d{4}", d or "") else ""
    if field == "iban":
        n = re.sub(r"[\s\u00A0-]", "", v.upper())
        return n if validate_iban(n) else ""
    if field == "bic":
        n = re.sub(r"[\s\u00A0-]", "", v.upper())
        return n if validate_bic(n) else ""
    return v


def _match_confirmed_value(text: str, model: dict, field: str) -> str:
    confirmed = (model or {}).get("confirmed_values") or {}
    values = confirmed.get(field) or []
    if not isinstance(values, list):
        return ""

    for item in sorted([x for x in values if isinstance(x, dict)], key=_confirmed_value_sort_key, reverse=True):
        value = _clean_confirmed_value_for_field(field, str(item.get("value") or ""))
        if not value:
            continue
        if _text_contains_confirmed_value(text, value, field):
            return value
    return ""


def _match_confirmed_invoice_number(text: str, model: dict) -> str:
    # Compatibilité avec les appels existants ; la logique est maintenant générique.
    return _match_confirmed_value(text, model, "invoice_number")


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

    # Invoice number : ligne + fallback label.
    # Si l'OCR automatique a mis une date / TVA / IBAN-like dans le champ n° facture,
    # on n'apprend surtout pas cette erreur dans le modèle fournisseur.
    if invoice_number and not _is_bad_invoice_number_value(invoice_number):
        line = _find_line_with_value(ocr_text, invoice_number)
        rx = _make_line_regex(line, "invoice_number", invoice_number) if line else None
        if rx and _norm_for_search(line or "") != _norm_for_search(invoice_number):
            patterns["invoice_number"].append({
                "mode": "line_regex",
                "regex": rx,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

        # Apprendre le label spécifique trouvé près de la valeur
        specific_label = _extract_label_near_value(ocr_text, invoice_number, field="invoice_number")
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
            "label_regex": r"\b(Num[eé]ro\s+de\s+facture|Numero\s+de\s+facture|N[°O]\s*FACTURE|FACTURE\s*(N[°O]|NO\.?)|INVOICE\s*(NO\.?|NUMBER)|INV\.?\s*NO\.?|NUMER\s+FAKTURY|RECHNUNGS\s*[-.]?\s*(NR|NO|NUMMER)|RECHNUNGSNUMMER|BELEGNUMMER)\b",
            "value_regex": r"([A-Z0-9][A-Z0-9\-_/\.]*\d[A-Z0-9\-_/\.]*)",
            "window": 200,
            "group": 1,
            "hit_count": 0,
            "created_at": _now_iso(),
        })
    # Date : ligne + fallback label
    if invoice_date:
        line = _find_line_with_value(ocr_text, invoice_date)
        rx = _make_line_regex(line, "invoice_date", invoice_date) if line else None
        if rx and _norm_for_search(line or "") != _norm_for_search(invoice_date):
            patterns["invoice_date"].append({
                "mode": "line_regex",
                "regex": rx,
                "group": 1,
                "hit_count": 0,
                "created_at": _now_iso(),
            })

        # Apprendre le label spécifique trouvé près de la valeur
        specific_label = _extract_label_near_value(ocr_text, invoice_date, field="invoice_date")
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
        # Les modèles historiques peuvent encore contenir ces règles :
        # ne jamais les recopier lors d’un nouvel apprentissage.
        if field in ("vat_rate", "vat_base", "vat_amount"):
            continue
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

        if field == "invoice_number":
            # Supprime les faux patterns appris anciennement, ex :
            # label ``invoice@ed-trans com`` qui attrape une TVA FR...
            out = [r for r in out if not _is_bad_invoice_number_rule(r)]
            # Pour les numéros de facture, la dernière règle apprise/confirmée
            # doit primer sur la plus utilisée. Cela évite qu'un ancien pattern
            # générique continue à gagner contre un pattern fournisseur plus précis.
            out.sort(key=_invoice_number_rule_sort_key, reverse=True)
        elif field == "invoice_date":
            # Supprime les faux patterns appris anciennement, ex :
            # Delivery date / Due date utilisés par erreur comme date de facture.
            out = [r for r in out if not _is_bad_invoice_date_rule(r)]
            out.sort(key=lambda x: (_rule_last_known_ts(x), int(x.get("hit_count", 0) or 0)), reverse=True)
        else:
            out.sort(key=lambda x: int(x.get("hit_count", 0)), reverse=True)
        merged[field] = out[:max_rules_per_field]

    return merged


def extract_fields_with_model(text: str, model: dict) -> Dict[str, str]:
    """Applique les patterns du modèle (si présents) et retourne les champs trouvés."""
    patterns = (model or {}).get("patterns") or {}
    out: Dict[str, str] = {}

    if not text:
        return out

    src = (text or "").replace("\u00A0", " ")

    # Les valeurs corrigées manuellement par l'utilisateur sont prioritaires,
    # mais uniquement si elles sont réellement présentes dans l'OCR courant.
    # Exemple : si l'utilisateur corrige une date de facture puis sauvegarde,
    # une prochaine OCR du même document doit reprendre cette date avant toute
    # ancienne règle erronée qui retournerait une date de livraison/échéance.
    for confirmed_field in ("invoice_number", "invoice_date", "iban", "bic"):
        confirmed_value = _match_confirmed_value(src, model, confirmed_field)
        if confirmed_value:
            out[confirmed_field] = confirmed_value

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

        # Une valeur confirmée par sauvegarde utilisateur est déjà prioritaire.
        if field in out:
            continue

        # Pour invoice_number :
        # - ne plus trier par hit_count, mais par dernière règle connue
        #   (last_seen/created_at), afin que le dernier pattern fournisseur
        #   confirmé soit essayé avant un ancien pattern générique ;
        # - ignorer les valeurs très faibles comme "27" si un candidat plus
        #   crédible est disponible ensuite.
        if field == "invoice_number":
            rules.sort(key=_invoice_number_rule_sort_key, reverse=True)
            weak_candidate = None

            for r in rules:
                # Les anciens modèles peuvent contenir des faux labels appris
                # dans invoice_number, par exemple "Company number". On les
                # neutralise ici sans supprimer le JSON existant.
                rule_text = " ".join([str(r.get("label_regex") or ""), str(r.get("regex") or "")]).upper()
                if re.search(r"COMPANY\s*\\?\s*NUMBER|VAT\s*\\?\s*NUMBER|CUSTOMER\s*\\?\s*NUMBER", rule_text):
                    continue
                if _is_bad_invoice_number_rule(r):
                    continue

                val = apply_rule(field, r)
                if not val:
                    continue
                val = val.strip()
                if not val:
                    continue

                if _is_bad_invoice_number_value(val):
                    continue

                if _is_weak_invoice_number_value(val):
                    if weak_candidate is None:
                        weak_candidate = val
                    continue

                out[field] = val
                break

            if field not in out and weak_candidate:
                out[field] = weak_candidate
            continue

        # Pour invoice_date, scorer tous les candidats pour éviter de prendre une échéance
        if field == "invoice_date":
            best_date = None
            best_score = -10**9
            for r in rules:
                if _is_bad_invoice_date_rule(r):
                    continue
                mode = (r.get("mode") or "").strip().lower()
                if mode == "line_regex":
                    rx = re.compile(r.get("regex", ""), re.IGNORECASE | re.MULTILINE)
                    g = int(r.get("group", 0))
                    for m in rx.finditer(src):
                        val = (m.group(g) if g else m.group(0) or "").strip()
                        if not val:
                            continue
                        ctx = src[max(0, m.start() - 120): min(len(src), m.end() + 120)]
                        score = _score_invoice_date_candidate(r, ctx)
                        if score > best_score:
                            best_score = score
                            best_date = val
                elif mode == "near_label":
                    label_rx = re.compile(r.get("label_regex", ""), re.IGNORECASE)
                    value_rx = re.compile(r.get("value_regex", ""), re.IGNORECASE)
                    window = int(r.get("window", 120))
                    g = int(r.get("group", 0))
                    for lm in label_rx.finditer(src):
                        chunk = src[lm.end(): lm.end() + window]
                        vm = value_rx.search(chunk)
                        if not vm:
                            continue
                        val = (vm.group(g) if g else vm.group(0) or "").strip()
                        if not val:
                            continue
                        abs_end = min(len(src), lm.end() + vm.end())
                        # Contexte volontairement local : avec un OCR contenant
                        # "Auftrags-Datum" puis "Rechnungs-Datum", un contexte
                        # trop large mélange les deux labels et peut choisir la
                        # date de commande au lieu de la date de facture.
                        ctx = src[max(0, lm.start() - 25): min(len(src), abs_end + 80)]
                        score = _score_invoice_date_candidate(r, ctx)
                        if score > best_score:
                            best_score = score
                            best_date = val
                else:
                    val = apply_rule(field, r)
                    if val:
                        score = _score_invoice_date_candidate(r, val)
                        if score > best_score:
                            best_score = score
                            best_date = val.strip()
            if best_date:
                cleaned_date = _normalize_model_date(best_date.strip())
                if re.fullmatch(r"\d{2}/\d{2}/\d{4}", cleaned_date or ""):
                    out[field] = cleaned_date
            continue

        # Comportement standard pour les autres champs
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