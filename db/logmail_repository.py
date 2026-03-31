# db/logmail_repository.py

from db.repository import BaseRepository
from typing import Dict, List


class LogmailRepository(BaseRepository):
    """
    Accès à la table XXA_LOGMAIL_228794
    """

    def get_sender_for_entry_id(self, entry_id: str) -> str | None:
        """
        Récupère l'expéditeur pour un entry_id donné.
        Prend le premier enregistrement trouvé (par date_creation DESC).
        """
        query = """
            SELECT TOP 1 expediteur
            FROM XXA_LOGMAIL_228794
            WHERE entry_id = ?
            ORDER BY date_creation DESC, id_log DESC
        """
        row = self.fetch_one(query, (entry_id,))
        return row["expediteur"] if row else None

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
            return False

        with self._connection.connect() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT @@SERVERNAME AS server_name, DB_NAME() AS db_name")
            row = cursor.fetchone()

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id = ?
                """,
                (entry_id,)
            )
            before_count = cursor.fetchone()[0]
            if int(before_count or 0) <= 0:
                return False

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
            released_rows = int(cursor.rowcount or 0)

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
            claimed_rows = int(cursor.rowcount or 0)

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
            for r in rows:
                print("   ", tuple(r))

            if claimed_rows > 0:
                return True

            owner = ""
            for r in rows:
                try:
                    owner = str(r[1] or "").strip()
                except Exception:
                    owner = ""
                if owner:
                    break

            return owner == username



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


    def get_logmail_rows_for_folder(self, folder_path: str) -> list[dict]:
        """
        Comme XXA_LOGMAIL_228794 ne stocke pas le chemin dossier,
        on charge les lignes logmail récentes puis on filtre côté Python
        avec les fichiers réellement présents dans le dossier.
        """
        query = """
            SELECT
                entry_id,
                nom_pdf,
                processing_user,
                processing_since,
                date_creation,
                message_id,
                store_id
            FROM dbo.XXA_LOGMAIL_228794
            WHERE LTRIM(RTRIM(COALESCE(nom_pdf, ''))) <> ''
            ORDER BY date_creation ASC, nom_pdf ASC
        """
        return self.fetch_all(query) or []
    
    def clone_logmail_row_for_split_file(self, source_nom_pdf: str, new_nom_pdf: str, entry_id: str | None = None):
        """
        Clone la ligne logmail du PDF source vers un nouveau nom de PDF scindé.
        On garde message_id / sujet / expediteur / date_mail / store_id.
        """
        source_nom_pdf = str(source_nom_pdf or "").strip()
        new_nom_pdf = str(new_nom_pdf or "").strip()
        entry_id = str(entry_id or "").strip()

        if not source_nom_pdf or not new_nom_pdf:
            return

        sql = """
            ;WITH src AS (
                SELECT TOP 1
                    message_id,
                    entry_id,
                    sujet,
                    expediteur,
                    date_mail,
                    store_id
                FROM dbo.XXA_LOGMAIL_228794
                WHERE nom_pdf = ?
                ORDER BY date_creation DESC, id_log DESC
            )
            UPDATE dbo.XXA_LOGMAIL_228794
            SET entry_id = COALESCE(NULLIF(?, ''), entry_id)
            WHERE nom_pdf = ?;

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO dbo.XXA_LOGMAIL_228794
                (
                    date_creation,
                    message_id,
                    entry_id,
                    nom_pdf,
                    sujet,
                    expediteur,
                    processing_user,
                    processing_since,
                    date_mail,
                    store_id
                )
                SELECT
                    SYSDATETIME(),
                    src.message_id,
                    COALESCE(NULLIF(?, ''), src.entry_id),
                    ?,
                    src.sujet,
                    src.expediteur,
                    NULL,
                    NULL,
                    src.date_mail,
                    src.store_id
                FROM src
            END
        """
        self.execute(sql, (source_nom_pdf, entry_id, new_nom_pdf, entry_id, new_nom_pdf))


    def get_processing_status_for_entry(self, entry_id: str) -> str:
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return "pending"

        query = """
            SELECT TOP 1 LTRIM(RTRIM(COALESCE(processing_status, 'pending'))) AS processing_status
            FROM dbo.XXA_LOGMAIL_228794
            WHERE entry_id = ?
        """
        row = self.fetch_one(query, (entry_id,))
        status = str((row or {}).get("processing_status") or "pending").strip().lower()
        return "ecart" if status == "eccarts" else status


    def get_processing_status_map_for_entries(self, entry_ids: list[str]) -> dict[str, str]:
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
                SELECT entry_id, MAX(LTRIM(RTRIM(COALESCE(processing_status, 'pending')))) AS processing_status
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id IN ({placeholders})
                GROUP BY entry_id
            """
            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                entry_id = str(r.get("entry_id") or "").strip()
                status = str(r.get("processing_status") or "pending").strip().lower()
                if entry_id:
                    out[entry_id] = ("ecart" if status == "eccarts" else status) or "pending"

        return out


    def set_processing_status_for_entry(self, entry_id: str, status: str) -> None:
        entry_id = str(entry_id or "").strip()
        status = str(status or "").strip().lower()
        if status == "eccarts":
            status = "ecart"

        if not entry_id:
            return
        if status not in {"pending", "validated", "error", "ecart"}:
            raise ValueError(f"Statut invalide: {status}")

        query = """
            UPDATE dbo.XXA_LOGMAIL_228794
            SET processing_status = ?
            WHERE entry_id = ?
        """
        self.execute(query, (status, entry_id))

    def update_document_by_filename(self, nom_pdf: str, *, entry_id: str = "", invoice_date: str = "", iban: str = "", bic: str = "", status: str | None = None) -> str:
        """Met à jour un document par nom de fichier, en créant une entrée si nécessaire.

        Retourne l'entry_id finalement utilisé.
        """
        from uuid import uuid4

        nom_pdf = str(nom_pdf or "").strip()
        if not nom_pdf:
            return ""

        existing_entry_id = str(self.get_entry_id_for_file(nom_pdf) or "").strip()

        # Si on n'a pas d'entry_id courant, générer et définir via set_entry_id_for_file
        if not existing_entry_id:
            new_entry_id = str(entry_id or "").strip() or f"MANUAL-{uuid4()}"
            self.set_entry_id_for_file(nom_pdf, new_entry_id)
            existing_entry_id = new_entry_id

        # Si on fournit un entry_id et qu'il diffère, on override
        if entry_id and entry_id.strip() and entry_id.strip() != existing_entry_id:
            self.set_entry_id_for_file(nom_pdf, entry_id.strip())
            existing_entry_id = entry_id.strip()

        final_entry_id = existing_entry_id
        if not final_entry_id:
            return ""

        set_parts = []
        params = []

        if invoice_date:
            set_parts.append("invoice_date = ?")
            params.append(str(invoice_date).strip())
        if iban:
            set_parts.append("iban = ?")
            params.append(str(iban).strip())
        if bic:
            set_parts.append("bic = ?")
            params.append(str(bic).strip())
        if status is not None:
            normalized_status = str(status or "").strip().lower()
            if normalized_status == "eccarts":
                normalized_status = "ecart"
            set_parts.append("processing_status = ?")
            params.append(normalized_status)

        if not set_parts:
            return final_entry_id

        params.append(final_entry_id)
        params.append(nom_pdf)
        query = f"""
            UPDATE dbo.XXA_LOGMAIL_228794
            SET {", ".join(set_parts)}
            WHERE entry_id = ?
              AND nom_pdf = ?
        """
        print(f"DEBUG DBACTION: update_document_by_filename query={query.strip()} params={params}")
        self.execute(query, tuple(params))
        return final_entry_id


    def get_document_rows_for_folder(self, folder_path: str, status: str, limit: int | None = None) -> list[dict]:
        """
        Retourne les lignes groupées par entry_id pour alimenter le tableau de gauche.
        Le disque sert ensuite uniquement à vérifier que le fichier existe.

        Pour la vue "pending", le tri se fait sur date_mail ASC.
        Fallback sur date_creation si date_mail est NULL ou non convertible.
        Les autres vues gardent le tri historique sur date_creation ASC.
        """
        status = str(status or "pending").strip().lower()
        if status not in {"pending", "validated", "error", "ecart"}:
            status = "pending"

        top_clause = ""
        if limit is not None and int(limit) > 0:
            top_clause = f"TOP {int(limit)}"

        sort_expr = "COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation)" if status == "pending" else "date_creation"

        query = f"""
            ;WITH base AS (
                SELECT
                    entry_id,
                    nom_pdf,
                    CASE
                        WHEN LTRIM(RTRIM(COALESCE(processing_status, 'pending'))) = 'eccarts' THEN 'ecart'
                        ELSE LTRIM(RTRIM(COALESCE(processing_status, 'pending')))
                    END AS processing_status,
                    invoice_date,
                    iban,
                    bic,
                    doc_type,
                    date_creation,
                    date_mail,
                    ROW_NUMBER() OVER (
                        PARTITION BY entry_id
                        ORDER BY {sort_expr} ASC,
                                 id_log ASC
                    ) AS rn
                FROM dbo.XXA_LOGMAIL_228794
                WHERE LTRIM(RTRIM(COALESCE(nom_pdf, ''))) <> ''
                AND CASE
                        WHEN LTRIM(RTRIM(COALESCE(processing_status, 'pending'))) = 'eccarts' THEN 'ecart'
                        ELSE LTRIM(RTRIM(COALESCE(processing_status, 'pending')))
                    END = ?
            )
            SELECT {top_clause}
                entry_id,
                nom_pdf,
                processing_status,
                invoice_date,
                iban,
                bic,
                doc_type,
                date_creation,
                date_mail
            FROM base
            WHERE rn = 1
            ORDER BY {sort_expr} ASC,
                     nom_pdf ASC
        """
        return self.fetch_all(query, (status,)) or []
    

    def get_files_for_entry(self, entry_id: str) -> list[dict]:
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return []

        query = """
            SELECT
                nom_pdf,
                entry_id,
                processing_status,
                invoice_date,
                iban,
                bic,
                doc_type,
                date_creation
            FROM dbo.XXA_LOGMAIL_228794
            WHERE entry_id = ?
            ORDER BY date_creation ASC, id_log ASC
        """
        return self.fetch_all(query, (entry_id,)) or []
    

    def get_files_for_entries(self, entry_ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        clean_ids = [str(e or "").strip() for e in (entry_ids or []) if str(e or "").strip()]
        if not clean_ids:
            return out

        chunk_size = 200
        for i in range(0, len(clean_ids), chunk_size):
            chunk = clean_ids[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            query = f"""
                SELECT
                    nom_pdf,
                    entry_id,
                    processing_status,
                    invoice_date,
                    iban,
                    bic,
                    doc_type,
                    date_creation
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id IN ({placeholders})
                ORDER BY entry_id ASC, date_creation ASC, id_log ASC
            """
            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                entry_id = str(r.get("entry_id") or "").strip()
                if not entry_id:
                    continue
                out.setdefault(entry_id, []).append(r)

        return out


    def update_document_metadata_for_entry(self, entry_id: str, *, invoice_date: str = "", iban: str = "", bic: str = "", status: str | None = None):
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return

        params = [str(invoice_date or "").strip(), str(iban or "").strip(), str(bic or "").strip()]
        set_parts = [
            "invoice_date = ?",
            "iban = ?",
            "bic = ?",
        ]

        if status is not None:
            normalized_status = str(status or "").strip().lower()
            if normalized_status == "eccarts":
                normalized_status = "ecart"
            set_parts.append("processing_status = ?")
            params.append(normalized_status)

        params.append(entry_id)

        query = f"""
            UPDATE dbo.XXA_LOGMAIL_228794
            SET {", ".join(set_parts)}
            WHERE entry_id = ?
        """
        self.execute(query, tuple(params))

    def get_entry_id_for_file(self, nom_pdf: str):
        query = """
            SELECT TOP 1 entry_id
            FROM XXA_LOGMAIL_228794
            WHERE nom_pdf = ?
            ORDER BY date_creation DESC, id_log DESC
        """
        row = self.fetch_one(query, (nom_pdf,))
        return row["entry_id"] if row else None
