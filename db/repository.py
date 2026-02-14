# db/repository.py

from typing import Any, List, Dict
from db.connection import SqlServerConnection


class BaseRepository:
    def __init__(self, connection: SqlServerConnection):
        self._connection = connection

    def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)

            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()

            return [dict(zip(columns, row)) for row in rows]

    def fetch_one(self, query: str, params: tuple = ()) -> Dict[str, Any] | None:
        results = self.fetch_all(query, params)
        return results[0] if results else None

    def execute(self, query: str, params: tuple = ()) -> None:
        with self._connection.connect() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
