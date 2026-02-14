# db/logmail_repository.py

from db.repository import BaseRepository


class LogmailRepository(BaseRepository):
    """
    Accès à la table XXA_LOGMAIL_228794
    """

    def get_entry_id_for_file(self, nom_pdf: str):
        query = """
            SELECT entry_id
            FROM XXA_LOGMAIL_228794
            WHERE nom_pdf = ?
        """
        row = self.fetch_one(query, (nom_pdf,))
        return row["entry_id"] if row else None

    def get_files_for_entry(self, entry_id: str):
        query = """
            SELECT nom_pdf
            FROM XXA_LOGMAIL_228794
            WHERE entry_id = ?
            ORDER BY nom_pdf
        """
        return self.fetch_all(query, (entry_id,))
