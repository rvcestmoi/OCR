# db/transporter_repository.py

from db.repository import BaseRepository


class TransporterRepository(BaseRepository):

    def find_transporter_by_bank(self, iban: str, bic: str):
        query = """
            SELECT 
                bank.IBAN,
                bank.SWIFT,
                bank.BankName,
                kun.name1,
                kun.Strasse,
                kun.Ort,
                kun.LKZ,
                bank.KundenNr
            FROM xxakunbank bank
            LEFT JOIN xxakun kun 
                ON kun.KundenNr = bank.KundenNr
            WHERE REPLACE(UPPER(bank.IBAN), ' ', '') = REPLACE(UPPER(?), ' ', '')
            AND REPLACE(UPPER(bank.SWIFT), ' ', '') = REPLACE(UPPER(?), ' ', '')
        """


        result = self.fetch_one(query, (iban, bic))       


        return result

    def search_transporters_by_name(self, name_part: str):
        query = """
            SELECT TOP 10 kundennr, name1
            FROM xxakun
            WHERE UPPER(name1) LIKE UPPER(?)
            ORDER BY name1
        """
        return self.fetch_all(query, (f"%{name_part}%",))
    
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





