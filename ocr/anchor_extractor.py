import re
import pytesseract
from pytesseract import Output


def ocr_words_with_positions(pil_image, lang="eng+fra+deu+spa+ita+nld"):
    """
    Retourne une liste de mots OCR avec leurs positions depuis une image PIL.
    """
    data = pytesseract.image_to_data(
        pil_image,
        lang=lang,
        output_type=Output.DICT,
        config="--psm 6"
    )

    words = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        if not text:
            continue

        words.append({
            "text": text,
            "x": int(data["left"][i]),
            "y": int(data["top"][i]),
            "w": int(data["width"][i]),
            "h": int(data["height"][i]),
        })

    return words


def extract_by_anchor(
    words,
    anchor_texts,
    direction="right",
    max_distance=300,
    same_line_tolerance=12,
    regex=None
):
    """
    Cherche une ancre (mot exact dans anchor_texts), puis extrait les mots
    autour de l'ancre selon une direction.
    - direction: "right" ou "below"
    - regex: si fourni, retourne le 1er match dans le texte extrait
    """
    anchor_texts = [a.strip().lower() for a in anchor_texts if a.strip()]
    if not anchor_texts:
        return None

    for anchor in words:
        anchor_text = anchor["text"].strip().lower().rstrip(":")
        if anchor_text not in anchor_texts:
            continue
        

        ax, ay = anchor["x"], anchor["y"]

        candidates = []
        for w in words:
            if direction == "right":
                # même ligne (tolérance)
                if abs(w["y"] - ay) > same_line_tolerance:
                    continue
                if w["x"] <= ax:
                    continue
                if (w["x"] - ax) > max_distance:
                    continue

            elif direction == "below":
                if w["y"] <= ay:
                    continue
                if (w["y"] - ay) > max_distance:
                    continue

            else:
                raise ValueError("direction doit être 'right' ou 'below'")

            candidates.append(w)

        candidates.sort(key=lambda d: d["x"])
        extracted = " ".join(c["text"] for c in candidates).strip()

        if not extracted:
            continue

        if regex:
            m = re.search(regex, extracted)
            if m:
                return m.group(0)

        return extracted

    return None

def extract_iban(words):
    """
    Extrait un IBAN depuis les mots OCR
    """
    # Reconstituer des lignes (approximation par y)
    lines = {}
    for w in words:
        y = w["y"] // 10  # regroupe par lignes proches
        lines.setdefault(y, []).append(w["text"])

    # Regex IBAN robuste
    iban_regex = r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{13,30}\b"


    for line_words in lines.values():
        line = " ".join(line_words).upper()
        match = re.search(iban_regex, line)
        if match:
            return match.group(0).replace(" ", "")

    return None

def extract_bic(words):
    """
    Extrait un BIC / SWIFT de manière robuste
    """
    import re

    VALID_COUNTRIES = {
        "DE", "FR", "BE", "NL", "LU", "ES", "IT", "PT",
        "AT", "CH", "PL", "CZ", "SK", "HU", "RO", "BG",
        "LT", "LV", "EE", "FI", "SE", "DK", "NO", "IE"
    }

    BLACKLIST = {
        "LOGISTIK", "TRANSPORT", "INFORMATION", "INVOICE",
        "FACTURE", "RECHNUNG", "BANKING", "DETAILS"
    }

    bic_regex = re.compile(
        r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?\b"
    )

    # regrouper par lignes
    lines = {}
    for w in words:
        y = w["y"] // 10
        lines.setdefault(y, []).append(w["text"])

    for line_words in lines.values():
        line = " ".join(line_words).upper()

        for m in bic_regex.finditer(line):
            bic = m.group(0)

            # longueur exacte
            if len(bic) not in (8, 11):
                continue

            # blacklist
            if bic in BLACKLIST:
                continue

            # code pays (positions 5-6)
            country = bic[4:6]
            if country not in VALID_COUNTRIES:
                continue

            return bic

    return None
