"""
Test de connexion à SQL Server
"""
from services.database import get_database_manager
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_connection():
    """Test la connexion à SQL Server"""
    try:
        logger.info("🔄 Tentative de connexion à SQL Server...")
        
        # Option 1: Authentification SQL Server (utilisateur/password)
        db = get_database_manager(use_windows_auth=False)
        
        # Test d'une requête simple
        logger.info("📊 Exécution d'une requête test...")
        results = db.execute_query("SELECT 1 as test_value")
        logger.info(f"✅ Résultat: {results[0]}")
        
        # Afficher les informations serveur
        db_info = db.execute_query("SELECT @@VERSION as server_version")
        logger.info(f"📌 Serveur: {db_info[0][0]}")
        
        # Lister les bases de données disponibles
        databases = db.execute_query("SELECT name FROM sys.databases ORDER BY name")
        logger.info(f"📂 Bases de données disponibles:")
        for db_name in databases:
            logger.info(f"   - {db_name[0]}")
        
        db.disconnect()
        logger.info("✅ Test de connexion réussi!")
        
    except Exception as e:
        logger.error(f"❌ Erreur: {e}")
        logger.info("\n💡 Vérifiez:")
        logger.info("   1. SQL Server est démarré")
        logger.info("   2. Le fichier .env contient les bonnes identifiants")
        logger.info("   3. pyodbc est installé: pip install pyodbc")
        logger.info("   4. Le driver ODBC est installé sur le système")


def test_windows_auth():
    """Test la connexion avec authentification Windows"""
    try:
        logger.info("🔄 Tentative de connexion avec authentification Windows...")
        
        db = get_database_manager(use_windows_auth=True)
        
        results = db.execute_query("SELECT 1 as test_value")
        logger.info(f"✅ Résultat: {results[0]}")
        
        db.disconnect()
        logger.info("✅ Test de connexion Windows Auth réussi!")
        
    except Exception as e:
        logger.error(f"❌ Erreur: {e}")


if __name__ == "__main__":
    logger.info("=== TEST DE CONNEXION SQL SERVER ===\n")
    test_connection()
    
    # Décommenter pour tester l'authentification Windows
    # logger.info("\n=== TEST AUTHENTIFICATION WINDOWS ===\n")
    # test_windows_auth()
