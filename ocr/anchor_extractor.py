# ocr/anchor_extractor.py

import re
from typing import Dict, List


class AnchorExtractor:
    """
    Extracteur d'ancres PUREMENT TEXTUEL.
    Aucune notion de position visuelle ou de coordonnées PDF.
    """

    def __init__(self, anchor_patterns: Dict[str, str]):
        """
        anchor_patterns :
            {
                "truck_id": r"Camion\s*:\s*(\w+)",
                "total_km": r"Total\s+kilom[eè]tres\s*:\s*([\d,.]+)",
                "amount": r"Montant\s*:\s*([\d,.]+)"
            }
        """
        self.anchor_patterns = {
            name: re.compile(pattern, re.IGNORECASE)
            for name, pattern in anchor_patterns.items()
        }

    def extract(self, text: str) -> Dict[str, List[str]]:
        """
        Recherche toutes les ancres dans le texte brut.

        Retour :
            {
                "truck_id": ["TR123", "TR124"],
                "total_km": ["1234", "987"],
                "amount": ["456.78"]
            }
        """
        results: Dict[str, List[str]] = {}

        for anchor_name, pattern in self.anchor_patterns.items():
            matches = pattern.findall(text)

            if not matches:
                continue

            # Normalisation : toujours une liste de strings
            cleaned = []
            for m in matches:
                if isinstance(m, tuple):
                    cleaned.append(" ".join(m))
                else:
                    cleaned.append(m)

            results[anchor_name] = cleaned

        return results
