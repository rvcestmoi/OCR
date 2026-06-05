# db/bank_repository.py

from db.repository import BaseRepository


class BankRepository(BaseRepository):
    """
    Accès à la table XXAKunBank
    """

    @staticmethod
    def _normalize_bank_value(value: str) -> str:
        return (
            str(value or "")
            .replace(" ", "")
            .replace(" ", "")
            .replace("-", "")
            .upper()
            .strip()
        )

    def find_by_iban_bic(self, iban: str, bic: str):
        iban_norm = self._normalize_bank_value(iban)
        bic_norm = self._normalize_bank_value(bic)
        query = """
            SELECT *
            FROM XXAKunBank
            WHERE REPLACE(REPLACE(REPLACE(UPPER(COALESCE(IBAN, '')), ' ', ''), '-', ''), CHAR(160), '') = ?
              AND (
                    REPLACE(REPLACE(REPLACE(UPPER(COALESCE(SWIFT, '')), ' ', ''), '-', ''), CHAR(160), '') = ?
                    OR LEFT(REPLACE(REPLACE(REPLACE(UPPER(COALESCE(SWIFT, '')), ' ', ''), '-', ''), CHAR(160), ''), 8) = ?
                  )
        """
        return self.fetch_one(query, (iban_norm, bic_norm, bic_norm[:8]))

    def find_by_iban(self, iban: str):
        iban_norm = self._normalize_bank_value(iban)
        query = """
            SELECT *
            FROM XXAKunBank
            WHERE REPLACE(REPLACE(REPLACE(UPPER(COALESCE(IBAN, '')), ' ', ''), '-', ''), CHAR(160), '') = ?
        """
        return self.fetch_one(query, (iban_norm,))

    def get_all_bank_infos_by_kundennr(self, kundennr: str) -> list[dict]:
        kundennr = str(kundennr or "").strip()
        if not kundennr:
            return []

        query = """
            SELECT
                LTRIM(RTRIM(COALESCE(IBAN, '')))  AS iban,
                LTRIM(RTRIM(COALESCE(SWIFT, ''))) AS bic,
                LTRIM(RTRIM(COALESCE(BANKNAME, ''))) AS bank_name,
                LfdNr AS lfdnr
            FROM XXAKunBank
            WHERE LTRIM(RTRIM(CAST(KundenNr AS VARCHAR(50)))) = ?
            ORDER BY ISNULL(LfdNr, 999999), IBAN, SWIFT
        """
        return self.fetch_all(query, (kundennr,)) or []
