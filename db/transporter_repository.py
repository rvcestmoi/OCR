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


    def update_bank(self, kundennr: str, iban: str, bic: str):
        # Vérifier si ligne existe
        check_query = """
            SELECT COUNT(*) AS cnt
            FROM xxakunbank
            WHERE KundenNr = ?
        """

        result = self.fetch_one(check_query, (kundennr,))
        exists = result and result.get("cnt", 0) > 0

        if exists:
            query = """
                UPDATE xxakunbank
                SET IBAN = ?, SWIFT = ?, LfdNr = 1  
                WHERE KundenNr = ?
            """
            self.execute(query, (iban, bic, kundennr))
            print("UPDATE effectué")

        else:
            query = """
                INSERT INTO xxakunbank (KundenNr, IBAN, SWIFT, LfdNr)
                VALUES (?, ?, ?,1)
            """
            self.execute(query, (kundennr, iban, bic))
            print("INSERT effectué")



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
        query = """
            UPDATE XXAKun
            SET KtoKreA = ?, KtoKre = 4
            WHERE KundenNr = ?
        """
        self.execute(query, (konto_aux, kundennr))


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
