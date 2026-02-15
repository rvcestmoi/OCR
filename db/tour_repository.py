# db/tour_repository.py
from db.repository import BaseRepository

class TourRepository(BaseRepository):
    def __init__(self, connection):
        super().__init__(connection)

    def find_by_tournr(self, tour_nr: str):
        query = """
            SELECT TourNr
            FROM xxatour
            WHERE TourNr = ?
        """
        return self.fetch_one(query, (tour_nr,))
