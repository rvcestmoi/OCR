"""
Gestionnaire de connexion SQL Server pour OCR Factures
"""
import pyodbc
from config import get_connection_string, get_connection_string_windows_auth
import logging

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Gestionnaire de connexion et opérations SQL Server"""
    
    def __init__(self, use_windows_auth=False):
        """
        Initialise le gestionnaire de base de données
        
        Args:
            use_windows_auth (bool): Si True, utilise l'authentification Windows
        """
        self.use_windows_auth = use_windows_auth
        self.connection = None
    
    def connect(self):
        """Établit la connexion à SQL Server"""
        try:
            conn_str = (
                get_connection_string_windows_auth() 
                if self.use_windows_auth 
                else get_connection_string()
            )
            self.connection = pyodbc.connect(conn_str)
            logger.info("✅ Connexion SQL Server établie avec succès")
            return self.connection
        except pyodbc.Error as e:
            logger.error(f"❌ Erreur de connexion SQL Server: {e}")
            raise
    
    def disconnect(self):
        """Ferme la connexion"""
        if self.connection:
            self.connection.close()
            logger.info("✅ Connexion SQL Server fermée")
    
    def execute_query(self, query, params=None):
        """
        Exécute une requête SELECT
        
        Args:
            query (str): Requête SQL
            params (tuple): Paramètres pour la requête
        
        Returns:
            list: Liste des résultats
        """
        try:
            cursor = self.connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            results = cursor.fetchall()
            cursor.close()
            return results
        except pyodbc.Error as e:
            logger.error(f"❌ Erreur lors de l'exécution de la requête: {e}")
            raise
    
    def execute_update(self, query, params=None):
        """
        Exécute une requête INSERT, UPDATE ou DELETE
        
        Args:
            query (str): Requête SQL
            params (tuple): Paramètres pour la requête
        
        Returns:
            int: Nombre de lignes affectées
        """
        try:
            cursor = self.connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            affected_rows = cursor.rowcount
            self.connection.commit()
            cursor.close()
            logger.info(f"✅ {affected_rows} ligne(s) affectée(s)")
            return affected_rows
        except pyodbc.Error as e:
            self.connection.rollback()
            logger.error(f"❌ Erreur lors de la mise à jour: {e}")
            raise
    
    def call_procedure(self, procedure_name, params=None):
        """
        Appelle une procédure stockée
        
        Args:
            procedure_name (str): Nom de la procédure
            params (tuple): Paramètres pour la procédure
        
        Returns:
            list: Résultats de la procédure
        """
        try:
            cursor = self.connection.cursor()
            
            if params:
                # Créer la liste des placeholders
                placeholders = ",".join(["?" for _ in params])
                query = f"EXEC {procedure_name} {placeholders}"
                cursor.execute(query, params)
            else:
                cursor.execute(f"EXEC {procedure_name}")
            
            results = cursor.fetchall()
            cursor.close()
            return results
        except pyodbc.Error as e:
            logger.error(f"❌ Erreur lors de l'appel à {procedure_name}: {e}")
            raise


# Singleton pour éviter plusieurs connexions
_db_manager = None


def get_database_manager(use_windows_auth=False):
    """
    Obtient l'instance unique du gestionnaire de base de données
    
    Args:
        use_windows_auth (bool): Si True, utilise l'authentification Windows
    
    Returns:
        DatabaseManager: Instance unique
    """
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager(use_windows_auth)
        _db_manager.connect()
    return _db_manager
