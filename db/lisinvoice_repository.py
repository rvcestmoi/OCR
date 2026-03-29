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