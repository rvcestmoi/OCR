
from typing import Any, Dict, Optional
from db.repository import BaseRepository


class XXAReRepository(BaseRepository):
    """
    Accès table XXARe pour vérifier l'existence d'une facture déjà enregistrée.
    Règle: NrBuch = invoice_number, AufDK = 'D', FakAdr = kundennr transporteur
    """

    def __init__(self, connection):
        super().__init__(connection)

    def find_existing_invoice(self, nr_buch: str, fak_adr: str, aufdk: str = "D") -> Optional[Dict[str, Any]]:
        nr_buch = (nr_buch or "").strip()
        fak_adr = (fak_adr or "").strip()
        aufdk = (aufdk or "D").strip()

        if not nr_buch or not fak_adr:
            return None

        sql = """
        SELECT TOP 1 *
        FROM XXARe
        WHERE NrBuch = ?
          AND AufDK = ?
          AND FakAdr = ?
        """
        # fetch_one existe déjà dans tes repos (LogmailRepository l'utilise)
        row = self.fetch_one(sql, (nr_buch, aufdk, fak_adr))
        return row or None

    def invoice_exists(self, nr_buch: str, fak_adr: str, aufdk: str = "D") -> bool:
        return self.find_existing_invoice(nr_buch, fak_adr, aufdk=aufdk) is not None