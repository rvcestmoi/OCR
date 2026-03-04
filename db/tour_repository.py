# db/tour_repository.py
from db.repository import BaseRepository
from typing import Dict, List, Optional, Union, Any,Set

class TourRepository(BaseRepository):
    def __init__(self, connection):
        super().__init__(connection)

    def find_by_tournr(self, tour_nr: str):
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return None

        query = """
            SELECT CONVERT(VARCHAR(20), TourNr) AS TourNr, TourIntnr
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
        
    def set_infosymbol18_for_tournr(self, tour_nr: str, value: int = 600) -> None:
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return

        sub = """
            SELECT AufIntNr
            FROM xxaslauf
            WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) = ?
        """

        q1 = f"""
            UPDATE XXAAufInfSym
            SET InfoSymbol18 = ?
            WHERE AufIntNr IN ({sub})
        """

        q2 = f"""
            UPDATE XXASLAufInfSym
            SET InfoSymbol18 = ?
            WHERE AufIntNr IN ({sub})
        """

        with self._connection.connect() as conn:
            cur = conn.cursor()
            cur.execute(q1, (value, tour_nr))
            cur.execute(q2, (value, tour_nr))
            conn.commit()


    def get_existing_tournrs_in_xxatour(self, tournrs: List[str]) -> Set[str]:
        tournrs = [str(t).strip() for t in (tournrs or []) if str(t).strip()]
        if not tournrs:
            return set()

        placeholders = ",".join(["?"] * len(tournrs))
        query = f"""
            SELECT LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) AS TourNr
            FROM xxatour
            WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) IN ({placeholders})
        """
        rows = self.fetch_all(query, tuple(tournrs))
        return {str(r.get("TourNr") or r.get("tournr") or "").strip() for r in rows}


    def get_theoretical_vat_percent_by_tournr(self, tour_nr: str) -> Optional[float]:
        """
        Retourne le taux de TVA théorique (en %) pour une tournée/dossier (TourNr).
        Source : XXAV_FR_UNION_XXAPreFakAuf_XXAFakAuf + XXAUC (champ Prozent).
        """
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return None

        query = """
            SELECT TOP 1 COALESCE(uc.Prozent, 0) AS Prozent
            FROM XXAV_FR_UNION_XXAPreFakAuf_XXAFakAuf auf
            LEFT JOIN XXAUC uc ON auf.FFUC = uc.UC
            WHERE auf.AufDK = 'K'
            AND LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) = ?
        """

        row = self.fetch_one(query, (tour_nr,))
        if not row:
            return None

        v = row.get("Prozent") if "Prozent" in row else row.get("prozent")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None
    
    def get_tour_extended_info(self, tour_nr: str) -> Optional[Dict[str, Any]]:
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return None

        query = """
        WITH allpos AS (
            SELECT
                SUM(pos.TatsGew) AS totalPoids,
                SUM(pos.LMAnz)   AS TotMpl,
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) AS TourNr
            FROM XXAV_FR_MainAufIntNrByLegs leg
            LEFT JOIN XXASLAuf auf ON auf.AufIntNr = leg.leg_AufIntNr
            LEFT JOIN xxaaufpos pos ON pos.AufIntNr = leg.MAIN_AufIntNr
            WHERE LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) = ?
            GROUP BY LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20))))
        )
        SELECT
            LTRIM(RTRIM(CAST(tour.TourNr AS VARCHAR(20)))) AS TourNr,
            tour.BelOrt AS Depart,
            tour.EmgOrt AS Arrivee,
            CONVERT(VARCHAR(10), tour.TourDatum, 103)   AS DateTour,
            CONVERT(VARCHAR(10), tour.TourEntDat, 103)  AS DateLivraison,
            COALESCE(pos.totalPoids, 0) AS Total_Poids,
            COALESCE(pos.TotMpl, 0)     AS Total_MPL
        FROM XXATour tour
        LEFT JOIN allpos pos
            ON pos.TourNr = LTRIM(RTRIM(CAST(tour.TourNr AS VARCHAR(20))))
        WHERE LTRIM(RTRIM(CAST(tour.TourNr AS VARCHAR(20)))) = ?
        """
        return self.fetch_one(query, (tour_nr, tour_nr))   


    def get_palette_details_with_trajet_by_tournrs(self, tour_numbers: List[str]) -> List[Dict[str, Any]]:
        """Retourne les lignes palettes/poids + trajet pour une liste de TourNr."""
        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]
        if not tour_numbers:
            return []

        placeholders = ",".join(["?"] * len(tour_numbers))
        query = f"""
            SELECT
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) AS Dossier,
                pos.VPE,
                SUM(pos.VPEAnz) AS Palettes,
                SUM(pos.TatsGew) AS Poids,
                CONCAT(
                    MAX(tour.BELLKZ), '-', MAX(tour.BelPLZ), ' ', MAX(tour.BelOrt),
                    '-',
                    MAX(tour.EmgLKZ), '-', MAX(tour.EmgPLZ), ' ', MAX(tour.EmgOrt)
                ) AS Trajet
            FROM XXAV_FR_MainAufIntNrByLegs leg
            LEFT JOIN xxaslauf auf ON auf.AufIntNr = leg.leg_AufIntNr
            LEFT JOIN xxaaufpos pos ON pos.aufintnr = leg.MAin_aufintnr
            LEFT JOIN XXATour tour ON tour.TourNr = auf.TourNr
            WHERE pos.VPE IS NOT NULL
            AND LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) IN ({placeholders})
            GROUP BY
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))),
                pos.VPE
            ORDER BY
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))),
                pos.VPE
        """
        return self.fetch_all(query, tuple(tour_numbers))
    