from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled
import fitz


class MainWindowLinksMixin:

    def _block_validate_if_missing_cmr(self) -> bool:
        """
        Retourne True si on peut continuer la validation.
        Retourne False si bloqué + message.
        """
        ok, missing = self._check_all_dossiers_have_cmr()
        if ok:
            return True

        QMessageBox.warning(
            self,
            "Validation impossible",
            "Tous les dossiers doivent être rattachés à au moins une CMR avant validation.\n\n"
            f"Dossiers sans CMR : {', '.join(missing)}"
        )
        return False   

    def on_fetch_links_main(self):
        pdf_path = self.view_pdf_path or self.current_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.information(self, "Liens", "Aucun document affiché.")
            return

        source_filename = os.path.basename(pdf_path)

        info = self._get_logmail_info_for_pdf(source_filename)
        entry_id = (info.get("entry_id") or "").strip()
        message_id = (info.get("message_id") or "").strip()
        sujet = info.get("sujet") or ""
        expediteur = info.get("expediteur") or ""

        if not entry_id:
            try:
                json_path = self._get_saved_json_path(pdf_path)
                if os.path.exists(json_path):
                    with open(json_path, "r", encoding="utf-8") as f:
                        data = json.load(f) or {}
                    entry_id = str(data.get("entry_id") or "").strip()
            except Exception:
                pass

        if not entry_id:
            QMessageBox.information(self, "Liens", "Impossible de déterminer l'entry_id pour ce document.")
            return

        if not message_id:
            message_id = entry_id

        urls = self._extract_urls_from_pdf(pdf_path)
        if not urls:
            QMessageBox.information(self, "Liens", "Aucun lien HTTP(S) trouvé dans ce document.")
            return

        dest_folder = os.path.dirname(pdf_path)

        # ✅ progress dialog
        self._links_prog = QProgressDialog("Téléchargement des documents liés…", "Annuler", 0, len(urls), self)
        self._links_prog.setWindowModality(Qt.WindowModal)
        self._links_prog.setMinimumDuration(0)
        self._links_prog.setValue(0)
        self._links_prog.show()

        # ✅ thread + worker
        self._links_thread = QThread(self)
        self._links_worker = LinkDownloadWorker(urls, dest_folder, entry_id=entry_id)
        self._links_worker.moveToThread(self._links_thread)

        # cancel
        self._links_prog.canceled.connect(self._links_worker.cancel)

        # start
        self._links_thread.started.connect(self._links_worker.run)

        # progress UI
        self._links_worker.progress.connect(lambda v, txt: (self._links_prog.setValue(v), self._links_prog.setLabelText(txt)))

        def _done(downloaded_paths: list[str], errors: list[str], canceled: bool):
            # stop thread download
            try:
                self._links_thread.quit()
            except Exception:
                pass

            if canceled or not downloaded_paths:
                try:
                    self._links_prog.close()
                except Exception:
                    pass
                QMessageBox.information(self, "Liens", "Annulé." if canceled else "Aucun téléchargement.")
                return

            # ✅ maintenant on lance le post-process en thread (BDD + JSON)
            self._post_thread = QThread(self)
            self._post_worker = LinkPostProcessWorker(downloaded_paths, entry_id, message_id, sujet, expediteur)
            self._post_worker.moveToThread(self._post_thread)

            # progress dialog réutilisé
            try:
                self._links_prog.setMaximum(len(downloaded_paths))
                self._links_prog.setValue(0)
                self._links_prog.setLabelText("Mise à jour BDD/JSON…")
            except Exception:
                pass

            # cancel => post worker
            try:
                self._links_prog.canceled.disconnect(self._links_worker.cancel)
            except Exception:
                pass
            self._links_prog.canceled.connect(self._post_worker.cancel)

            self._post_thread.started.connect(self._post_worker.run)
            self._post_worker.progress.connect(lambda v, txt: (self._links_prog.setValue(v), self._links_prog.setLabelText(txt)))

            def _post_done(downloaded_names: list[str], post_errors: list[str], post_canceled: bool):
                try:
                    self._links_prog.close()
                except Exception:
                    pass

                all_errors = (errors or []) + (post_errors or [])

                # ⚠️ refresh folder : peut être lourd, donc on le fait après (et tu peux le désactiver si besoin)
                try:
                    self.load_folder(dest_folder)
                except Exception:
                    pass

                msg = []
                if downloaded_names:
                    msg.append("Téléchargés + ajoutés en base:\n- " + "\n- ".join(downloaded_names))
                if post_canceled:
                    msg.append("\nPost-traitement annulé.")
                if all_errors:
                    msg.append("\nErreurs:\n- " + "\n- ".join(all_errors[:8]) + ("\n(...)" if len(all_errors) > 8 else ""))

                QMessageBox.information(self, "Liens", "\n\n".join(msg) if msg else "Terminé.")

                try:
                    self._post_thread.quit()
                except Exception:
                    pass

            self._post_worker.finished.connect(_post_done)
            self._post_thread.start()

            self._links_worker.finished.connect(_done)
            self._links_thread.finished.connect(self._links_thread.deleteLater)
            self._links_worker.finished.connect(self._links_worker.deleteLater)

            self._links_thread.start()

    def _extract_urls_from_pdf(self, pdf_path: str) -> list[str]:
        urls = []
        try:
            doc = fitz.open(pdf_path)
            for page in doc:
                # liens PDF (annotations)
                try:
                    for lk in page.get_links() or []:
                        uri = lk.get("uri")
                        if uri and isinstance(uri, str) and uri.lower().startswith(("http://", "https://")):
                            urls.append(uri)
                except Exception:
                    pass

                # liens dans le texte
                try:
                    txt = page.get_text() or ""
                    _URL_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)
                    for m in _URL_RE.findall(txt):
                        urls.append(m)
                except Exception:
                    pass
            doc.close()
        except Exception:
            return []

        # clean + unique
        out = []
        seen = set()
        for u in urls:
            u = (u or "").strip().rstrip(").,;\"'")
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out  

    def _guess_filename_from_url(self, url: str) -> str:
        try:
            p = urlparse(url)
            base = os.path.basename(p.path) or ""
            if base:
                return self._safe_filename(base)
        except Exception:
            pass
        return "document.pdf"

    def _safe_filename(self, name: str) -> str:
        name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
        name = re.sub(r"\s{2,}", " ", name).strip()
        return name or "document.pdf"

    def _get_logmail_info_for_pdf(self, nom_pdf: str) -> dict:
        # TOP 1 le plus récent (utile si doublons)
        row = self.logmail_repo.fetch_one(
            """
            SELECT TOP 1 message_id, entry_id, sujet, expediteur
            FROM XXA_LOGMAIL_228794
            WHERE nom_pdf = ?
            ORDER BY date_creation DESC, id_log DESC
            """,
            (nom_pdf,),
        )
        return row or {}

    def _upsert_logmail_for_downloaded_file(self, nom_pdf: str, entry_id: str, message_id: str, sujet: str = "", expediteur: str = ""):
        # Update si existe, sinon insert
        self.logmail_repo.execute(
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
            (entry_id, message_id, nom_pdf, message_id, entry_id, nom_pdf, sujet or "", expediteur or ""),
        )

    def _create_minimal_json_no_ocr(self, pdf_path: str, entry_id: str):
        json_path = self._get_saved_json_path(pdf_path)
        if os.path.exists(json_path):
            return
        data = {
            "entry_id": entry_id,
            "status": "draft",
            "tags": ["cmr"],
            "ocr_text": "",
            "folders": [],
            "vat_lines": []
        }
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def on_save_clicked(self):
        # sauvegarde normale (avec popup)
        self.save_current_data(show_message=True)

        # MAJ modèle supplier en silencieux
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            pass

