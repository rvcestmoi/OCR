# db/tour_repository.py
from db.repository import BaseRepository
from typing import Dict, List

class TourRepository(BaseRepository):
    def __init__(self, connection):
        super().__init__(connection)

    def find_by_tournr(self, tour_nr: str):
        query = """
            SELECT TourNr
            FROM xxatour
            WHERE TourNr = ?
        """
        return self.fetch_one(query, (tour_nr,))
    
    def get_kosten_by_tournr(self, tour_numbers: List[str]) -> Dict[str, float]:
        """
        Retourne un dict {TourNr: Kosten} en une seule requête.
        """
        tour_numbers = [t.strip() for t in tour_numbers if t and t.strip()]
        if not tour_numbers:
            return {}

        placeholders = ",".join(["?"] * len(tour_numbers))
        query = f"""
            SELECT TourNr, Kosten
            FROM xxatour
            WHERE TourNr IN ({placeholders})
        """
        rows = self.fetch_all(query, tuple(tour_numbers))

        out: Dict[str, float] = {}
        for r in rows:
            # Selon ton fetch_all, les clés peuvent être 'TourNr' ou 'tournr'
            k = str(r.get("TourNr") or r.get("tournr") or "").strip()
            v = r.get("Kosten") if "Kosten" in r else r.get("kosten")
            try:
                out[k] = float(v) if v is not None else None
            except Exception:
                out[k] = None
        return out
