# db/bank_repository.py

from db.repository import BaseRepository


class BankRepository(BaseRepository):
    """
    Accès à la table XXAKunBank
    """

    def find_by_iban_bic(self, iban: str, bic: str):
        query = """
            SELECT *
            FROM XXAKunBank
            WHERE IBAN = ?
              AND SWIFT = ?
        """
        return self.fetch_one(query, (iban, bic))

    def find_by_iban(self, iban: str):
        query = """
            SELECT *
            FROM XXAKunBank
            WHERE IBAN = ?
        """
        return self.fetch_one(query, (iban,))

    def get_all_bank_infos_by_kundennr(self, kundennr: str) -> list[dict]:
        kundennr = str(kundennr or "").strip()
        if not kundennr:
            return []

        query = """
            SELECT
                LTRIM(RTRIM(COALESCE(IBAN, '')))  AS iban,
                LTRIM(RTRIM(COALESCE(SWIFT, ''))) AS bic,
                LTRIM(RTRIM(COALESCE(BANKNAME, ''))) AS bank_name
            FROM XXAKunBank
            WHERE LTRIM(RTRIM(COALESCE(KUNDENNR, ''))) = ?
            ORDER BY IBAN, SWIFT
        """
        return self.fetch_all(query, (kundennr,)) or []
