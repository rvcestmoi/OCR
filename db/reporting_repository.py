from __future__ import annotations

from db.repository import BaseRepository


class ReportingRepository(BaseRepository):
    """Alimentation de la table de reporting des modifications OCR."""

    TABLE_NAME = "dbo.XXA_OCR_REPORTING_MODIFS"

    def ensure_table_exists(self) -> None:
        """Crée la table de reporting si elle n'existe pas.

        L'application appelle cette méthode avant l'upsert pour éviter un
        plantage si le script SQL n'a pas encore été exécuté sur un poste.
        Si l'utilisateur SQL n'a pas les droits de création, l'erreur remontera
        et sera affichée comme une erreur de reporting non bloquante.
        """
        sql = """
            IF OBJECT_ID('dbo.XXA_OCR_REPORTING_MODIFS', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.XXA_OCR_REPORTING_MODIFS
                (
                    IdReporting INT IDENTITY(1,1) NOT NULL,
                    DateModification DATETIME2(0) NOT NULL CONSTRAINT DF_XXA_OCR_REPORTING_MODIFS_DateModification DEFAULT SYSDATETIME(),
                    Utilisateur NVARCHAR(128) NOT NULL,
                    RechNr VARCHAR(100) NOT NULL,
                    TourNr VARCHAR(20) NOT NULL,
                    IsBloque BIT NOT NULL CONSTRAINT DF_XXA_OCR_REPORTING_MODIFS_IsBloque DEFAULT (0),
                    IsValidated BIT NOT NULL CONSTRAINT DF_XXA_OCR_REPORTING_MODIFS_IsValidated DEFAULT (0),
                    IsLastModifierForTour BIT NOT NULL CONSTRAINT DF_XXA_OCR_REPORTING_MODIFS_IsLastModifierForTour DEFAULT (0),
                    DateCreation DATETIME2(0) NOT NULL CONSTRAINT DF_XXA_OCR_REPORTING_MODIFS_DateCreation DEFAULT SYSDATETIME(),
                    CONSTRAINT PK_XXA_OCR_REPORTING_MODIFS PRIMARY KEY CLUSTERED (IdReporting ASC),
                    CONSTRAINT UX_XXA_OCR_REPORTING_MODIFS_UserRechTour UNIQUE (Utilisateur, RechNr, TourNr)
                );

                CREATE INDEX IX_XXA_OCR_REPORTING_MODIFS_TourNr_Last
                    ON dbo.XXA_OCR_REPORTING_MODIFS (TourNr, IsLastModifierForTour, DateModification DESC);

                CREATE INDEX IX_XXA_OCR_REPORTING_MODIFS_RechNr
                    ON dbo.XXA_OCR_REPORTING_MODIFS (RechNr);
            END

            IF COL_LENGTH('dbo.XXA_OCR_REPORTING_MODIFS', 'IsValidated') IS NULL
            BEGIN
                ALTER TABLE dbo.XXA_OCR_REPORTING_MODIFS
                ADD IsValidated BIT NOT NULL
                    CONSTRAINT DF_XXA_OCR_REPORTING_MODIFS_IsValidated DEFAULT (0);
            END
        """
        self.execute(sql)

    def upsert_modification(
        self,
        *,
        utilisateur: str,
        rech_nr: str,
        tour_nr: str,
        is_bloque: bool,
        is_validated: bool = False,
    ) -> None:
        utilisateur = str(utilisateur or "").strip()
        rech_nr = str(rech_nr or "").strip()
        tour_nr = str(tour_nr or "").strip()

        if not utilisateur:
            raise ValueError("Utilisateur vide pour le reporting OCR.")
        if not rech_nr:
            raise ValueError("Numéro de facture vide pour le reporting OCR.")
        if not tour_nr:
            raise ValueError("Numéro de dossier vide pour le reporting OCR.")

        sql = """
            SET XACT_ABORT ON;
            BEGIN TRANSACTION;

            UPDATE dbo.XXA_OCR_REPORTING_MODIFS
            SET IsLastModifierForTour = 0
            WHERE TourNr = ?;

            UPDATE dbo.XXA_OCR_REPORTING_MODIFS
            SET DateModification = SYSDATETIME(),
                IsBloque = ?,
                IsValidated = CASE WHEN ? = 1 THEN 1 ELSE IsValidated END,
                IsLastModifierForTour = 1
            WHERE Utilisateur = ?
              AND RechNr = ?
              AND TourNr = ?;

            IF @@ROWCOUNT = 0
            BEGIN
                INSERT INTO dbo.XXA_OCR_REPORTING_MODIFS
                (
                    DateModification,
                    Utilisateur,
                    RechNr,
                    TourNr,
                    IsBloque,
                    IsValidated,
                    IsLastModifierForTour
                )
                VALUES
                (
                    SYSDATETIME(),
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    1
                );
            END

            COMMIT TRANSACTION;
        """
        is_bloque_db = 1 if is_bloque else 0
        is_validated_db = 1 if is_validated else 0
        self.execute(
            sql,
            (
                tour_nr,
                is_bloque_db,
                is_validated_db,
                utilisateur,
                rech_nr,
                tour_nr,
                utilisateur,
                rech_nr,
                tour_nr,
                is_bloque_db,
                is_validated_db,
            ),
        )

    def upsert_modifications_for_invoice(
        self,
        *,
        utilisateur: str,
        rech_nr: str,
        tour_nrs: list[str],
        is_bloque: bool,
        is_validated: bool = False,
    ) -> list[str]:
        """Upsert une ligne par dossier et retourne les erreurs éventuelles."""
        self.ensure_table_exists()

        errors: list[str] = []
        seen: set[str] = set()
        for tour_nr in tour_nrs or []:
            tour_nr = str(tour_nr or "").strip()
            if not tour_nr or tour_nr in seen:
                continue
            seen.add(tour_nr)
            try:
                self.upsert_modification(
                    utilisateur=utilisateur,
                    rech_nr=rech_nr,
                    tour_nr=tour_nr,
                    is_bloque=is_bloque,
                    is_validated=is_validated,
                )
            except Exception as e:
                errors.append(f"{tour_nr} : {e}")
        return errors
