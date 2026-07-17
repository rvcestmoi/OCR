from __future__ import annotations

from db.repository import BaseRepository


class LISInvoiceRepository(BaseRepository):
    def row_exists(self, rech_nr: str, kunden_nr, tour_nr: str) -> bool:
        sql = """
            SELECT TOP 1 1 AS ok
            FROM dbo.LISINVOICE_EDTRANS
            WHERE RechNr = ?
              AND KundenNr = ?
              AND TourNr = ?
        """
        row = self.fetch_one(sql, (rech_nr, kunden_nr, tour_nr))
        return row is not None

    def upsert_invoice_row(
        self,
        *,
        rech_nr: str,
        rech_dat,
        ht,
        ttc,
        taux,
        kunden_nr,
        tour_nr: str,
        import_value: str = "NON",
    ) -> None:
        rech_nr = str(rech_nr or "").strip()
        tour_nr = str(tour_nr or "").strip()
        import_value = str(import_value or "NON").strip().upper() or "NON"

        if not rech_nr:
            raise ValueError("RechNr vide.")
        if not rech_dat:
            raise ValueError("RechDat vide.")
        if ht is None:
            raise ValueError("HT vide.")
        if ttc is None:
            raise ValueError("TTC vide.")
        if taux is None:
            raise ValueError("Taux vide.")
        if kunden_nr is None or str(kunden_nr).strip() == "":
            raise ValueError("KundenNr vide.")
        if not tour_nr:
            raise ValueError("TourNr vide.")

        if self.row_exists(rech_nr, kunden_nr, tour_nr):
            sql = """
                UPDATE dbo.LISINVOICE_EDTRANS
                SET RechDat = CAST(? AS date),
                    HT = CAST(? AS decimal(18,2)),
                    TTC = CAST(? AS decimal(18,2)),
                    Taux = CAST(? AS decimal(18,2)),
                    [Import] = ?
                WHERE RechNr = ?
                  AND KundenNr = ?
                  AND TourNr = ?
            """
            params = (
                rech_dat,
                ht,
                ttc,
                taux,
                import_value,
                rech_nr,
                kunden_nr,
                tour_nr,
            )
        else:
            sql = """
                INSERT INTO dbo.LISINVOICE_EDTRANS
                (
                    RechNr,
                    RechDat,
                    HT,
                    TTC,
                    Taux,
                    KundenNr,
                    TourNr,
                    [Import]
                )
                VALUES (
                    ?,
                    CAST(? AS date),
                    CAST(? AS decimal(18,2)),
                    CAST(? AS decimal(18,2)),
                    CAST(? AS decimal(18,2)),
                    ?,
                    ?,
                    ?
                )
            """
            params = (
                rech_nr,
                rech_dat,
                ht,
                ttc,
                taux,
                kunden_nr,
                tour_nr,
                import_value,
            )

        self.execute(sql, params)


    def tour_exists(self, tour_nr: str) -> bool:
        tour_nr = str(tour_nr or "").strip()
        if not tour_nr:
            return False

        sql = """
            SELECT TOP 1 TourNr
            FROM dbo.LISINVOICE_EDTRANS
            WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) = ?
        """
        row = self.fetch_one(sql, (tour_nr,))
        return row is not None


    def printed_shipment_exists(self, tour_nr: str, kunden_nr) -> bool:
        """Retourne si la tournée existe déjà côté factures imprimées WinSped.

        Contrôle demandé en plus de LISINVOICE_EDTRANS :
        XXAV_InvC_PrintedShipments où TourNr = dossier, FANr = KundenNr transporteur, AufDK = 'K'.
        """
        tour_nr = str(tour_nr or "").strip()
        kunden_nr = str(kunden_nr or "").strip()
        if not tour_nr or not kunden_nr:
            return False

        sql = """
            SELECT TOP 1 1 AS ok
            FROM dbo.XXAV_InvC_PrintedShipments
            WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) = ?
              AND LTRIM(RTRIM(CAST(FANr AS VARCHAR(20)))) = ?
              AND AufDK = 'K'
        """
        row = self.fetch_one(sql, (tour_nr, kunden_nr))
        return row is not None

    def get_existing_printed_shipment_tournrs(self, tour_numbers: list[str], kunden_nr) -> set[str]:
        """Retourne les TourNr déjà présents dans XXAV_InvC_PrintedShipments
        pour le KundenNr transporteur donné.
        """
        kunden_nr = str(kunden_nr or "").strip()
        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]
        if not kunden_nr or not tour_numbers:
            return set()

        out: set[str] = set()
        chunk_size = 200
        for i in range(0, len(tour_numbers), chunk_size):
            chunk = tour_numbers[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            sql = f"""
                SELECT DISTINCT LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) AS TourNr
                FROM dbo.XXAV_InvC_PrintedShipments
                WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) IN ({placeholders})
                  AND LTRIM(RTRIM(CAST(FANr AS VARCHAR(20)))) = ?
                  AND AufDK = 'K'
            """
            rows = self.fetch_all(sql, tuple(chunk) + (kunden_nr,)) or []
            for r in rows:
                t = str(r.get("TourNr") or r.get("tournr") or "").strip()
                if t:
                    out.add(t)
        return out

    def get_existing_tournrs(self, tour_numbers: list[str]) -> set[str]:
        """Retourne les TourNr déjà présents dans LISINVOICE_EDTRANS en batch.

        Utilisé à l'affichage des gros PDF pour éviter une requête SQL par
        ligne de dossier.
        """
        tour_numbers = [str(t).strip() for t in (tour_numbers or []) if str(t).strip()]
        if not tour_numbers:
            return set()

        out: set[str] = set()
        chunk_size = 200
        for i in range(0, len(tour_numbers), chunk_size):
            chunk = tour_numbers[i:i + chunk_size]
            placeholders = ",".join(["?"] * len(chunk))
            sql = f"""
                SELECT DISTINCT LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) AS TourNr
                FROM dbo.LISINVOICE_EDTRANS
                WHERE LTRIM(RTRIM(CAST(TourNr AS VARCHAR(20)))) IN ({placeholders})
            """
            rows = self.fetch_all(sql, tuple(chunk)) or []
            for r in rows:
                t = str(r.get("TourNr") or r.get("tournr") or "").strip()
                if t:
                    out.add(t)
        return out

