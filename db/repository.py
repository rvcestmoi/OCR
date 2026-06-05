# db/repository.py

from typing import Any, List, Dict
from datetime import date, datetime
from decimal import Decimal

from db.connection import SqlServerConnection


class BaseRepository:
    def __init__(self, connection: SqlServerConnection):
        self._connection = connection

    def _normalize_param(self, value):
        if value is None:
            return None

        # vieux driver "SQL Server" : support limité des Decimal/date Python
        if isinstance(value, Decimal):
            return format(value, "f")  # ex: 470.00

        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M:%S")

        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")

        return value

    def _normalize_params(self, params: tuple = ()) -> tuple:
        return tuple(self._normalize_param(p) for p in (params or ()))

    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, self._normalize_params(params))

            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()

            return [dict(zip(columns, row)) for row in rows]

    def fetch_one(self, query: str, params: tuple = ()) -> Dict[str, Any] | None:
        results = self.fetch_all(query, params)
        return results[0] if results else None

    def execute(self, query: str, params: tuple = ()) -> None:
        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, self._normalize_params(params))
            conn.commit()

    def execute_rowcount(self, query: str, params: tuple = ()) -> int:
        """Exécute une requête d'écriture et retourne le nombre de lignes touchées.

        Utile pour éviter d'afficher un succès alors qu'un UPDATE n'a en réalité
        rien modifié côté SQL Server.
        """
        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, self._normalize_params(params))
            rowcount = cursor.rowcount
            conn.commit()
            try:
                return int(rowcount or 0)
            except Exception:
                return 0