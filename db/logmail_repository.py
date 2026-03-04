# db/logmail_repository.py

from db.repository import BaseRepository
from typing import Dict, List


class LogmailRepository(BaseRepository):
    """
    Accès à la table XXA_LOGMAIL_228794
    """

    def get_entry_id_for_file(self, nom_pdf: str):
        query = """
            SELECT TOP 1 entry_id
            FROM XXA_LOGMAIL_228794
            WHERE nom_pdf = ?
            ORDER BY date_creation DESC, id_log DESC
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

    def update_entry_for_file(self, nom_pdf: str, entry_id: str) -> None:
        query = """
            UPDATE XXA_LOGMAIL_228794
            SET entry_id = ?
            WHERE nom_pdf = ?
        """
        self.execute(query, (entry_id, nom_pdf))


    def get_entry_ids_for_files(self, filenames: List[str]) -> Dict[str, str]:
        if not filenames:
            return {}

        out: Dict[str, str] = {}
        chunk_size = 200

        for i in range(0, len(filenames), chunk_size):
            chunk = [f for f in filenames[i:i + chunk_size] if f]
            if not chunk:
                continue

            placeholders = ",".join(["?"] * len(chunk))
            query = f"""
                WITH x AS (
                    SELECT
                        nom_pdf,
                        entry_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY nom_pdf
                            ORDER BY date_creation DESC, id_log DESC
                        ) AS rn
                    FROM XXA_LOGMAIL_228794
                    WHERE nom_pdf IN ({placeholders})
                )
                SELECT nom_pdf, entry_id
                FROM x
                WHERE rn = 1
            """
            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                n = str(r.get("nom_pdf") or "").strip()
                e = str(r.get("entry_id") or "").strip()
                if n and e:
                    out[n] = e

        return out

    def set_entry_id_for_file(self, nom_pdf: str, new_entry_id: str):
        """
        Regroupe un fichier dans un autre entry_id (ne touche pas message_id).
        Si le fichier n'existe pas en base, on l'insère en MANUAL.
        """
        sql = """
            UPDATE dbo.XXA_LOGMAIL_228794
            SET entry_id = ?
            WHERE nom_pdf = ?;

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO dbo.XXA_LOGMAIL_228794 (date_creation, message_id, entry_id, nom_pdf, sujet, expediteur)
                VALUES (SYSDATETIME(), CONCAT('MANUAL-', CONVERT(varchar(36), NEWID())), ?, ?, '', '')
            END
        """
        self.execute(sql, (new_entry_id, nom_pdf, new_entry_id, nom_pdf))