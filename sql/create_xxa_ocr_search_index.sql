IF OBJECT_ID('dbo.XXA_OCR_SEARCH_INDEX', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.XXA_OCR_SEARCH_INDEX (
        entry_id varchar(200) NOT NULL CONSTRAINT PK_XXA_OCR_SEARCH_INDEX PRIMARY KEY,
        nom_pdf nvarchar(500) NULL,
        processing_status varchar(30) NOT NULL CONSTRAINT DF_XXA_OCR_SEARCH_INDEX_status DEFAULT('pending'),
        invoice_number nvarchar(100) NULL,
        invoice_date nvarchar(50) NULL,
        iban nvarchar(80) NULL,
        bic nvarchar(50) NULL,
        tour_numbers nvarchar(max) NULL,
        transporter_kundennr nvarchar(100) NULL,
        transporter_name nvarchar(255) NULL,
        date_mail datetime2 NULL,
        expediteur nvarchar(255) NULL,
        search_text nvarchar(max) NULL,
        search_compact nvarchar(max) NULL,
        updated_at datetime2 NOT NULL CONSTRAINT DF_XXA_OCR_SEARCH_INDEX_updated DEFAULT(SYSDATETIME())
    );

    CREATE INDEX IX_XXA_OCR_SEARCH_INDEX_status_updated
        ON dbo.XXA_OCR_SEARCH_INDEX(processing_status, updated_at DESC);

    CREATE INDEX IX_XXA_OCR_SEARCH_INDEX_date_mail
        ON dbo.XXA_OCR_SEARCH_INDEX(date_mail DESC);
END
ELSE
BEGIN
    IF COL_LENGTH('dbo.XXA_OCR_SEARCH_INDEX', 'transporter_name') IS NULL
        ALTER TABLE dbo.XXA_OCR_SEARCH_INDEX ADD transporter_name nvarchar(255) NULL;

    IF COL_LENGTH('dbo.XXA_OCR_SEARCH_INDEX', 'date_mail') IS NULL
        ALTER TABLE dbo.XXA_OCR_SEARCH_INDEX ADD date_mail datetime2 NULL;

    IF COL_LENGTH('dbo.XXA_OCR_SEARCH_INDEX', 'expediteur') IS NULL
        ALTER TABLE dbo.XXA_OCR_SEARCH_INDEX ADD expediteur nvarchar(255) NULL;

    IF NOT EXISTS (
        SELECT 1
        FROM sys.indexes
        WHERE name = 'IX_XXA_OCR_SEARCH_INDEX_date_mail'
          AND object_id = OBJECT_ID('dbo.XXA_OCR_SEARCH_INDEX')
    )
        CREATE INDEX IX_XXA_OCR_SEARCH_INDEX_date_mail
            ON dbo.XXA_OCR_SEARCH_INDEX(date_mail DESC);
END
