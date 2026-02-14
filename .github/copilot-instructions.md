# OCR Codebase Guide for AI Agents

## Project Overview
Invoice OCR extraction system with supplier model matching. PySide6-based UI processes PDFs using Tesseract OCR + PyMuPDF, extracts financial data (IBAN, BIC, invoice numbers), and matches suppliers via pre-built JSON models.

## Architecture

### Data Flow
1. **PDF Input** → `ocr/ocr_engine.py:extract_text_from_pdf()` → Raw OCR text
2. **Text Parsing** → `ocr/invoice_parser.py:parse_invoice()` → Structured `InvoiceData` 
3. **Field Detection** → `ocr/field_detector.py:guess_field()` → Categorize extracted values
4. **Supplier Lookup** → `ocr/supplier_model.py` → Match IBAN+BIC to `models/suppliers/*.json`
5. **UI Display** → `ui/main_window.py` → Interactive review/manual correction

### Key Modules
- **`ocr/ocr_engine.py`**: Two-stage PDF processing:
  - Stage 1: Fast native text extraction via PyMuPDF (`fitz`)
  - Stage 2: Fallback OCR with Tesseract if Stage 1 yields <100 chars (DPI=150 for speed)
  - Hardcoded paths (Tesseract, Poppler) for Windows environment
- **`ocr/invoice_parser.py`**: Regex-based extraction with OCR-safe normalization
  - `InvoiceData` dataclass holds: IBAN, BIC, invoice_date, invoice_number, folder_number
  - Defensive extraction logic (IBAN priority near label, BIC blacklist check)
- **`ocr/anchor_extractor.py`**: Spatial text extraction using word positions
  - `extract_by_anchor()` finds keyword, then extracts adjacent words (right/below directions)
  - Used for structured layout extraction with tolerance (distance, line alignment)
- **`ocr/supplier_model.py`**: JSON model loader
  - Key format: `"{IBAN}_{BIC}"`
  - Models stored in `models/suppliers/` directory

## Critical Patterns

### OCR Text Normalization
All extraction functions normalize via `_normalize_ocr()` (in `invoice_parser.py`):
- Removes common OCR artifacts (misread chars, spacing)
- Case-insensitive matching
- Before regex matching, always normalize input text

### Regex-First Extraction
- **IBAN**: Accepts 15-34 chars, prioritizes text near "IBAN" label
- **BIC**: Strict 8 or 11 chars, requires explicit label (BIC/SWIFT), blacklist validation
- **Invoice Number**: Heuristic scoring (see `field_detector.py`)
- Always strip/replace spaces and dashes before pattern matching

### Windows-Specific Paths
```python
# Hardcoded in ocr_engine.py - adjust for different environments
pytesseract.pytesseract.tesseract_cmd = r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\poppler\Library\bin"
```
When porting: Replace with environment variables or config file lookups.

### UI Workflow (PySide6)
- Main window spans 1200x800, three panels: PDF list (left), PDF viewer (center), OCR results (right)
- `pdf_viewer.text_selected` signal connects to field filling via `fill_active_field()`
- Manual field editing possible in UI before saving

## Testing Approach
- **`test.py`**: Basic extraction test (run to validate OCR paths work)
- **`test_anchor.py`**: Spatial extraction validation
- **`test_parsing.py`**: Invoice field extraction tests
- No automated test runner configured; run individually with `python test_*.py`

## External Dependencies
- **pytesseract**: OCR text/data extraction; requires system Tesseract installation
- **pdf2image + poppler**: PDF → PNG conversion (for OCR fallback)
- **PyMuPDF (fitz)**: Native PDF text extraction (Stage 1, faster)
- **PySide6**: UI framework
- **dataclasses**: Type hints for `InvoiceData`
- **pyodbc**: SQL Server database connectivity
- **python-dotenv**: Environment variable management from `.env` files

## SQL Server Configuration

### Setup
1. Copy `.env.example` to `.env` and update with your SQL Server credentials:
   ```
   SQL_SERVER_HOST=localhost
   SQL_SERVER_PORT=1433
   SQL_SERVER_DB=OCR_Factures
   SQL_SERVER_USER=sa
   SQL_SERVER_PASSWORD=your_password
   ```

2. Install ODBC driver for SQL Server: `ODBC Driver 17 for SQL Server` or `18`

3. Install Python dependencies:
   ```bash
   pip install pyodbc python-dotenv
   ```

### Usage
- **DatabaseManager** class in `services/database.py` handles all SQL operations
- Supports both SQL Server authentication and Windows authentication
- Connection string built dynamically from `config.py` environment variables
- Test connection: `python test_database.py`

### Common Operations
```python
from services.database import get_database_manager

db = get_database_manager()
# Query: results = db.execute_query("SELECT * FROM Factures")
# Insert/Update: db.execute_update("INSERT INTO Factures ...")
# Procedures: db.call_procedure("sp_procedure_name", params)
db.disconnect()
```

## Development Priorities
1. **Path Configuration**: First refactor Windows hardcoded paths to config/env
2. **Test Automation**: Add pytest framework with fixture PDFs
3. **Supplier Model Coverage**: Audit `models/suppliers/` for missing IBAN_BIC combos
4. **Error Handling**: Current code has minimal try-except; add logging
5. **Performance**: DPI=150 is optimization trade-off; benchmark against DPI=300 for accuracy

## Common Workflows
- **Debug PDF Extraction**: Run `test.py` with target PDF, inspect raw OCR text
- **Add Supplier Model**: Create JSON file `models/suppliers/{IBAN}_{BIC}.json` with field definitions
- **Extend Field Detection**: Modify `guess_field()` scoring in `field_detector.py`, run `test_parsing.py`
- **UI Testing**: `python main.py` then load PDF folder via "Analyser un dossier" button
