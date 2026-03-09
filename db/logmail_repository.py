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

    def get_processing_users_for_entries(self, entry_ids: list[str]) -> dict[str, str]:
        if not entry_ids:
            return {}

        out: dict[str, str] = {}
        chunk_size = 200

        for i in range(0, len(entry_ids), chunk_size):
            chunk = [e for e in entry_ids[i:i + chunk_size] if e]
            if not chunk:
                continue

            placeholders = ",".join(["?"] * len(chunk))
            query = f"""
                SELECT entry_id, MAX(LTRIM(RTRIM(COALESCE(processing_user, '')))) AS processing_user
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id IN ({placeholders})
                GROUP BY entry_id
            """
            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                entry_id = str(r.get("entry_id") or "").strip()
                user = str(r.get("processing_user") or "").strip()
                if entry_id:
                    out[entry_id] = user

        return out


    def claim_entry_for_user(self, entry_id: str, username: str) -> bool:
        entry_id = str(entry_id or "").strip()
        username = str(username or "").strip()

        if not entry_id or not username:
            print(f"[CLAIM] paramètres invalides: entry_id={entry_id!r}, username={username!r}")
            return False

        with self._connection.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT @@SERVERNAME AS server_name, DB_NAME() AS db_name")
            row = cursor.fetchone()
            print(f"[CLAIM] server={row[0]!r}, db={row[1]!r}, entry_id={entry_id!r}, username={username!r}")

            cursor.execute(
                """
                SELECT COUNT(*) 
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id = ?
                """,
                (entry_id,)
            )
            before_count = cursor.fetchone()[0]
            print(f"[CLAIM] nb lignes trouvées pour entry_id avant update = {before_count}")

            cursor.execute(
                """
                UPDATE dbo.XXA_LOGMAIL_228794
                SET processing_user = NULL,
                    processing_since = NULL
                WHERE LTRIM(RTRIM(COALESCE(processing_user, ''))) = ?
                AND entry_id <> ?
                """,
                (username, entry_id),
            )
            print(f"[CLAIM] release autres lignes user -> rowcount={cursor.rowcount}")

            cursor.execute(
                """
                UPDATE dbo.XXA_LOGMAIL_228794
                SET processing_user = ?,
                    processing_since = SYSDATETIME()
                WHERE entry_id = ?
                AND (
                        processing_user IS NULL
                        OR LTRIM(RTRIM(COALESCE(processing_user, ''))) = ''
                        OR LTRIM(RTRIM(COALESCE(processing_user, ''))) = ?
                )
                """,
                (username, entry_id, username),
            )
            print(f"[CLAIM] claim ligne -> rowcount={cursor.rowcount}")

            conn.commit()

            cursor.execute(
                """
                SELECT TOP 5 entry_id, processing_user, processing_since
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id = ?
                """,
                (entry_id,)
            )
            rows = cursor.fetchall()
            print("[CLAIM] état après commit:")
            for r in rows:
                print("   ", tuple(r))

            return cursor.rowcount > 0



    def release_entry_for_user(self, entry_id: str, username: str) -> bool:
        query = """
            UPDATE dbo.XXA_LOGMAIL_228794
            SET processing_user = NULL,
                processing_since = NULL
            WHERE entry_id = ?
            AND LTRIM(RTRIM(COALESCE(processing_user, ''))) = ?
        """

        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (entry_id, username))
            conn.commit()
            return cursor.rowcount > 0

    def get_processing_user_for_entry(self, entry_id: str) -> str:
        query = """
            SELECT TOP 1 LTRIM(RTRIM(COALESCE(processing_user, ''))) AS processing_user
            FROM dbo.XXA_LOGMAIL_228794
            WHERE entry_id = ?
        """
        row = self.fetch_one(query, (entry_id,))
        return str((row or {}).get("processing_user") or "").strip()
    
    def release_all_entries_for_user(self, username: str) -> int:
        username = str(username or "").strip()
        if not username:
            return 0

        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE dbo.XXA_LOGMAIL_228794
                SET processing_user = NULL,
                    processing_since = NULL
                WHERE LTRIM(RTRIM(COALESCE(processing_user, ''))) = ?
                """,
                (username,),
            )
            count = cursor.rowcount
            conn.commit()
            return count
        
    def get_entry_creation_order_map(self, entry_ids: list[str]) -> dict[str, str]:
        """
        Retourne un map {entry_id: first_date_iso} pour trier les groupes
        selon XXA_LOGMAIL_228794 (plus ancien d'abord).
        """
        if not entry_ids:
            return {}

        out: dict[str, str] = {}
        chunk_size = 200

        for i in range(0, len(entry_ids), chunk_size):
            chunk = [e for e in entry_ids[i:i + chunk_size] if e and not str(e).startswith("__NO_ENTRY__")]
            if not chunk:
                continue

            placeholders = ",".join(["?"] * len(chunk))
            query = f"""
                SELECT
                    entry_id,
                    MIN(date_creation) AS first_date
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id IN ({placeholders})
                GROUP BY entry_id
            """
            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                entry_id = str(r.get("entry_id") or "").strip()
                first_date = r.get("first_date")
                if entry_id:
                    out[entry_id] = str(first_date or "")

        return out