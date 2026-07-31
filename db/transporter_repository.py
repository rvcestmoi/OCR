# db/transporter_repository.py

from db.repository import BaseRepository


class TransporterRepository(BaseRepository):

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

    def find_transporter_by_bank(self, iban: str, bic: str):
        iban_norm = self._normalize_bank_value(iban)
        bic_norm = self._normalize_bank_value(bic)
        bic8 = bic_norm[:8]

        query = """
            WITH bank_match AS (
                SELECT
                    bank.IBAN,
                    bank.SWIFT,
                    bank.BankName,
                    kun.name1,
                    kun.Strasse,
                    kun.Ort,
                    kun.LKZ,
                    kun.PLZ,
                    kun.UstId,
                    bank.KundenNr,
                    REPLACE(REPLACE(REPLACE(UPPER(COALESCE(bank.IBAN, '')), ' ', ''), '-', ''), CHAR(160), '') AS iban_norm,
                    REPLACE(REPLACE(REPLACE(UPPER(COALESCE(bank.SWIFT, '')), ' ', ''), '-', ''), CHAR(160), '') AS swift_norm
                FROM xxakunbank bank
                LEFT JOIN xxakun kun
                    ON kun.KundenNr = bank.KundenNr
            )
            SELECT TOP 1
                IBAN,
                SWIFT,
                BankName,
                name1,
                Strasse,
                Ort,
                LKZ,
                PLZ,
                UstId,
                KundenNr
            FROM bank_match
            WHERE iban_norm = ?
              AND (
                    swift_norm = ?
                    OR LEFT(swift_norm, 8) = ?
                  )
            ORDER BY
                CASE
                    WHEN swift_norm = ? THEN 0
                    WHEN LEN(swift_norm) = 11 AND LEFT(swift_norm, 8) = ? THEN 1
                    WHEN LEFT(swift_norm, 8) = ? THEN 2
                    ELSE 9
                END,
                LEN(swift_norm),
                KundenNr
        """

        result = self.fetch_one(query, (iban_norm, bic_norm, bic8, bic_norm, bic8, bic8))
        return result

    def search_transporters_by_name(self, name_part: str):
        name_part = (name_part or "").strip()
        if not name_part:
            return []

        query = """
            SELECT TOP 10
                kundennr,
                name1
            FROM xxakun
            WHERE
                GsDruck = 'J'
                AND (
                    UPPER(name1) LIKE UPPER(?)
                    OR UPPER(CAST(kundennr AS VARCHAR(50))) LIKE UPPER(?)
                )
            ORDER BY name1
        """

        like = f"%{name_part}%"
        return self.fetch_all(query, (like, like))
    
    def get_bank_by_kundennr(self, kundennr: str):
        query = """
            SELECT IBAN, SWIFT
            FROM xxakunbank
            WHERE KundenNr = ?
        """
        return self.fetch_one(query, (kundennr,))


    def _same_bank_pair(self, iban_a: str, bic_a: str, iban_b: str, bic_b: str) -> bool:
        ia = self._normalize_bank_value(iban_a)
        ib = self._normalize_bank_value(iban_b)
        if not ia or ia != ib:
            return False

        ba = self._normalize_bank_value(bic_a)
        bb = self._normalize_bank_value(bic_b)
        if not ba or not bb:
            return ba == bb

        # Le BIC peut être enregistré en 8 ou 11 caractères selon les fiches.
        return ba == bb or ba[:8] == bb[:8]

    def update_bank(self, kundennr: str, iban: str, bic: str) -> dict:
        """Met à jour/insère l'IBAN-BIC principal du transporteur.

        La méthode précédente lançait l'UPDATE/INSERT sans contrôler qu'une ligne
        avait réellement été touchée. Ici on :
          1. normalise les valeurs OCR ;
          2. met à jour la ligne banque principale si elle existe ;
          3. insère une ligne LfdNr=1 si aucune banque n'existe ;
          4. relit SQL Server pour confirmer la présence effective de l'IBAN/BIC.
        """
        kundennr = str(kundennr or "").strip()
        iban = self._normalize_bank_value(iban)
        bic = self._normalize_bank_value(bic)

        if not kundennr:
            raise ValueError("KundenNr vide : mise à jour banque impossible.")
        if not iban or not bic:
            raise ValueError("IBAN/BIC vides : mise à jour banque impossible.")

        existing_pair = self.fetch_one(
            """
            SELECT TOP 1 IBAN, SWIFT, LfdNr
            FROM xxakunbank
            WHERE KundenNr = ?
              AND REPLACE(REPLACE(REPLACE(UPPER(COALESCE(IBAN, '')), ' ', ''), '-', ''), CHAR(160), '') = ?
              AND (
                    REPLACE(REPLACE(REPLACE(UPPER(COALESCE(SWIFT, '')), ' ', ''), '-', ''), CHAR(160), '') = ?
                    OR LEFT(REPLACE(REPLACE(REPLACE(UPPER(COALESCE(SWIFT, '')), ' ', ''), '-', ''), CHAR(160), ''), 8) = ?
                  )
            ORDER BY ISNULL(LfdNr, 999999)
            """,
            (kundennr, iban, bic, bic[:8]),
        )
        if existing_pair:
            return {
                "action": "already_exists",
                "rows_affected": 0,
                "iban": str(existing_pair.get("IBAN") or "").strip(),
                "bic": str(existing_pair.get("SWIFT") or "").strip(),
                "lfdnr": existing_pair.get("LfdNr"),
            }

        main_bank = self.fetch_one(
            """
            SELECT TOP 1 IBAN, SWIFT, LfdNr
            FROM xxakunbank
            WHERE KundenNr = ?
            ORDER BY ISNULL(LfdNr, 999999)
            """,
            (kundennr,),
        )

        action = "inserted"
        rows_affected = 0

        if main_bank:
            lfdnr = main_bank.get("LfdNr")
            if lfdnr is None:
                rows_affected = self.execute_rowcount(
                    """
                    UPDATE xxakunbank
                    SET IBAN = ?, SWIFT = ?, IsDefault = 1
                    WHERE KundenNr = ?
                      AND LfdNr IS NULL
                    """,
                    (iban, bic, kundennr),
                )
            else:
                rows_affected = self.execute_rowcount(
                    """
                    UPDATE xxakunbank
                    SET IBAN = ?, SWIFT = ?, IsDefault = 1
                    WHERE KundenNr = ?
                      AND LfdNr = ?
                    """,
                    (iban, bic, kundennr, lfdnr),
                )
            action = "updated"
        else:
            rows_affected = self.execute_rowcount(
                """
                INSERT INTO xxakunbank (KundenNr, IBAN, SWIFT, LfdNr, IsDefault)
                VALUES (?, ?, ?, 1, 1)
                """,
                (kundennr, iban, bic),
            )

        if rows_affected <= 0:
            raise RuntimeError(
                f"Aucune ligne XXAKunBank modifiée pour le transporteur {kundennr}."
            )

        saved = self.fetch_one(
            """
            SELECT TOP 1 IBAN, SWIFT, LfdNr
            FROM xxakunbank
            WHERE KundenNr = ?
            ORDER BY ISNULL(LfdNr, 999999)
            """,
            (kundennr,),
        )

        if not saved or not self._same_bank_pair(iban, bic, saved.get("IBAN"), saved.get("SWIFT")):
            raise RuntimeError(
                "La mise à jour SQL a été exécutée, mais la relecture XXAKunBank "
                "ne retrouve pas l'IBAN/BIC attendu."
            )

        return {
            "action": action,
            "rows_affected": rows_affected,
            "iban": str(saved.get("IBAN") or "").strip(),
            "bic": str(saved.get("SWIFT") or "").strip(),
            "lfdnr": saved.get("LfdNr"),
        }



    def find_transporter_by_kundennr(self, kundennr: str):
        query = """
            SELECT TOP 1
                bank.IBAN,
                bank.SWIFT,
                bank.BankName,
                kun.name1,
                kun.Strasse,
                KUN.PLZ,
                kun.Ort,
                kun.LKZ,
                kun.UstId,
                kun.KundenNr
            FROM xxakun kun
            LEFT JOIN xxakunbank bank
                ON bank.KundenNr = kun.KundenNr
            WHERE kun.KundenNr = ?
            ORDER BY bank.LfdNr
        """
        return self.fetch_one(query, (kundennr,))

    def get_ustid_by_kundennr(self, kundennr: str):
        query = """
            SELECT UstId
            FROM XXAKun
            WHERE KundenNr = ?
        """
        return self.fetch_one(query, (kundennr,))

    def get_ktoKreA_by_kundennr(self, kundennr: str):
        query = """
            SELECT KtoKreA
            FROM XXAKun
            WHERE KundenNr = ?
        """
        return self.fetch_one(query, (kundennr,))


    def update_ktoKreA(self, kundennr: str, konto_aux: str):
        """Met à jour le compte auxiliaire transporteur.

        Règle métier ED-TRANS :
        - KtoKreA reçoit le compte auxiliaire saisi ;
        - KtoKre reçoit le KundenNr du transporteur.
        """
        query = """
            UPDATE XXAKun
            SET KtoKreA = ?, KtoKre = ?
            WHERE KundenNr = ?
        """
        try:
            return self.execute_rowcount(query, (konto_aux, kundennr, kundennr))
        except AttributeError:
            self.execute(query, (konto_aux, kundennr, kundennr))
            return 0


    def get_lkz_by_kundennr(self, kundennr: str) -> str:
        query = """
            SELECT LKZ
            FROM XXAKun
            WHERE KundenNr = ?
        """
        row = self.fetch_one(query, (kundennr,))
        if not row:
            return ""
        return str(row.get("LKZ") or "").strip()
