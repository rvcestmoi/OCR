import re
from dataclasses import dataclass


@dataclass
class InvoiceData:
    iban: str = ""
    bic: str = ""
    invoice_date: str = ""
    invoice_number: str = ""
    folder_number: str = ""


def parse_invoice(text: str) -> InvoiceData:
    """
    Analyse le texte OCR d'une facture et extrait
    IBAN, BIC, date facture, numéro facture, numéro dossier
    """
    data = InvoiceData()

    # =========================
    # IBAN
    # =========================
    iban_match = re.search(
        r'\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}\b',
        text.replace(" ", "")
    )
    if iban_match:
        data.iban = iban_match.group()

    # =========================
    # BIC
    # =========================
    bic_match = re.search(
        r'\b[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?\b',
        text
    )
    if bic_match:
        data.bic = bic_match.group()

    # =========================
    # Date de facture
    # formats : 11.06.2025 / 11/06/2025 / 11-06-2025
    # =========================
    date_match = re.search(
        r'\b\d{2}[./-]\d{2}[./-]\d{4}\b',
        text
    )
    if date_match:
        data.invoice_date = date_match.group()

    # =========================
    # Numéro de facture
    # Ex: "Invoice No.", "Inv. No.", "Facture", etc.
    # =========================
    invoice_match = re.search(
        r'(Invoice|Inv\.?|Facture)[^\d]{0,10}(\d{4,})',
        text,
        re.IGNORECASE
    )
    if invoice_match:
        data.invoice_number = invoice_match.group(2)

    # =========================
    # Numéro de dossier
    # Ex: "N° 78063936", "LS:", "Dossier"
    # =========================
    folder_match = re.search(
        r'(N°|No\.?|LS:|Dossier)[^\d]{0,5}(\d{6,})',
        text,
        re.IGNORECASE
    )
    if folder_match:
        data.folder_number = folder_match.group(2)

    return data
