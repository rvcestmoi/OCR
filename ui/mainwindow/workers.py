from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, Signal, Slot
from .common import *


class _DownloadCanceled(Exception):
    pass


class LinkDownloadWorker(QObject):
        progress = Signal(int, str)          # (index, label)
        finished = Signal(list, list, bool)  # (downloaded_paths, errors, canceled)

        def __init__(self, urls, dest_folder, entry_id=""):
            super().__init__()
            self.urls = urls
            self.dest_folder = dest_folder
            self.entry_id = entry_id           
            self._cancelled = False
            self._current_resp = None

        @Slot()
        def cancel(self):
            self._cancelled = True
            # ✅ tente d'interrompre un read bloqué
            try:
                if self._current_resp is not None:
                    self._current_resp.close()
            except Exception:
                pass

        def _safe_filename(self, name: str) -> str:
            name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
            name = re.sub(r"\s{2,}", " ", name).strip()
            return name or "document.pdf"

        def _guess_filename_from_url(self, url: str) -> str:
            try:
                p = urlparse(url)
                base = os.path.basename(p.path) or ""
                if base:
                    return self._safe_filename(base)
            except Exception:
                pass
            return "document.pdf"

        def _unique_path(self, path: str) -> str:
            if not os.path.exists(path):
                return path
            root, ext = os.path.splitext(path)
            k = 2
            while os.path.exists(f"{root}_{k}{ext}"):
                k += 1
            return f"{root}_{k}{ext}"

        @Slot()
        def run(self):
            os.makedirs(self.dest_folder, exist_ok=True)

            downloaded = []
            errors = []
            canceled = False

            for i, url in enumerate(self.urls, start=1):
                if self._cancelled:
                    canceled = True
                    break

                self.progress.emit(i - 1, f"{i}/{len(self.urls)}\n{url}")

                try:
                    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urlopen(req, timeout=20) as resp:
                        self._current_resp = resp

                        # nom par défaut
                        filename = self._guess_filename_from_url(url)
                        if not filename.lower().endswith(".pdf"):
                            filename = os.path.splitext(filename)[0] + ".pdf"

                        # content-disposition si présent
                        cd = resp.headers.get("Content-Disposition", "") or ""
                        m = re.search(r'filename="?([^"]+)"?', cd, re.IGNORECASE)
                        if m:
                            fn = self._safe_filename(m.group(1))
                            if not fn.lower().endswith(".pdf"):
                                fn += ".pdf"
                            filename = fn

                        stored_filename = self.build_storage_filename(getattr(self, "entry_id", ""), filename)
                        target = self._unique_path(os.path.join(self.dest_folder, stored_filename))

                        tmp = target + ".part"

                        with open(tmp, "wb") as f:
                            # lit un petit header pour valider PDF
                            head = resp.read(5)
                            if self._cancelled:
                                raise RuntimeError("CANCELLED")
                            if not head.startswith(b"%PDF"):
                                raise ValueError("Le lien ne renvoie pas un PDF (%PDF manquant).")
                            f.write(head)

                            while True:
                                if self._cancelled:
                                    raise RuntimeError("CANCELLED")
                                chunk = resp.read(256 * 1024)
                                if not chunk:
                                    break
                                f.write(chunk)

                        # commit atomique
                        if os.path.exists(target):
                            os.remove(target)
                        os.replace(tmp, target)
                        downloaded.append(target)

                except Exception as e:
                    # nettoyage .part
                    try:
                        # si on a pu déterminer un tmp
                        if 'tmp' in locals() and os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass

                    if str(e) == "CANCELLED":
                        canceled = True
                        break
                    errors.append(f"{url} -> {e}")

                finally:
                    self._current_resp = None

            # finir la barre
            self.progress.emit(len(self.urls), f"{len(self.urls)}/{len(self.urls)}")
            self.finished.emit(downloaded, errors, canceled)


class LinkPostProcessWorker(QObject):
        progress = Signal(int, str)           # (index, label)
        finished = Signal(list, list, bool)   # (downloaded_names, errors, canceled)

        def __init__(self, pdf_paths: list[str], entry_id: str, message_id: str, sujet: str, expediteur: str):
            super().__init__()
            self.pdf_paths = pdf_paths or []
            self.entry_id = entry_id
            self.message_id = message_id
            self.sujet = sujet or ""
            self.expediteur = expediteur or ""
            self._cancelled = False

        @Slot()
        def cancel(self):
            self._cancelled = True

        def _json_path_for_pdf(self, pdf_path: str) -> str:
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            model_dir = MODELS_DIR
            return os.path.join(model_dir, f"{base_name}.json")

        def _create_minimal_json_no_ocr(self, pdf_path: str):
            json_path = self._json_path_for_pdf(pdf_path)
            if os.path.exists(json_path):
                return
            data = {
                "entry_id": self.entry_id,
                "status": "draft",
                "tags": ["cmr"],
                "ocr_text": "",
                "folders": [],
                "vat_lines": []
            }
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        @Slot()
        def run(self):
            downloaded_names = []
            errors = []
            canceled = False

            # ✅ repo DB dans le worker (thread-safe car BaseRepository ouvre une connexion par appel)
            from db.connection import SqlServerConnection
            from db.config import DB_CONFIG
            from db.logmail_repository import LogmailRepository

            repo = LogmailRepository(SqlServerConnection(**DB_CONFIG))

            total = len(self.pdf_paths)

            for i, p in enumerate(self.pdf_paths, start=1):
                if self._cancelled:
                    canceled = True
                    break

                name = os.path.basename(p)
                self.progress.emit(i - 1, f"BDD/JSON {i}/{total}\n{name}")

                try:
                    # upsert logmail
                    repo.execute(
                        """
                        UPDATE XXA_LOGMAIL_228794
                        SET entry_id = ?, message_id = ?
                        WHERE nom_pdf = ?;

                        IF @@ROWCOUNT = 0
                        BEGIN
                            INSERT INTO XXA_LOGMAIL_228794 (date_creation, message_id, entry_id, nom_pdf, sujet, expediteur)
                            VALUES (SYSDATETIME(), ?, ?, ?, ?, ?)
                        END
                        """,
                        (self.entry_id, self.message_id, name, self.message_id, self.entry_id, name, self.sujet, self.expediteur),
                    )

                    # json minimal
                    self._create_minimal_json_no_ocr(p)

                    downloaded_names.append(name)

                except Exception as e:
                    errors.append(f"{name} -> {e}")

            self.progress.emit(total, f"BDD/JSON {total}/{total}")
            self.finished.emit(downloaded_names, errors, canceled)
