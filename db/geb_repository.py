# db/geb_repository.py
from db.repository import BaseRepository

class GebRepository(BaseRepository):
    def search_gebs(self, term: str = ""):
        term = (term or "").strip()

        if not term:
            query = """
                SELECT TOP 50 GebNr, Bez
                FROM XXAGeb
                ORDER BY GebNr
            """
            return self.fetch_all(query)

        like = f"%{term}%"
        query = """
            SELECT TOP 50 GebNr, Bez
            FROM XXAGeb
            WHERE CONVERT(VARCHAR(30), GebNr) LIKE ?
               OR UPPER(Bez) LIKE UPPER(?)
            ORDER BY GebNr
        """
        return self.fetch_all(query, (like, like))