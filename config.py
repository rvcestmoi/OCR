"""
Configuration SQL Server pour OCR Factures
Supporte les variables d'environnement pour les données sensibles
"""
import os
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env
load_dotenv()

# =========================
# Configuration SQL Server
# =========================

SQL_SERVER_CONFIG = {
    "server": os.getenv("SQL_SERVER_HOST", "localhost"),
    "database": os.getenv("SQL_SERVER_DB", "OCR_Factures"),
    "username": os.getenv("SQL_SERVER_USER", "sa"),
    "password": os.getenv("SQL_SERVER_PASSWORD", ""),
    "port": int(os.getenv("SQL_SERVER_PORT", "1433")),
    "driver": "ODBC Driver 17 for SQL Server",  # ou "ODBC Driver 18 for SQL Server"
    "encrypt": os.getenv("SQL_SERVER_ENCRYPT", "yes").lower() == "yes",
    "trust_cert": os.getenv("SQL_SERVER_TRUST_CERT", "no").lower() == "yes",
}

# Connection string pour pyodbc
def get_connection_string():
    """Construit la chaîne de connexion SQL Server"""
    conn_str = (
        f"Driver={{{SQL_SERVER_CONFIG['driver']}}};"
        f"Server={SQL_SERVER_CONFIG['server']},{SQL_SERVER_CONFIG['port']};"
        f"Database={SQL_SERVER_CONFIG['database']};"
        f"UID={SQL_SERVER_CONFIG['username']};"
        f"PWD={SQL_SERVER_CONFIG['password']};"
    )
    
    if SQL_SERVER_CONFIG['encrypt']:
        conn_str += "Encrypt=yes;"
    
    if SQL_SERVER_CONFIG['trust_cert']:
        conn_str += "TrustServerCertificate=yes;"
    
    return conn_str


# Connection string pour pyodbc avec authentification Windows (optional)
def get_connection_string_windows_auth():
    """Construit la chaîne de connexion avec authentification Windows"""
    conn_str = (
        f"Driver={{{SQL_SERVER_CONFIG['driver']}}};"
        f"Server={SQL_SERVER_CONFIG['server']},{SQL_SERVER_CONFIG['port']};"
        f"Database={SQL_SERVER_CONFIG['database']};"
        f"Trusted_Connection=yes;"
    )
    
    if SQL_SERVER_CONFIG['encrypt']:
        conn_str += "Encrypt=yes;"
    
    if SQL_SERVER_CONFIG['trust_cert']:
        conn_str += "TrustServerCertificate=yes;"
    
    return conn_str


# =========================
# Configuration OCR
# =========================

OCR_CONFIG = {
    "tesseract_path": os.getenv(
        "TESSERACT_PATH",
        r"C:\Users\hrouillard\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"
    ),
    "poppler_path": os.getenv(
        "POPPLER_PATH",
        r"C:\poppler\Library\bin"
    ),
    "ocr_dpi": int(os.getenv("OCR_DPI", "150")),
    "ocr_languages": os.getenv("OCR_LANGUAGES", "fra+eng+deu+spa+ita+nld").split("+"),
}
