import os, sys
from db.connection import SqlServerConnection
from db.config import DB_CONFIG
from db.logmail_repository import LogmailRepository

def main(folder: str):
    pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]

    repo = LogmailRepository(SqlServerConnection(**DB_CONFIG))
    entry_map = repo.get_entry_ids_for_files(pdf_files) or {}

    print("\n=== PDF FILES ===")
    for f in pdf_files:
        print(" -", f)

    print("\n=== ENTRY MAP (nom_pdf -> entry_id) ===")
    for f in pdf_files:
        print(f" - {f} => {entry_map.get(f)}")

    groups = {}
    for fn in pdf_files:
        entry_id = entry_map.get(fn)
        if not entry_id:
            entry_id = f"__NO_ENTRY__::{fn}"
        groups.setdefault(entry_id, []).append(fn)

    print("\n=== GROUPS (entry_id -> fichiers) ===")
    for k, v in groups.items():
        print(" -", k, "=>", len(v), v)

    print("\nNB groupes:", len(groups))

if __name__ == "__main__":
    main(r"C:\Users\hrouillard\Documents\clients\ED trans\OCR\modeles2")