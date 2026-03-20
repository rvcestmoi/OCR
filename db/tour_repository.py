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

        q3 = f"""
            UPDATE XXASLAufInfSym
            SET InfoSymbol18 = ?
            WHERE AufIntNr IN ({sub})
        """

        with self._connection.connect() as conn:
            cur = conn.cursor()
            cur.execute(q1, (value, tour_nr))
            cur.execute(q2, (value, tour_nr))
            conn.commit()

        sql = """
            UPDATE sym
            SET sym.InfoSymbol4 = ?
            FROM XXATourInfSym sym
            LEFT JOIN XXATour tour ON tour.TourIntNr = sym.TourIntNr
            WHERE tour.TourNr = ?;
        """
        self.execute(sql, (value, tour_nr))


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
        """
        Retourne les lignes palettes/poids + trajet CMR + AufNr pour une liste de TourNr.

        Important :
        - le trajet doit être récupéré au niveau COMMANDE (XXASLAuf), pas au niveau tournée
        - le champ Trajet renvoyé est donc propre à chaque AufNr
        - en secours, on retombe sur les champs de tournée si les champs commande sont vides
        """
        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]
        if not tour_numbers:
            return []

        placeholders = ",".join(["?"] * len(tour_numbers))
        query = f"""
            SELECT
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) AS Dossier,
                LTRIM(RTRIM(CAST(auf.AufNr AS VARCHAR(50)))) AS AufNr,
                pos.VPE,
                SUM(pos.VPEAnz) AS Palettes,
                SUM(pos.TatsGew) AS Poids,
                CONCAT(
                    COALESCE(NULLIF(MAX(LTRIM(RTRIM(CAST(auf.BELLKZ AS VARCHAR(20))))), ''), NULLIF(MAX(LTRIM(RTRIM(CAST(tour.BELLKZ AS VARCHAR(20))))), ''), ''),
                    '-',
                    COALESCE(NULLIF(MAX(LTRIM(RTRIM(CAST(auf.BelPLZ AS VARCHAR(20))))), ''), NULLIF(MAX(LTRIM(RTRIM(CAST(tour.BelPLZ AS VARCHAR(20))))), ''), ''),
                    ' ',
                    COALESCE(NULLIF(MAX(LTRIM(RTRIM(CAST(auf.BelOrt AS VARCHAR(100))))), ''), NULLIF(MAX(LTRIM(RTRIM(CAST(tour.BelOrt AS VARCHAR(100))))), ''), ''),
                    '-',
                    COALESCE(NULLIF(MAX(LTRIM(RTRIM(CAST(auf.EmgLKZ AS VARCHAR(20))))), ''), NULLIF(MAX(LTRIM(RTRIM(CAST(tour.EmgLKZ AS VARCHAR(20))))), ''), ''),
                    '-',
                    COALESCE(NULLIF(MAX(LTRIM(RTRIM(CAST(auf.EmgPLZ AS VARCHAR(20))))), ''), NULLIF(MAX(LTRIM(RTRIM(CAST(tour.EmgPLZ AS VARCHAR(20))))), ''), ''),
                    ' ',
                    COALESCE(NULLIF(MAX(LTRIM(RTRIM(CAST(auf.EmgOrt AS VARCHAR(100))))), ''), NULLIF(MAX(LTRIM(RTRIM(CAST(tour.EmgOrt AS VARCHAR(100))))), ''), '')
                ) AS Trajet
            FROM XXAV_FR_MainAufIntNrByLegs leg
            LEFT JOIN xxaslauf auf ON auf.AufIntNr = leg.leg_AufIntNr
            LEFT JOIN xxaaufpos pos ON pos.aufintnr = leg.MAin_aufintnr
            LEFT JOIN XXATour tour ON tour.TourNr = auf.TourNr
            WHERE pos.VPE IS NOT NULL
              AND LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) IN ({placeholders})
            GROUP BY
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))),
                LTRIM(RTRIM(CAST(auf.AufNr AS VARCHAR(50)))),
                pos.VPE
            ORDER BY
                LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))),
                LTRIM(RTRIM(CAST(auf.AufNr AS VARCHAR(50)))),
                pos.VPE
        """
        return self.fetch_all(query, tuple(tour_numbers))
    
    def set_block_status_for_tournr(self, tour_nr: str, is_blocked: bool, motif: str = ""):
        """
        Met à jour XXATourExt.isBloqued + MotifBlocage pour un TourNr.
        Upsert si la ligne XXATourExt n'existe pas encore.
        """
        sql = """
            DECLARE @TourIntNr INT;
            SELECT TOP 1 @TourIntNr = TourIntNr
            FROM XXATour
            WHERE TourNr = ?;

            IF @TourIntNr IS NULL
                RETURN;

            IF EXISTS (SELECT 1 FROM XXATourExt WHERE TourIntNr = @TourIntNr)
            BEGIN
                UPDATE XXATourExt
                SET
                    isBloqued = ?,
                    MotifBlocage = ?
                WHERE TourIntNr = @TourIntNr;
            END
            ELSE
            BEGIN
                INSERT INTO XXATourExt (TourIntNr, isBloqued, MotifBlocage)
                VALUES (@TourIntNr, ?, ?);
            END
        """
        # si pas bloqué -> motif NULL (plus propre)
        motif_db = (motif or "").strip() if is_blocked else None
        self.execute(sql, (tour_nr, 1 if is_blocked else 0, motif_db, 1 if is_blocked else 0, motif_db))

    def get_tournrs_matching_ffnr(self, tournrs: List[str], kundennr: str) -> Set[str]:
        tournrs = [str(t).strip() for t in (tournrs or []) if str(t).strip()]
        kundennr = str(kundennr or "").strip()

        if not tournrs or not kundennr:
            return set()

        placeholders = ",".join(["?"] * len(tournrs))
        query = f"""
            SELECT LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) AS TourNr
            FROM xxatour
            WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) IN ({placeholders})
            AND LTRIM(RTRIM(CAST(FFNR AS VARCHAR(50)))) = ?
        """
        rows = self.fetch_all(query, tuple(tournrs) + (kundennr,))
        return {
            str(r.get("TourNr") or r.get("tournr") or "").strip()
            for r in rows
            if str(r.get("TourNr") or r.get("tournr") or "").strip()
        }


    def get_ffnr_for_tour(self, tournr: str) -> str:
        tournr = str(tournr or "").strip()
        if not tournr:
            return ""

        query = """
            SELECT TOP 1 LTRIM(RTRIM(CAST(FFNR AS VARCHAR(50)))) AS FFNR
            FROM xxatour
            WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) = ?
        """
        row = self.fetch_one(query, (tournr,))
        return str((row or {}).get("FFNR") or "").strip()
    

    def get_aufintnr_by_aufnr(self, aufnr: str) -> str:
        aufnr = str(aufnr or "").strip()
        if not aufnr:
            return ""

        query = """
            SELECT TOP 1 LTRIM(RTRIM(CAST(aufintnr AS VARCHAR(50)))) AS aufintnr
            FROM xxaslauf
            WHERE LTRIM(RTRIM(CAST(aufnr AS VARCHAR(50)))) = ?
        """
        row = self.fetch_one(query, (aufnr,))
        return str((row or {}).get("aufintnr") or "").strip()

    def get_aufnrs_with_cmr_in_ged(self, aufnrs: list[str]) -> set[str]:
        aufnrs = [str(a).strip() for a in (aufnrs or []) if str(a).strip()]
        if not aufnrs:
            return set()

        out: set[str] = set()
        chunk_size = 200

        for i in range(0, len(aufnrs), chunk_size):
            chunk = aufnrs[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))

            query = f"""
                SELECT DISTINCT LTRIM(RTRIM(CAST(sw.SWort AS VARCHAR(100)))) AS aufnr
                FROM XXAArcDoc doc
                LEFT JOIN XXAArcSW sw ON doc.ArcDocINr = sw.ArcDocINr
                WHERE doc.Archiv = 'CMR'
                AND sw.ArcSBINr = 1
                AND LTRIM(RTRIM(CAST(sw.SWort AS VARCHAR(100)))) IN ({placeholders})
            """

            rows = self.fetch_all(query, tuple(chunk)) or []
            for r in rows:
                aufnr = str(r.get("aufnr") or "").strip()
                if aufnr:
                    out.add(aufnr)

        return out

    def has_infosymbol19_311_for_tournr(self, tour_nr: str) -> bool:
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return False

        query = """
            SELECT TOP 1 1
            FROM XXASLAufInfSym sym
            LEFT JOIN XXASLAuf auf ON auf.AufIntNr = sym.AufIntNr
            WHERE sym.InfoSymbol19 = 311
            AND LTRIM(RTRIM(CAST(auf.TourNr AS VARCHAR(20)))) = ?
        """
        row = self.fetch_one(query, (tour_nr,))
        return bool(row)

    def has_europal_for_tournr(self, tour_nr: str) -> bool:
        tour_nr = (tour_nr or "").strip()
        if not tour_nr:
            return False

        query = """
            SELECT TOP 1 1
            FROM xxav_LIS_SUMTOUR_228794
            WHERE VPE = 'EUROPAL'
            AND LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) = ?
        """
        row = self.fetch_one(query, (tour_nr,))
        return bool(row)