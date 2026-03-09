import re

ALLOWED_FOLDER_PREFIXES = (
    "150",
    "845",
    "255",
    "355",
    "445",
    "645",
    "675",
    "695",
    "725",
    "785",
)

_PREFIXES_3 = tuple(p for p in ALLOWED_FOLDER_PREFIXES if len(p) == 3 and p != "150")
_PREFIXES_3_REGEX = "|".join(_PREFIXES_3)

DOSSIER_PATTERN = re.compile(
    rf"(?<!\d)(?:"
    rf"150\d{{5,8}}"
    rf"|(?:{_PREFIXES_3_REGEX})\d{{6,8}}"
    rf")(?!\d)"
)

DOSSIER_CANDIDATE = re.compile(
    rf"(?<!\d)(?:{'|'.join(ALLOWED_FOLDER_PREFIXES)})"
    rf"[0-9 \u00A0\-\n]{{4,25}}\d(?!\d)"
)


def normalize_folder_candidate(value: str) -> str:
    if not value:
        return ""
    return (
        str(value)
        .replace(" ", "")
        .replace("\u00A0", "")
        .replace("-", "")
        .replace("\n", "")
        .strip()
    )


def is_valid_folder_number(value: str) -> bool:
    value = normalize_folder_candidate(value)
    return bool(DOSSIER_PATTERN.fullmatch(value))


def extract_folder_numbers_from_text(text: str) -> list[str]:
    if not text:
        return []

    found: list[str] = []
    seen: set[str] = set()

    for match in DOSSIER_PATTERN.finditer(text):
        folder = match.group(0).strip()
        if folder not in seen:
            seen.add(folder)
            found.append(folder)

    for match in DOSSIER_CANDIDATE.finditer(text):
        raw = match.group(0)
        folder = normalize_folder_candidate(raw)
        if folder and folder not in seen and is_valid_folder_number(folder):
            seen.add(folder)
            found.append(folder)

    return found