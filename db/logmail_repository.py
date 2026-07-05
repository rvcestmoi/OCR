# db/logmail_repository.py

from db.repository import BaseRepository
from typing import Dict, List
import re


class LogmailRepository(BaseRepository):
    """
    Accès à la table XXA_LOGMAIL_228794
    """

    def get_latest_mail_info(self) -> dict:
        """
        Récupère les infos du dernier mail affichées dans le volet de gauche.

        On prend la dernière ligne logmail (TOP 1 ORDER BY id_log DESC),
        avec un fallback sur date_creation si date_mail est NULL.
        """
        query = """
            SELECT TOP 1
                date_mail,
                date_creation,
                LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet
            FROM dbo.XXA_LOGMAIL_228794
            ORDER BY id_log DESC
        """
        row = self.fetch_one(query) or {}
        return {
            "date_mail": row.get("date_mail") or row.get("date_creation"),
            "expediteur": row.get("expediteur") or "",
            "sujet": row.get("sujet") or "",
        }

    def get_latest_mail_date(self):
        """
        Récupère la dernière date_mail connue dans XXA_LOGMAIL_228794.
        """
        query = """
            SELECT TOP 1 date_mail
            FROM XXA_LOGMAIL_228794
            ORDER BY id_log DESC
        """
        row = self.fetch_one(query)
        return row["date_mail"] if row else None

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

    def get_mail_info_for_entry_id(self, entry_id: str = "", nom_pdf: str = "") -> dict:
        """Retourne les informations mail du groupe/document courant.

        Utilisé pour pousser dans XXATourExt les champs OCRExpediteur et
        OCRObjet en même temps que le motif de blocage. On privilégie la ligne
        du document affiché quand `nom_pdf` est fourni, puis on retombe sur la
        première ligne du même entry_id.
        """
        entry_id = str(entry_id or "").strip()
        nom_pdf = str(nom_pdf or "").strip()

        if entry_id:
            row = self.fetch_one(
                """
                SELECT TOP 1
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) AS date_mail,
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                    LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id = ?
                ORDER BY
                    CASE WHEN ? <> '' AND LTRIM(RTRIM(COALESCE(nom_pdf, ''))) = ? THEN 0 ELSE 1 END,
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) ASC,
                    id_log ASC
                """,
                (entry_id, nom_pdf, nom_pdf),
            )
        elif nom_pdf:
            row = self.fetch_one(
                """
                SELECT TOP 1
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) AS date_mail,
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                    LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet
                FROM dbo.XXA_LOGMAIL_228794
                WHERE LTRIM(RTRIM(COALESCE(nom_pdf, ''))) = ?
                ORDER BY COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) ASC, id_log ASC
                """,
                (nom_pdf,),
            )
        else:
            row = None

        row = row or {}
        return {
            "date_mail": row.get("date_mail"),
            "expediteur": str(row.get("expediteur") or "").strip(),
            "sujet": str(row.get("sujet") or "").strip(),
        }

    def get_files_for_entry(self, entry_id: str):
        query = """
            SELECT nom_pdf
            FROM XXA_LOGMAIL_228794
            WHERE entry_id = ?
              AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
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
        return self._normalize_processing_status(status)


    def _normalize_processing_status(self, status: str | None, *, default: str = "pending") -> str:
        st = str(status or "").strip().lower()
        if st == "eccarts":
            return "ecart"
        if st in {"aux_vide", "aux_vides", "auxvide", "auxvides", "aux-empty", "aux_empty"}:
            return "aux_empty"
        if st in {"pending", "validated", "error", "ecart"}:
            return st
        return default


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
                    out[entry_id] = self._normalize_processing_status(status)

        return out


    def set_processing_status_for_entry(self, entry_id: str, status: str) -> None:
        entry_id = str(entry_id or "").strip()
        status = str(status or "").strip().lower()
        status = self._normalize_processing_status(status, default="")

        if not entry_id:
            return
        if status not in {"pending", "validated", "error", "ecart", "aux_empty"}:
            raise ValueError(f"Statut invalide: {status}")

        query = """
            UPDATE dbo.XXA_LOGMAIL_228794
            SET processing_status = ?
            WHERE entry_id = ?
        """
        self.execute(query, (status, entry_id))


    def set_doc_type_for_file(self, nom_pdf: str, doc_type: str | None) -> None:
        nom_pdf = str(nom_pdf or "").strip()
        normalized_doc_type = str(doc_type or "").strip()

        if not nom_pdf:
            return

        query = """
            UPDATE dbo.XXA_LOGMAIL_228794
            SET doc_type = ?
            WHERE nom_pdf = ?
        """
        self.execute(query, (normalized_doc_type or None, nom_pdf))

    def set_doc_type_for_entry(self, entry_id: str, doc_type: str | None) -> None:
        """Met à jour le type de tous les documents rattachés au même entry_id."""
        entry_id = str(entry_id or "").strip()
        normalized_doc_type = str(doc_type or "").strip()

        if not entry_id:
            return

        query = """
            UPDATE dbo.XXA_LOGMAIL_228794
            SET doc_type = ?
            WHERE entry_id = ?
        """
        self.execute(query, (normalized_doc_type or None, entry_id))

    def _normalize_logmail_status_for_sql(self, status: str | None) -> str | None:
        """Statuts autorisés dans XXA_LOGMAIL_228794.processing_status.

        Important : "draft" est un statut JSON/local, pas un statut SQL valide
        chez le client. Pour une sauvegarde simple, on ne touche donc pas au
        processing_status SQL.
        """
        if status is None:
            return None
        st = str(status or "").strip().lower()
        if not st or st == "draft":
            return None
        st = self._normalize_processing_status(st, default="")
        if st in {"pending", "validated", "error", "ecart", "aux_empty"}:
            return st
        return None

    def _normalize_search_index_status(self, status: str | None) -> str:
        """Statut stocké dans l'index de recherche.

        Ici "draft" est volontairement ramené à "pending" : une sauvegarde
        brouillon doit rester visible/recherchable dans le pool des en attente.
        """
        st = str(status or "").strip().lower()
        if st in {"", "draft"}:
            return "pending"
        st = self._normalize_processing_status(st, default="")
        if st in {"pending", "validated", "error", "ecart", "aux_empty"}:
            return st
        return "pending"

    def ensure_search_index_table(self) -> None:
        """Crée la table d'index de recherche si elle n'existe pas.

        La méthode est appelée avant l'upsert. Si l'utilisateur SQL n'a pas les
        droits DDL, l'exception remonte : c'est préférable à un échec silencieux.
        """
        if getattr(self, "_search_index_table_checked", False):
            return

        create_sql = """
        IF OBJECT_ID('dbo.XXA_OCR_SEARCH_INDEX', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.XXA_OCR_SEARCH_INDEX (
                entry_id varchar(200) NOT NULL CONSTRAINT PK_XXA_OCR_SEARCH_INDEX PRIMARY KEY,
                nom_pdf nvarchar(500) NULL,
                processing_status varchar(30) NOT NULL CONSTRAINT DF_XXA_OCR_SEARCH_INDEX_status DEFAULT('pending'),
                invoice_number nvarchar(100) NULL,
                invoice_date nvarchar(50) NULL,
                iban nvarchar(80) NULL,
                bic nvarchar(50) NULL,
                tour_numbers nvarchar(max) NULL,
                transporter_kundennr nvarchar(100) NULL,
                transporter_name nvarchar(255) NULL,
                date_mail datetime2 NULL,
                expediteur nvarchar(255) NULL,
                search_text nvarchar(max) NULL,
                search_compact nvarchar(max) NULL,
                updated_at datetime2 NOT NULL CONSTRAINT DF_XXA_OCR_SEARCH_INDEX_updated DEFAULT(SYSDATETIME())
            );
            CREATE INDEX IX_XXA_OCR_SEARCH_INDEX_status_updated
                ON dbo.XXA_OCR_SEARCH_INDEX(processing_status, updated_at DESC);
            CREATE INDEX IX_XXA_OCR_SEARCH_INDEX_date_mail
                ON dbo.XXA_OCR_SEARCH_INDEX(date_mail DESC);
        END
        ELSE
        BEGIN
            IF COL_LENGTH('dbo.XXA_OCR_SEARCH_INDEX', 'transporter_name') IS NULL
                ALTER TABLE dbo.XXA_OCR_SEARCH_INDEX ADD transporter_name nvarchar(255) NULL;
            IF COL_LENGTH('dbo.XXA_OCR_SEARCH_INDEX', 'date_mail') IS NULL
                ALTER TABLE dbo.XXA_OCR_SEARCH_INDEX ADD date_mail datetime2 NULL;
            IF COL_LENGTH('dbo.XXA_OCR_SEARCH_INDEX', 'expediteur') IS NULL
                ALTER TABLE dbo.XXA_OCR_SEARCH_INDEX ADD expediteur nvarchar(255) NULL;
            IF NOT EXISTS (
                SELECT 1
                FROM sys.indexes
                WHERE name = 'IX_XXA_OCR_SEARCH_INDEX_date_mail'
                  AND object_id = OBJECT_ID('dbo.XXA_OCR_SEARCH_INDEX')
            )
                CREATE INDEX IX_XXA_OCR_SEARCH_INDEX_date_mail
                    ON dbo.XXA_OCR_SEARCH_INDEX(date_mail DESC);
        END
        """
        self.execute(create_sql)
        self._search_index_table_checked = True

    def get_search_index_mail_metadata(self, entry_id: str, nom_pdf: str = "") -> dict:
        """Récupère date_mail/expéditeur depuis XXA_LOGMAIL_228794 pour l'index.

        On privilégie la ligne du document si `nom_pdf` est fourni, sinon la
        première ligne du groupe `entry_id`. Le fallback date_creation permet
        d'avoir une date exploitable quand date_mail est NULL.
        """
        entry_id = str(entry_id or "").strip()
        nom_pdf = str(nom_pdf or "").strip()
        if not entry_id:
            return {"date_mail": None, "expediteur": ""}

        if nom_pdf:
            row = self.fetch_one(
                """
                SELECT TOP 1
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) AS date_mail,
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id = ?
                ORDER BY
                    CASE WHEN LTRIM(RTRIM(COALESCE(nom_pdf, ''))) = ? THEN 0 ELSE 1 END,
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) ASC,
                    id_log ASC
                """,
                (entry_id, nom_pdf),
            )
        else:
            row = self.fetch_one(
                """
                SELECT TOP 1
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) AS date_mail,
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur
                FROM dbo.XXA_LOGMAIL_228794
                WHERE entry_id = ?
                ORDER BY COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) ASC, id_log ASC
                """,
                (entry_id,),
            )

        return {
            "date_mail": (row or {}).get("date_mail"),
            "expediteur": str((row or {}).get("expediteur") or "").strip(),
        }

    @staticmethod
    def _date_for_search_text(value) -> str:
        """Transforme une date SQL/Python en texte cherchable, avec variantes."""
        if not value:
            return ""
        try:
            # datetime/date Python
            iso = value.strftime("%Y-%m-%d %H:%M:%S")
            fr = value.strftime("%d/%m/%Y %H:%M")
            return f"{iso} {fr}"
        except Exception:
            return str(value or "").strip()

    def upsert_search_index(
        self,
        *,
        entry_id: str,
        nom_pdf: str = "",
        status: str | None = None,
        invoice_number: str = "",
        invoice_date: str = "",
        iban: str = "",
        bic: str = "",
        tour_numbers=None,
        transporter_kundennr: str = "",
        transporter_name: str = "",
        date_mail=None,
        expediteur: str = "",
        verbose: bool = True,
    ) -> str:
        """Alimente dbo.XXA_OCR_SEARCH_INDEX pour rendre la recherche rapide.

        Retourne "updated", "inserted" ou "skipped".
        """
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            if verbose:
                print("DEBUG SEARCH INDEX: skipped, entry_id vide")
            return "skipped"

        self.ensure_search_index_table()

        if tour_numbers is None:
            tours = []
        elif isinstance(tour_numbers, str):
            tours = [tour_numbers]
        else:
            tours = list(tour_numbers or [])

        clean_tours = []
        for t in tours:
            t = str(t or "").strip()
            if t and t not in clean_tours:
                clean_tours.append(t)

        status_idx = self._normalize_search_index_status(status)
        nom_pdf = str(nom_pdf or "").strip()
        invoice_number = str(invoice_number or "").strip()
        invoice_date = str(invoice_date or "").strip()
        iban = str(iban or "").strip()
        bic = str(bic or "").strip()
        tour_text = " ".join(clean_tours)
        transporter_kundennr = str(transporter_kundennr or "").strip()
        transporter_name = str(transporter_name or "").strip()
        expediteur = str(expediteur or "").strip()

        # Date mail / expéditeur viennent de XXA_LOGMAIL_228794. On les récupère
        # ici si l'appelant ne les a pas fournis, pour éviter de dépendre du JSON.
        if not date_mail or not expediteur:
            try:
                meta = self.get_search_index_mail_metadata(entry_id, nom_pdf)
                if not date_mail:
                    date_mail = meta.get("date_mail")
                if not expediteur:
                    expediteur = str(meta.get("expediteur") or "").strip()
            except Exception as e:
                print(f"⚠️ SEARCH INDEX: impossible de récupérer date_mail/expéditeur pour {entry_id}: {e}")

        date_mail_search = self._date_for_search_text(date_mail)

        search_parts = [
            entry_id,
            nom_pdf,
            invoice_number,
            invoice_date,
            iban,
            bic,
            tour_text,
            transporter_kundennr,
            transporter_name,
            date_mail_search,
            expediteur,
        ]
        search_text = " | ".join([p for p in search_parts if p])
        search_compact = re.sub(r"[^0-9A-Z]+", "", search_text.upper())

        update_sql = """
            UPDATE dbo.XXA_OCR_SEARCH_INDEX
            SET nom_pdf = ?,
                processing_status = ?,
                invoice_number = ?,
                invoice_date = ?,
                iban = ?,
                bic = ?,
                tour_numbers = ?,
                transporter_kundennr = ?,
                transporter_name = ?,
                date_mail = ?,
                expediteur = ?,
                search_text = ?,
                search_compact = ?,
                updated_at = SYSDATETIME()
            WHERE entry_id = ?
        """
        params_update = (
            nom_pdf,
            status_idx,
            invoice_number,
            invoice_date,
            iban,
            bic,
            tour_text,
            transporter_kundennr,
            transporter_name,
            date_mail,
            expediteur,
            search_text,
            search_compact,
            entry_id,
        )
        count = self.execute_rowcount(update_sql, params_update)
        if count and count > 0:
            if verbose:
                print(f"DEBUG SEARCH INDEX: updated entry_id={entry_id} tours={tour_text}")
            return "updated"

        exists = self.fetch_one(
            "SELECT COUNT(1) AS c FROM dbo.XXA_OCR_SEARCH_INDEX WHERE entry_id = ?",
            (entry_id,),
        )
        if int((exists or {}).get("c") or 0) > 0:
            # Cas rare : rowcount non fiable côté driver, mais ligne présente.
            if verbose:
                print(f"DEBUG SEARCH INDEX: updated/exists entry_id={entry_id} tours={tour_text}")
            return "updated"

        insert_sql = """
            INSERT INTO dbo.XXA_OCR_SEARCH_INDEX (
                entry_id,
                nom_pdf,
                processing_status,
                invoice_number,
                invoice_date,
                iban,
                bic,
                tour_numbers,
                transporter_kundennr,
                transporter_name,
                date_mail,
                expediteur,
                search_text,
                search_compact,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, SYSDATETIME())
        """
        params_insert = (
            entry_id,
            nom_pdf,
            status_idx,
            invoice_number,
            invoice_date,
            iban,
            bic,
            tour_text,
            transporter_kundennr,
            transporter_name,
            date_mail,
            expediteur,
            search_text,
            search_compact,
        )
        self.execute(insert_sql, params_insert)
        if verbose:
            print(f"DEBUG SEARCH INDEX: inserted entry_id={entry_id} tours={tour_text}")
        return "inserted"

    def clear_search_index(self) -> int:
        """Vide entièrement l'index de recherche avant une reconstruction complète."""
        self.ensure_search_index_table()
        return self.execute_rowcount("DELETE FROM dbo.XXA_OCR_SEARCH_INDEX")

    def get_all_search_index_source_rows(self) -> list[dict]:
        """Source complète pour reconstruire XXA_OCR_SEARCH_INDEX.

        Retourne une ligne représentative par entry_id depuis XXA_LOGMAIL_228794,
        hors documents marqués DELETED. Les champs JSON viendront enrichir ces
        lignes côté UI quand un fichier de sauvegarde existe.
        """
        query = """
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
                    COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) AS date_mail,
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                    LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet,
                    ROW_NUMBER() OVER (
                        PARTITION BY entry_id
                        ORDER BY
                            COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation) ASC,
                            id_log ASC
                    ) AS rn
                FROM dbo.XXA_LOGMAIL_228794
                WHERE LTRIM(RTRIM(COALESCE(entry_id, ''))) <> ''
                  AND LTRIM(RTRIM(COALESCE(nom_pdf, ''))) <> ''
                  AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
            )
            SELECT
                entry_id,
                nom_pdf,
                processing_status,
                invoice_date,
                iban,
                bic,
                doc_type,
                date_creation,
                date_mail,
                expediteur,
                sujet
            FROM base
            WHERE rn = 1
            ORDER BY date_mail ASC, nom_pdf ASC
        """
        return self.fetch_all(query) or []

    def search_entry_ids_in_index(
        self,
        search_query: str,
        status: str | None = None,
        limit: int = 500,
        date_mail_from=None,
        date_mail_to=None,
    ) -> list[str]:
        """Recherche rapide dans XXA_OCR_SEARCH_INDEX.

        Si la table n'existe pas encore, retourne simplement [] pour garder la
        compatibilité avec les installations non migrées.
        """
        q = str(search_query or "").strip()
        if not q:
            return []

        try:
            exists = self.fetch_one("SELECT OBJECT_ID('dbo.XXA_OCR_SEARCH_INDEX', 'U') AS object_id")
            if not (exists or {}).get("object_id"):
                return []
        except Exception:
            return []

        status_idx = self._normalize_search_index_status(status)
        like_text = f"%{q.upper()}%"
        compact_q = re.sub(r"[^0-9A-Z]+", "", q.upper())
        like_compact = f"%{compact_q}%" if compact_q else like_text
        try:
            top = max(1, min(int(limit or 500), 2000))
        except Exception:
            top = 500

        params = [status_idx, like_text, like_compact]
        date_filter_sql = ""
        if date_mail_from:
            date_filter_sql += " AND date_mail >= ?"
            params.append(date_mail_from)
        if date_mail_to:
            date_filter_sql += " AND date_mail < ?"
            params.append(date_mail_to)

        sql = f"""
            SELECT TOP {top} entry_id
            FROM dbo.XXA_OCR_SEARCH_INDEX
            WHERE processing_status = ?
              AND (
                    UPPER(COALESCE(search_text, '')) LIKE ?
                    OR UPPER(COALESCE(search_compact, '')) LIKE ?
                  )
              {date_filter_sql}
            ORDER BY updated_at DESC
        """
        rows = self.fetch_all(sql, tuple(params)) or []
        out = []
        for r in rows:
            entry = str(r.get("entry_id") or "").strip()
            if entry and entry not in out:
                out.append(entry)
        return out

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

        sql_status = self._normalize_logmail_status_for_sql(status)
        if sql_status is not None:
            set_parts.append("processing_status = ?")
            params.append(sql_status)

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


    def get_document_rows_for_folder(
        self,
        folder_path: str,
        status: str,
        limit: int | None = None,
        search_query: str | None = None,
        date_mail_from=None,
        date_mail_to=None,
    ) -> list[dict]:
        """
        Retourne les lignes groupées par entry_id pour alimenter le tableau de gauche.
        Le disque sert ensuite uniquement à vérifier que le fichier existe.

        Pour la vue "pending", le tri se fait sur date_mail ASC.
        Fallback sur date_creation si date_mail est NULL ou non convertible.
        Les autres vues gardent le tri historique sur date_creation ASC.

        Si `search_query` est renseigné, la recherche s'applique côté SQL sur
        l'ensemble des lignes du statut demandé (et pas seulement sur les lignes
        déjà limitées/chargées dans l'UI).
        """
        status = str(status or "pending").strip().lower()
        status = self._normalize_processing_status(status)
        if status not in {"pending", "validated", "error", "ecart", "aux_empty"}:
            status = "pending"

        normalized_search = str(search_query or "").strip()

        top_clause = ""
        if limit is not None and int(limit) > 0 and not normalized_search:
            top_clause = f"TOP {int(limit)}"

        sort_expr = "COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation)" if status == "pending" else "date_creation"

        params: list[str] = [status]
        search_filter_sql = ""
        if normalized_search:
            like_value = f"%{normalized_search.upper()}%"
            params.extend([like_value, like_value, like_value, like_value, like_value, like_value, like_value])
            search_filter_sql = """
                AND (
                    UPPER(COALESCE(nom_pdf, '')) LIKE ?
                    OR UPPER(COALESCE(CONVERT(varchar(50), invoice_date), '')) LIKE ?
                    OR UPPER(COALESCE(iban, '')) LIKE ?
                    OR UPPER(COALESCE(bic, '')) LIKE ?
                    OR UPPER(COALESCE(CONVERT(varchar(50), date_mail, 120), '')) LIKE ?
                    OR UPPER(COALESCE(expediteur, '')) LIKE ?
                    OR UPPER(COALESCE(sujet, '')) LIKE ?
                )
            """

        mail_date_expr = "COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation)"
        date_filter_sql = ""
        if date_mail_from:
            date_filter_sql += f" AND {mail_date_expr} >= ?"
            params.append(date_mail_from)
        if date_mail_to:
            date_filter_sql += f" AND {mail_date_expr} < ?"
            params.append(date_mail_to)

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
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                    LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet,
                    ROW_NUMBER() OVER (
                        PARTITION BY entry_id
                        ORDER BY {sort_expr} ASC,
                                 id_log ASC
                    ) AS rn
                FROM dbo.XXA_LOGMAIL_228794
                WHERE LTRIM(RTRIM(COALESCE(nom_pdf, ''))) <> ''
                AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
                AND CASE
                        WHEN LTRIM(RTRIM(COALESCE(processing_status, 'pending'))) = 'eccarts' THEN 'ecart'
                        ELSE LTRIM(RTRIM(COALESCE(processing_status, 'pending')))
                    END = ?
                {search_filter_sql}
                {date_filter_sql}
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
                date_mail,
                expediteur,
                sujet
            FROM base
            WHERE rn = 1
            ORDER BY {sort_expr} ASC,
                     nom_pdf ASC
        """
        return self.fetch_all(query, tuple(params)) or []


    def get_document_rows_for_folder_page(
        self,
        folder_path: str,
        status: str,
        *,
        offset: int = 0,
        fetch: int = 300,
        search_query: str | None = None,
        date_mail_from=None,
        date_mail_to=None,
    ) -> list[dict]:
        """Retourne une page de lignes représentatives pour le volet gauche.

        Cette version évite de charger des milliers de groupes en mémoire alors
        que l'UI n'en affichera que `max_pages_*`. Elle conserve la même logique
        que get_document_rows_for_folder(), mais ajoute une pagination SQL Server
        sur les groupes déjà dédoublonnés par entry_id.
        """
        status = str(status or "pending").strip().lower()
        status = self._normalize_processing_status(status)
        if status not in {"pending", "validated", "error", "ecart", "aux_empty"}:
            status = "pending"

        try:
            offset = max(0, int(offset or 0))
        except Exception:
            offset = 0
        try:
            fetch = max(1, int(fetch or 300))
        except Exception:
            fetch = 300

        normalized_search = str(search_query or "").strip()
        sort_expr = "COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation)" if status == "pending" else "date_creation"

        params: list = [status]
        search_filter_sql = ""
        if normalized_search:
            like_value = f"%{normalized_search.upper()}%"
            params.extend([like_value, like_value, like_value, like_value, like_value, like_value, like_value])
            search_filter_sql = """
                AND (
                    UPPER(COALESCE(nom_pdf, '')) LIKE ?
                    OR UPPER(COALESCE(CONVERT(varchar(50), invoice_date), '')) LIKE ?
                    OR UPPER(COALESCE(iban, '')) LIKE ?
                    OR UPPER(COALESCE(bic, '')) LIKE ?
                    OR UPPER(COALESCE(CONVERT(varchar(50), date_mail, 120), '')) LIKE ?
                    OR UPPER(COALESCE(expediteur, '')) LIKE ?
                    OR UPPER(COALESCE(sujet, '')) LIKE ?
                )
            """

        mail_date_expr = "COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation)"
        date_filter_sql = ""
        if date_mail_from:
            date_filter_sql += f" AND {mail_date_expr} >= ?"
            params.append(date_mail_from)
        if date_mail_to:
            date_filter_sql += f" AND {mail_date_expr} < ?"
            params.append(date_mail_to)

        params.extend([offset, fetch])

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
                    LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                    LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet,
                    ROW_NUMBER() OVER (
                        PARTITION BY entry_id
                        ORDER BY {sort_expr} ASC,
                                 id_log ASC
                    ) AS rn
                FROM dbo.XXA_LOGMAIL_228794
                WHERE LTRIM(RTRIM(COALESCE(nom_pdf, ''))) <> ''
                AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
                AND CASE
                        WHEN LTRIM(RTRIM(COALESCE(processing_status, 'pending'))) = 'eccarts' THEN 'ecart'
                        ELSE LTRIM(RTRIM(COALESCE(processing_status, 'pending')))
                    END = ?
                {search_filter_sql}
                {date_filter_sql}
            ), reps AS (
                SELECT
                    entry_id,
                    nom_pdf,
                    processing_status,
                    invoice_date,
                    iban,
                    bic,
                    doc_type,
                    date_creation,
                    date_mail,
                    expediteur,
                    sujet,
                    ROW_NUMBER() OVER (ORDER BY {sort_expr} ASC, nom_pdf ASC) AS page_rn
                FROM base
                WHERE rn = 1
            )
            SELECT
                entry_id,
                nom_pdf,
                processing_status,
                invoice_date,
                iban,
                bic,
                doc_type,
                date_creation,
                date_mail,
                expediteur,
                sujet
            FROM reps
            WHERE page_rn > ?
              AND page_rn <= (? + ?)
            ORDER BY page_rn ASC
        """
        # Le dernier paramètre ? est fetch, SQL Server calcule offset + fetch.
        params = params[:-2] + [offset, offset, fetch]
        return self.fetch_all(query, tuple(params)) or []


    def get_search_index_rows_for_entries(self, entry_ids: list[str]) -> dict[str, dict]:
        """Retourne les infos déjà indexées pour éviter de relire les JSON.

        Clé : entry_id. Si la table d'index n'existe pas encore, retourne {}.
        """
        out: dict[str, dict] = {}
        clean_ids = [str(e or "").strip() for e in (entry_ids or []) if str(e or "").strip()]
        if not clean_ids:
            return out

        try:
            exists = self.fetch_one("SELECT OBJECT_ID('dbo.XXA_OCR_SEARCH_INDEX', 'U') AS object_id")
            if not (exists or {}).get("object_id"):
                return out
        except Exception:
            return out

        chunk_size = 200
        for i in range(0, len(clean_ids), chunk_size):
            chunk = clean_ids[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            query = f"""
                SELECT
                    entry_id,
                    tour_numbers,
                    transporter_kundennr,
                    transporter_name,
                    date_mail,
                    expediteur
                FROM dbo.XXA_OCR_SEARCH_INDEX
                WHERE entry_id IN ({placeholders})
            """
            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                entry_id = str(r.get("entry_id") or "").strip()
                if entry_id:
                    out[entry_id] = r
        return out
    

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
              AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
            ORDER BY date_creation ASC, id_log ASC
        """
        return self.fetch_all(query, (entry_id,)) or []
    

    def get_document_rows_for_entries(
        self,
        entry_ids: list[str],
        status: str | None = None,
        date_mail_from=None,
        date_mail_to=None,
    ) -> list[dict]:
        """
        Retourne les lignes représentatives (1 ligne par entry_id) pour une liste
        d'entry_id donnée. Utile pour compléter la recherche de gauche avec des
        résultats trouvés hors du pool actuellement chargé.
        """
        clean_ids = [str(e or "").strip() for e in (entry_ids or []) if str(e or "").strip()]
        if not clean_ids:
            return []

        normalized_status = str(status or "").strip().lower()
        normalized_status = self._normalize_processing_status(normalized_status, default="")
        if normalized_status and normalized_status not in {"pending", "validated", "error", "ecart", "aux_empty"}:
            normalized_status = ""

        rows_out: list[dict] = []
        chunk_size = 200

        for i in range(0, len(clean_ids), chunk_size):
            chunk = clean_ids[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            params: list[str] = list(chunk)
            status_sql = ""
            if normalized_status:
                status_sql = """
                    AND CASE
                            WHEN LTRIM(RTRIM(COALESCE(processing_status, 'pending'))) = 'eccarts' THEN 'ecart'
                            ELSE LTRIM(RTRIM(COALESCE(processing_status, 'pending')))
                        END = ?
                """
                params.append(normalized_status)

            mail_date_expr = "COALESCE(TRY_CONVERT(datetime2, date_mail), date_creation)"
            date_filter_sql = ""
            if date_mail_from:
                date_filter_sql += f" AND {mail_date_expr} >= ?"
                params.append(date_mail_from)
            if date_mail_to:
                date_filter_sql += f" AND {mail_date_expr} < ?"
                params.append(date_mail_to)

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
                        LTRIM(RTRIM(COALESCE(expediteur, ''))) AS expediteur,
                        LTRIM(RTRIM(COALESCE(sujet, ''))) AS sujet,
                        ROW_NUMBER() OVER (
                            PARTITION BY entry_id
                            ORDER BY date_creation ASC, id_log ASC
                        ) AS rn
                    FROM dbo.XXA_LOGMAIL_228794
                    WHERE entry_id IN ({placeholders})
                      AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
                    {status_sql}
                    {date_filter_sql}
                )
                SELECT
                    entry_id,
                    nom_pdf,
                    processing_status,
                    invoice_date,
                    iban,
                    bic,
                    doc_type,
                    date_creation,
                    date_mail,
                    expediteur,
                    sujet
                FROM base
                WHERE rn = 1
                ORDER BY date_creation ASC, nom_pdf ASC
            """
            rows_out.extend(self.fetch_all(query, tuple(params)) or [])

        return rows_out


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
                  AND UPPER(LTRIM(RTRIM(COALESCE(doc_type, '')))) <> 'DELETED'
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
            normalized_status = self._normalize_processing_status(status, default="")
            if normalized_status:
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
