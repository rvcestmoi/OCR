# ocr/field_detector.py

import re
from typing import Dict, Any, List, Optional


# ------------------------------------------------------------
# Compatibilité UI : utilisé par ui/ocr_text_view.py
# ------------------------------------------------------------
def guess_field(text: str) -> str | None:
    """
    Heuristique simple pour suggérer un champ à partir d'un texte sélectionné
    dans l'UI. Retourne une clé de champ (ex: 'iban', 'bic', ...) ou None.
    """
    raw = text.strip()
    compact = raw.replace(" ", "").upper()
    scores: Dict[str, int] = {}

    def add(field: str, value: int) -> None:
        scores[field] = scores.get(field, 0) + value

    # IBAN (FR76..., DE89..., etc.)
    if re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", compact):
        add("iban", 100)

    # BIC (8 ou 11 caractères)
    if re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", compact):
        add("bic", 90)

    # Date
    if re.search(r"\b\d{2}[./-]\d{2}[./-]\d{2,4}\b", raw):
        add("date", 70)

    # Indices de facture
    if re.search(r"\b(INV|INVOICE|FACT|FA|FACTURE)\b", compact):
        add("invoice_number", 40)

    # Numérique pur : souvent n° dossier / n° facture
    if compact.isdigit():
        add("folder_number", 20)
        add("invoice_number", 20)

    # Longueur typique d'un n° facture
    if 5 <= len(compact) <= 12:
        add("invoice_number", 10)

    return max(scores, key=scores.get) if scores else None


# ------------------------------------------------------------
# Version "clean" : détection de champs 100% textuelle
# ------------------------------------------------------------
class FieldDetector:
    """
    Détecteur de champs métier basé uniquement sur le texte (sans coordonnées).
    """

    def __init__(self, field_patterns: Dict[str, str]):
        self.field_patterns = {
            name: re.compile(pattern, re.IGNORECASE)
            for name, pattern in field_patterns.items()
        }

    def detect(self, text: str, anchors: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
        result: Dict[str, Any] = {}

        for field_name, pattern in self.field_patterns.items():
            matches = pattern.findall(text)
            if not matches:
                continue

            values = self._normalize(matches)
            result[field_name] = values[0] if len(values) == 1 else values

        if anchors:
            self._merge_with_anchors(result, anchors)

        return result

    def _normalize(self, matches: List[Any]) -> List[Any]:
        cleaned: List[Any] = []

        for m in matches:
            if isinstance(m, tuple):
                m = " ".join(m)
            m = str(m).strip()

            # Normalisation numérique simple
            if re.fullmatch(r"\d+(?:[.,]\d+)?", m):
                cleaned.append(float(m.replace(",", ".")))
            else:
                cleaned.append(m)

        return cleaned

    def _merge_with_anchors(self, result: Dict[str, Any], anchors: Dict[str, List[str]]) -> None:
        for key, values in anchors.items():
            if key not in result and values:
                result[key] = values[0] if len(values) == 1 else values
