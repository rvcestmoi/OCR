from ocr.field_detector import guess_field, detect_fields_multilingual

SAMPLES = [
    ("FR76 4255 9100 0008 0097 7075 492", "iban"),
    ("CCOPFRPPXXX", "bic"),
    ("Date facture : 26/02/2026", "date"),
    ("Invoice date: 2026-02-26", "date"),
    ("Rechnungsnummer: F2511326", "invoice_number"),
    ("Factuurnummer: 2026-00451", "invoice_number"),
    ("Número de factura: FAC-2026-00098", "invoice_number"),
    ("Numer faktury: FV/2026/02/123", "invoice_number"),
    ("TourNr 845123456", "folder_number"),
    ("N° dossier : 1501234567", "folder_number"),
    ("Αριθμός τιμολογίου: INV-2026-77", "invoice_number"),
    ("Номер на фактура: 2026-884", "invoice_number"),
]

for sample, expected in SAMPLES:
    got = guess_field(sample)
    print(f"{sample!r} -> {got!r}")
    assert got == expected, f"Expected {expected!r}, got {got!r} for {sample!r}"

blob = """
Rechnungsnummer: F2511326
Rechnungsdatum: 26.02.2026
IBAN: FR76 4255 9100 0008 0097 7075 492
SWIFT/BIC: CCOPFRPPXXX
TourNr: 845123456
"""

detected = detect_fields_multilingual(blob)
print(detected)
assert detected["invoice_number"] == "F2511326"
assert detected["date"] == "26.02.2026"
assert detected["iban"] == "FR7642559100000800977075492"
assert detected["bic"] == "CCOPFRPPXXX"
assert detected["folder_number"] == "845123456"

print("OK")