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
