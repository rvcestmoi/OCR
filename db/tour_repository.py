# db/tour_repository.py
from db.repository import BaseRepository
from typing import Dict, List, Optional, Union, Any

class TourRepository(BaseRepository):
    def __init__(self, connection):
        super().__init__(connection)

    def find_by_tournr(self, tour_nr: str):
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return None

        query = """
            SELECT CONVERT(VARCHAR(20), TourNr) AS TourNr
            FROM xxatour
            WHERE CONVERT(VARCHAR(20), TourNr) = ?
        """
        return self.fetch_one(query, (tour_nr,))

    def get_kosten_by_tournr(self, tour_nr: str) -> Optional[float]:
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return None

        query = """
            SELECT TOP 1 Kosten
            FROM xxatour
            WHERE CONVERT(VARCHAR(20), TourNr) = ?
        """
        row = self.fetch_one(query, (tour_nr,))
        if not row:
            return None

        v = row.get("Kosten") if "Kosten" in row else row.get("kosten")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    def get_kosten_by_tournrs(self, tour_numbers: List[str]) -> Dict[str, Optional[float]]:
        tour_numbers = [str(t).strip() for t in tour_numbers if t and str(t).strip()]
        if not tour_numbers:
            return {}

        placeholders = ",".join(["?"] * len(tour_numbers))
        query = f"""
            SELECT CONVERT(VARCHAR(20), TourNr) AS TourNr, Kosten
            FROM xxatour
            WHERE CONVERT(VARCHAR(20), TourNr) IN ({placeholders})
        """
        rows = self.fetch_all(query, tuple(tour_numbers))

        out: Dict[str, Optional[float]] = {}
        for r in rows:
            k = str(r.get("TourNr") or r.get("tournr") or "").strip()
            v = r.get("Kosten") if "Kosten" in r else r.get("kosten")
            try:
                out[k] = float(v) if v is not None else None
            except Exception:
                out[k] = None
        return out
    
    def get_palette_details_by_tournr(self, tour_nr: str) -> List[Dict[str, Any]]:
        """
        Retourne les infos palettes par TourNr :
        TourNr, VPE, SUM(VPEAnz) as sumVPE
        """
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return []

        query = """
            SELECT
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) AS Dossier,
                pos.VPE,
                SUM(pos.VPEAnz) AS Palettes,
				Sum(pos.TatsGew) as Poids
            FROM XXAV_FR_MainAufIntNrByLegs leg
            LEFT JOIN xxaslauf auf ON auf.AufIntNr = leg.leg_AufIntNr
            LEFT JOIN xxaaufpos pos ON pos.aufintnr = leg.MAin_aufintnr
            WHERE pos.VPE IS NOT NULL and
			LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) = ?
            GROUP BY
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))),
                pos.VPE
            ORDER BY pos.VPE
        """
        return self.fetch_all(query, (tour_nr,))