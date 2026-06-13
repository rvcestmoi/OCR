from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled


class MainWindowDocumentsMixin:

    def build_entry_pdf_group(self):
        """
        Construit self.entry_pdf_paths à partir du entry_id de la facture sélectionnée.
        La facture (current_pdf_path) est mise en premier.
        """

        self.entry_pdf_paths = []
        self.current_doc_index = 0

        if not self.selected_invoice_entry_id or not self.current_pdf_path:
            # groupe minimal = juste la facture
            if self.current_pdf_path:
                self.entry_pdf_paths = [self.current_pdf_path]
            self.update_doc_indicator()
            return

        current_dir = os.path.dirname(self.current_pdf_path)
        invoice_path = self.current_pdf_path

        try:
            rows = self.logmail_repo.get_files_for_entry(self.selected_invoice_entry_id) or []
        except Exception:
            rows = []

        paths = []
        for r in rows:
            name = r.get("nom_pdf") or r.get("Nom_PDF") or r.get("filename") or ""
            name = str(name).strip()
            if not name:
                continue
            full_path = os.path.join(current_dir, name)
            if not is_supported_document(full_path):
                continue
            if os.path.exists(full_path) and full_path not in paths:
                paths.append(full_path)

        chosen_invoice_path = invoice_path
        if hasattr(self, "_choose_invoice_source_document"):
            try:
                chosen_invoice_path = str(self._choose_invoice_source_document(paths or [invoice_path], fallback_path=invoice_path) or invoice_path).strip() or invoice_path
            except Exception:
                chosen_invoice_path = invoice_path

        # s’assurer que le document source facture est dans la liste + en premier
        if chosen_invoice_path in paths:
            paths.remove(chosen_invoice_path)
        paths.insert(0, chosen_invoice_path)

        self.entry_pdf_paths = paths
        self.update_doc_indicator()

    def show_doc_by_index(self, index: int):
        if not self.entry_pdf_paths:
            self.update_doc_indicator()
            return

        index = max(0, min(index, len(self.entry_pdf_paths) - 1))
        self.current_doc_index = index

        self.view_pdf_path = self.entry_pdf_paths[self.current_doc_index]
        self.display_pdf()
        self.update_page_indicator()
        self.update_doc_indicator()

    def update_doc_indicator(self):
        total = len(self.entry_pdf_paths)
        if total <= 0:
            self.lbl_doc_info.setText("Doc 0 / 0")
            self.btn_prev_doc.setEnabled(False)
            self.btn_next_doc.setEnabled(False)
            return

        self.lbl_doc_info.setText(f"Doc {self.current_doc_index + 1} / {total}")
        self.btn_prev_doc.setEnabled(self.current_doc_index > 0)
        self.btn_next_doc.setEnabled(self.current_doc_index < total - 1)

    def on_prev_doc(self):
        if not self.entry_pdf_paths:
            return
        self.show_doc_by_index(self.current_doc_index - 1)

    def on_next_doc(self):
        if not self.entry_pdf_paths:
            return
        self.show_doc_by_index(self.current_doc_index + 1)

    def on_pdf_context_menu(self, pos):
        menu = QMenu(self)

        act_pal = menu.addAction("Details palettes")
        tour_nrs = self.get_folder_numbers()
        act_pal.setEnabled(bool(tour_nrs))

        menu.addSeparator()

        act_block = menu.addAction("Options de blocage")
        act_block.setEnabled(bool(self.view_pdf_path or self.current_pdf_path))

        chosen = menu.exec(getattr(self.pdf_viewer, "label", self.pdf_viewer).mapToGlobal(pos))
        if chosen == act_pal:
            self.open_pallet_details_dialog()
        elif chosen == act_block:
            self.open_block_options_dialog()

    def open_pallet_details_dialog(self):
        from ui.pallet_details_dialog import PalletDetailsDialog
        tour_nrs = self.get_folder_numbers()
        if not tour_nrs:
            QMessageBox.information(self, "Palettes", "Aucun numéro de dossier renseigné.")
            return

        dlg = PalletDetailsDialog(
            self,
            tour_numbers=tour_nrs,
            tour_repo=self.tour_repo,
            existing_saved=getattr(self, "pallet_details", {}) or {},
        )

        if dlg.exec() != QDialog.Accepted:
            return

        result = dlg.get_result()
        self.pallet_details = result
        self._save_pallet_details_to_json(result)

        QMessageBox.information(self, "Palettes", "Détails palettes sauvegardés.")

    def _current_model_json_path(self) -> str | None:
        if not self.current_pdf_path:
            return None
        base_name = os.path.splitext(os.path.basename(self.current_pdf_path))[0]
        model_dir = MODELS_DIR
        os.makedirs(model_dir, exist_ok=True)
        return os.path.join(model_dir, f"{base_name}.json")

    def _save_pallet_details_to_json(self, pallet_details: dict):
        json_path = self._current_model_json_path()
        if not json_path:
            return

        data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

        data["pallet_details"] = pallet_details

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _read_model_json(self) -> tuple[str | None, dict]:
        json_path = self._current_model_json_path()
        if not json_path:
            return None, {}

        data = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}

        return json_path, data

    def _write_model_json(self, json_path: str, data: dict) -> None:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _get_block_sync_tournrs(self, data: dict | None = None) -> list[str]:
        """Retourne les TourNr à mettre à jour lors d'un changement de blocage."""
        tournrs: list[str] = []

        # Priorité aux lignes actuellement affichées/saisies : l'utilisateur peut
        # poser un blocage avant d'avoir sauvegardé la facture.
        try:
            if hasattr(self, "get_folder_rows"):
                for row in self.get_folder_rows() or []:
                    if isinstance(row, dict):
                        tour_nr = str(row.get("tour_nr") or row.get("TourNr") or row.get("tournr") or "").strip()
                    else:
                        tour_nr = str(row or "").strip()
                    if tour_nr:
                        tournrs.append(tour_nr)
        except Exception:
            pass

        # Fallback JSON déjà chargé.
        if not tournrs and isinstance(data, dict):
            try:
                if hasattr(self, "_extract_tournrs_from_saved"):
                    tournrs.extend(self._extract_tournrs_from_saved(data) or [])
            except Exception:
                pass

        # Dernier fallback : JSON de la facture courante.
        if not tournrs:
            try:
                current_pdf_path = str(getattr(self, "current_pdf_path", "") or "").strip()
                if current_pdf_path and hasattr(self, "_read_saved_invoice_json") and hasattr(self, "_extract_tournrs_from_saved"):
                    saved = self._read_saved_invoice_json(current_pdf_path) or {}
                    tournrs.extend(self._extract_tournrs_from_saved(saved) or [])
            except Exception:
                pass

        return sorted({str(t).strip() for t in tournrs if str(t).strip()})

    def _get_effective_block_state_for_database(self, block_options: dict | None = None, preferred_doc_name: str = "") -> tuple[bool, str]:
        """Calcule l'état de blocage global à écrire en BDD pour la facture.

        Une facture est considérée bloquée dès qu'un document de son groupe porte
        un motif actif. Lorsqu'un motif est retiré d'un document, la BDD n'est
        débloquée que s'il ne reste aucun autre document bloqué.
        """
        block_options = block_options or getattr(self, "block_options", {}) or {}
        if not isinstance(block_options, dict):
            return False, ""

        preferred_doc_name = str(preferred_doc_name or "").strip()
        blocked_items: list[tuple[str, dict]] = []

        if preferred_doc_name:
            current = block_options.get(preferred_doc_name, {}) or {}
            if isinstance(current, dict) and bool(current.get("blocked", False)):
                blocked_items.append((preferred_doc_name, current))

        for name, info in block_options.items():
            name = str(name or "").strip()
            if preferred_doc_name and name == preferred_doc_name:
                continue
            if isinstance(info, dict) and bool(info.get("blocked", False)):
                blocked_items.append((name, info))

        if not blocked_items:
            return False, ""

        comments: list[str] = []
        for _name, info in blocked_items:
            comment = str(info.get("comment") or info.get("reason") or "").strip()
            if comment and comment not in comments:
                comments.append(comment)

        return True, " ; ".join(comments) if comments else "A bloquer"

    def _get_block_mail_metadata(self, preferred_doc_name: str = "") -> dict:
        """Récupère l'expéditeur et l'objet du mail source de la facture.

        Ces informations sont poussées dans XXATourExt lors de la synchronisation
        d'un motif de blocage. Si l'ancien flux n'a pas encore d'entry_id en
        mémoire, on retombe sur le nom du PDF courant.
        """
        meta = {"expediteur": "", "sujet": ""}
        try:
            entry_id = str(getattr(self, "selected_invoice_entry_id", "") or "").strip()
            if not entry_id and hasattr(self, "_resolve_current_entry_id"):
                entry_id = str(self._resolve_current_entry_id(getattr(self, "current_pdf_path", None)) or "").strip()

            nom_pdf = str(preferred_doc_name or "").strip()
            if not nom_pdf:
                current_pdf_path = str(getattr(self, "current_pdf_path", "") or "").strip()
                if current_pdf_path:
                    nom_pdf = os.path.basename(current_pdf_path)

            if hasattr(self, "logmail_repo") and hasattr(self.logmail_repo, "get_mail_info_for_entry_id"):
                row = self.logmail_repo.get_mail_info_for_entry_id(entry_id=entry_id, nom_pdf=nom_pdf) or {}
                meta["expediteur"] = str(row.get("expediteur") or "").strip()
                meta["sujet"] = str(row.get("sujet") or "").strip()
        except Exception:
            pass

        return meta

    def _apply_block_state_to_database(
        self,
        tournrs: list[str],
        *,
        blocked: bool,
        comment: str = "",
        mail_expediteur: str = "",
        mail_objet: str = "",
    ) -> list[str]:
        """Applique immédiatement l'état de blocage dans les tables SQL liées aux tours."""
        errors: list[str] = []
        value = 601 if blocked else 600
        ocr_user = str(getattr(self, "current_username", "") or "").strip()
        mail_expediteur = str(mail_expediteur or "").strip()
        mail_objet = str(mail_objet or "").strip()

        for tour_nr in tournrs or []:
            tour_nr = str(tour_nr or "").strip()
            if not tour_nr:
                continue
            try:
                self.tour_repo.set_infosymbol18_for_tournr(tour_nr, value=value)
                self.tour_repo.set_ocr_user_for_tournr(tour_nr, ocr_user=ocr_user)
                self.tour_repo.set_block_status_for_tournr(
                    tour_nr,
                    is_blocked=bool(blocked),
                    motif=comment,
                    ocr_user=ocr_user,
                    ocr_expediteur=mail_expediteur,
                    ocr_objet=mail_objet,
                )
            except Exception as e:
                errors.append(f"{tour_nr} : {e}")

        return errors

    def _sync_block_options_to_database(self, block_options: dict, *, data: dict | None = None, preferred_doc_name: str = "", show_message: bool = True) -> bool:
        """Synchronise la BDD dès qu'un motif de blocage est ajouté ou retiré."""
        tournrs = self._get_block_sync_tournrs(data)
        blocked, comment = self._get_effective_block_state_for_database(block_options, preferred_doc_name)

        if not tournrs:
            if show_message:
                QMessageBox.warning(
                    self,
                    "Blocage",
                    "Motif enregistré dans le JSON, mais aucun dossier (TourNr) n'a été trouvé pour mettre à jour la BDD.",
                )
            return False

        mail_meta = self._get_block_mail_metadata(preferred_doc_name) if blocked else {"expediteur": "", "sujet": ""}
        errors = self._apply_block_state_to_database(
            tournrs,
            blocked=blocked,
            comment=comment,
            mail_expediteur=mail_meta.get("expediteur", ""),
            mail_objet=mail_meta.get("sujet", ""),
        )
        if errors:
            if show_message:
                QMessageBox.warning(
                    self,
                    "Blocage",
                    "Motif enregistré, mais la mise à jour BDD a échoué pour :\n" + "\n".join(errors),
                )
            return False

        if show_message:
            try:
                state = "bloqué" if blocked else "débloqué"
                self.statusBar().showMessage(f"Blocage BDD synchronisé : {len(tournrs)} dossier(s) {state}.", 3500)
            except Exception:
                pass

        return True

    def open_block_options_dialog(self):
        # doc affiché (facture ou PJ)
        doc_path = self.view_pdf_path or self.current_pdf_path
        if not doc_path:
            return

        doc_name = os.path.basename(doc_path)

        json_path, data = self._read_model_json()
        if not json_path:
            return

        block_options = data.get("block_options", {}) or {}
        current = block_options.get(doc_name, {}) or {}

        dlg = BlockOptionsDialog(
            self,
            document_name=doc_name,
            blocked=bool(current.get("blocked", False)),
            comment=str(current.get("comment", "") or ""),
            reason=str(current.get("reason", "") or ""),
            free_comment=str(current.get("free_comment", "") or ""),
        )

        if dlg.exec() != QDialog.Accepted:
            return

        previous_blocked = bool(current.get("blocked", False))
        previous_comment = str(current.get("comment", "") or "").strip()
        result = dlg.get_result()
        block_changed = (
            previous_blocked != bool(result.get("blocked", False))
            or previous_comment != str(result.get("comment", "") or "").strip()
        )

        block_options[doc_name] = result
        data["block_options"] = block_options
        self._write_model_json(json_path, data)

        # en mémoire aussi (utile si tu veux t’en servir ailleurs)
        self.block_options = block_options

        # Dès qu'un motif est ajouté, retiré ou modifié, on met à jour la BDD
        # sans attendre la validation de la facture.
        if block_changed:
            self._sync_block_options_to_database(
                block_options,
                data=data,
                preferred_doc_name=doc_name,
                show_message=True,
            )

    def on_pdf_table_context_menu(self, pos):
        """Clic-droit sur la liste du haut : rattacher un document à la facture sélectionnée."""

        item = self.pdf_table.itemAt(pos)
        if not item:
            return

        row = item.row()
        it0 = self.pdf_table.item(row, 0)
        if not it0:
            return

        linked_filename = get_left_table_item_filename(it0)
        if not linked_filename:
            return

        menu = QMenu(self)

        pdf_path = it0.data(Qt.UserRole)  # ✅ définir AVANT de l'utiliser

        action_link = menu.addAction("Rattacher ce document à la facture sélectionnée")
        action_attach_cmr = menu.addAction("Rattacher CMR à un dossier…")
        action_attach_cmr.setEnabled(False)  # ✅ maintenant OK

        menu.addSeparator()
        action_move_to_errors = menu.addAction("Déplacer vers les erreurs")
        action_move_to_errors.setEnabled(bool(pdf_path))
        action_delete_permanently = menu.addAction("Supprimer définitivement")
        action_delete_permanently.setEnabled(bool(pdf_path))
        action_fetch_links = menu.addAction("Télécharger documents via liens (CMR)…")
        action_fetch_links.setEnabled(False)
        # --- cible = ligne actuellement sélectionnée (la facture cible)
        target_row = self.pdf_table.currentRow()
        target_filename = None
        target_entry_id = None

        if target_row >= 0:
            it = self.pdf_table.item(target_row, 0)
            if it:
                target_filename = get_left_table_item_filename(it)
                target_entry_id = self.logmail_repo.get_entry_id_for_file(target_filename)

        # fallback: si mémorisé via clic gauche
        if not target_entry_id and self.selected_invoice_filename:
            target_filename = self.selected_invoice_filename
            target_entry_id = self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(target_filename)

        can_link = bool(target_entry_id and target_filename and linked_filename and linked_filename != target_filename)
        action_link.setEnabled(False)
        action_relink = menu.addAction("Rattacher à un Dossier (regrouper avec un autre fichier)…")
        action_relink.setEnabled(bool(pdf_path))

        chosen = menu.exec(self.pdf_table.viewport().mapToGlobal(pos))


        # ✅ IMPORTANT: gérer l'action CMR AVANT le "chosen != action_link"
        if chosen == action_attach_cmr:
            self.attach_cmr_to_dossier_from_right_list(pdf_path, linked_filename)
            return

        if chosen == action_move_to_errors:
            self.move_pdf_to_errors(pdf_path, linked_filename)
            return

        if chosen == action_delete_permanently:
            self.mark_pdf_as_permanently_deleted(pdf_path, linked_filename)
            return
        
        if chosen == action_relink:
            self.relink_left_document_to_other_group(row)
            return

        if chosen != action_link:
            return

        if chosen == action_fetch_links:
            self.fetch_linked_documents_from_pdf(pdf_path, linked_filename)
            return

        if not can_link:
            return

        # ... ici tu continues ton rattachement "document -> facture" existant (entry_id)
        # en utilisant target_filename/target_entry_id


        # cible = ligne actuellement sélectionnée (la facture cible)
        target_row = self.pdf_table.currentRow()
        target_filename = None
        target_entry_id = None

        if target_row >= 0:
            it = self.pdf_table.item(target_row, 0)
            if it:
                target_filename = get_left_table_item_filename(it)
                target_entry_id = self.logmail_repo.get_entry_id_for_file(target_filename)

        # fallback: si tu avais déjà mémorisé une cible via clic gauche
        if not target_entry_id and self.selected_invoice_filename:
            target_filename = self.selected_invoice_filename
            target_entry_id = self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(target_filename)

        can_link = bool(target_entry_id and target_filename and linked_filename and linked_filename != target_filename)
        action_link.setEnabled(can_link)

        # et pour la suite du code, utilise target_filename/target_entry_id au lieu de selected_invoice_*

        action_link.setEnabled(can_link)

        chosen = menu.exec(self.pdf_table.viewport().mapToGlobal(pos))

        if chosen == action_move_to_errors:
            self.move_pdf_to_errors(pdf_path, linked_filename)
            return

        if chosen == action_delete_permanently:
            self.mark_pdf_as_permanently_deleted(pdf_path, linked_filename)
            return

        if chosen == action_attach_cmr:
            self.attach_cmr_to_dossier_from_right_list(pdf_path, linked_filename)
            return
        

        if chosen != action_link:
            return
        


        if not can_link:
            QMessageBox.information(
                self,
                "Rattachement",
                "Sélectionne d'abord une facture (clic gauche) dans la liste du haut.",
            )
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Rattacher un document")
        msg.setText(
            f"Rattacher le fichier :\n\n"
            f"  {linked_filename}\n\n"
            f"à la facture :\n\n"
            f"  {self.selected_invoice_filename}\n\n"
            f"(entry_id = {self.selected_invoice_entry_id})"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() != QMessageBox.Yes:
            return

        try:
            self.logmail_repo.update_entry_for_file(linked_filename, self.selected_invoice_entry_id)
        except Exception as e:
            QMessageBox.critical(self, "Erreur rattachement", str(e))
            return

        # Refresh groupe + liste pièces associées
        current_view = self.view_pdf_path or self.current_pdf_path
        self.build_entry_pdf_group()
        if current_view and current_view in self.entry_pdf_paths:
            self.current_doc_index = self.entry_pdf_paths.index(current_view)
        self.update_doc_indicator()

        QMessageBox.information(self, "Rattachement", "Document rattaché à la facture.")

    def _on_pdf_current_cell_changed(self, currentRow, currentColumn, previousRow, previousColumn):
        if currentRow >= 0:
            self.on_pdf_selected(currentRow, currentColumn)

    def load_default_folder(self):
        """Charge automatiquement le dossier par défaut au démarrage."""
        folder = self.DEFAULT_PDF_FOLDER
        if folder and os.path.isdir(folder):
            self.load_folder(folder)
        else:
            QMessageBox.warning(
                self,
                "Dossier PDF introuvable",
                f"Le dossier PDF par défaut n'existe pas :\n{folder}\n\n"
                "Vous pouvez en choisir un autre via : 'Analyser un dossier'."
            )


    def load_folder(self, folder: str):
        if not folder or not os.path.isdir(folder):
            return

        self.current_folder_path = folder

        if hasattr(self, "refresh_last_mail_date_label"):
            try:
                self.refresh_last_mail_date_label()
            except Exception:
                pass

        current_search_query = self._get_left_search_query()
        self._loaded_left_search_query = current_search_query
        date_mail_from, date_mail_to = self._get_left_mail_date_filter_range()
        self._loaded_left_mail_date_filter = (date_mail_from, date_mail_to)

        mode = str(getattr(self, "left_filter_mode", "pending") or "pending").strip().lower()
        if mode == "errors":
            sql_status = "error"
        elif mode == "ecart":
            sql_status = "ecart"
        else:
            sql_status = mode

        from app.settings import load_settings, get_ui_value
        settings = load_settings()
        max_pages_pending = int(get_ui_value(settings, "max_pages_pending", 100))
        max_pages_error = int(get_ui_value(settings, "max_pages_error", 50))
        max_pages_validated = int(get_ui_value(settings, "max_pages_validated", 200))
        max_pages_ecart = int(get_ui_value(settings, "max_pages_ecart", max_pages_error))

        if sql_status == "pending":
            display_limit = max_pages_pending
        elif sql_status == "error":
            display_limit = max_pages_error
        elif sql_status == "validated":
            display_limit = max_pages_validated
        elif sql_status == "ecart":
            display_limit = max_pages_ecart
        else:
            display_limit = None

        try:
            display_limit = int(display_limit) if display_limit else None
        except Exception:
            display_limit = None

        rows_to_add = []
        lkz_cache: dict[str, str] = {}
        seen_entry_ids: set[str] = set()

        def _split_index_tours(value) -> list[str]:
            if not value:
                return []
            if isinstance(value, (list, tuple, set)):
                raw = list(value)
            else:
                raw = re.split(r"[\s,;|]+", str(value or ""))
            out = []
            for t in raw:
                t = str(t or "").strip()
                if t and t not in out:
                    out.append(t)
            return out

        def _append_rows_from_sql(rows: list[dict]) -> None:
            nonlocal rows_to_add
            if not rows:
                return

            entry_ids = [
                str(r.get("entry_id") or "").strip()
                for r in rows
                if str(r.get("entry_id") or "").strip()
            ]
            entry_ids = [e for i, e in enumerate(entry_ids) if e and e not in entry_ids[:i]]

            try:
                files_by_entry = self.logmail_repo.get_files_for_entries(entry_ids) or {}
            except Exception:
                files_by_entry = {}

            try:
                index_by_entry = self.logmail_repo.get_search_index_rows_for_entries(entry_ids) or {}
            except Exception:
                index_by_entry = {}

            for r in rows:
                if display_limit and len(rows_to_add) >= display_limit:
                    break

                entry_id = str(r.get("entry_id") or "").strip()
                if not entry_id or entry_id in seen_entry_ids:
                    continue
                seen_entry_ids.add(entry_id)

                stored_filename = str(r.get("nom_pdf") or "").strip()
                if not stored_filename:
                    continue

                files = files_by_entry.get(entry_id) or []

                candidate_names = []
                if stored_filename:
                    candidate_names.append(stored_filename)

                for f in files:
                    name = str(f.get("nom_pdf") or "").strip()
                    if name and name not in candidate_names:
                        candidate_names.append(name)

                group_paths = []
                for name in candidate_names:
                    p = os.path.join(folder, name)
                    if not os.path.exists(p):
                        continue
                    try:
                        if not is_supported_document(p):
                            continue
                    except Exception:
                        continue
                    if p not in group_paths:
                        group_paths.append(p)

                if not group_paths:
                    continue

                rep_path = group_paths[0]
                try:
                    chosen_rep = ""
                    if hasattr(self, "_choose_representative_pdf"):
                        chosen_rep = str(self._choose_representative_pdf(group_paths) or "").strip()
                    if chosen_rep and os.path.exists(chosen_rep):
                        rep_path = chosen_rep
                except Exception:
                    pass

                idx = index_by_entry.get(entry_id) or {}
                indexed_tours = _split_index_tours(idx.get("tour_numbers"))
                transporter_kundennr = str(idx.get("transporter_kundennr") or "").strip()
                transporter_name = str(idx.get("transporter_name") or r.get("transporter_name") or "").strip()
                date_mail = r.get("date_mail") or idx.get("date_mail")
                expediteur = str(r.get("expediteur") or idx.get("expediteur") or "").strip()

                rows_to_add.append(
                    (
                        format_left_table_filename(os.path.basename(rep_path)),
                        rep_path,
                        entry_id,
                        group_paths,
                        (
                            "ecart"
                            if str(r.get("processing_status") or "pending").strip().lower() == "eccarts"
                            else str(r.get("processing_status") or "pending").strip().lower()
                        ),
                        str(r.get("invoice_date") or "").strip(),
                        str(r.get("iban") or "").strip(),
                        str(r.get("bic") or "").strip(),
                        date_mail,
                        expediteur,
                        str(r.get("sujet") or "").strip(),
                        transporter_name,
                        indexed_tours,
                        transporter_kundennr,
                    )
                )

        try:
            # Recherche : on privilégie l'index SQL. Si l'index n'est pas encore
            # disponible ou ne trouve rien, l'ancien chemin SQL/JSON reste en fallback.
            used_index_for_search = False
            if current_search_query:
                try:
                    idx_limit = max(1000, (display_limit or 200) * 8)
                    index_entry_ids = self.logmail_repo.search_entry_ids_in_index(
                        current_search_query,
                        status=sql_status,
                        limit=idx_limit,
                        date_mail_from=date_mail_from,
                        date_mail_to=date_mail_to,
                    ) or []
                except Exception as e:
                    print(f"⚠️ Erreur recherche XXA_OCR_SEARCH_INDEX: {e}")
                    index_entry_ids = []

                if index_entry_ids:
                    used_index_for_search = True
                    chunk_size = 200
                    for i in range(0, len(index_entry_ids), chunk_size):
                        if display_limit and len(rows_to_add) >= display_limit:
                            break
                        chunk = index_entry_ids[i:i + chunk_size]
                        _append_rows_from_sql(
                            self.logmail_repo.get_document_rows_for_entries(
                                chunk,
                                status=sql_status,
                                date_mail_from=date_mail_from,
                                date_mail_to=date_mail_to,
                            ) or []
                        )

            if not current_search_query or not used_index_for_search:
                fetch_size = max(250, (display_limit or 100) * 4)
                offset = 0
                while True:
                    if display_limit and len(rows_to_add) >= display_limit:
                        break
                    rows_page = self.logmail_repo.get_document_rows_for_folder_page(
                        folder,
                        sql_status,
                        offset=offset,
                        fetch=fetch_size,
                        search_query=current_search_query or None,
                        date_mail_from=date_mail_from,
                        date_mail_to=date_mail_to,
                    ) or []
                    if not rows_page:
                        break
                    offset += len(rows_page)
                    _append_rows_from_sql(rows_page)
                    # Sans limite UI, on garde une pagination mais on s'arrête quand SQL n'a plus rien.
                    if len(rows_page) < fetch_size:
                        break

            # Fallback rétrocompatible pour les anciens JSON non encore indexés,
            # uniquement si la recherche ressemble à un numéro de dossier.
            if (
                current_search_query
                and self._is_folder_like_left_search_query(current_search_query)
                and (not display_limit or len(rows_to_add) < display_limit)
            ):
                try:
                    extra_entry_matches = self._find_additional_left_search_entry_matches(
                        folder,
                        current_search_query,
                        sql_status,
                    )
                except Exception:
                    extra_entry_matches = {}

                missing_entry_ids = [
                    entry_id
                    for entry_id in (extra_entry_matches or {}).keys()
                    if entry_id and entry_id not in seen_entry_ids
                ]
                if missing_entry_ids:
                    _append_rows_from_sql(
                        self.logmail_repo.get_document_rows_for_entries(
                            missing_entry_ids,
                            status=sql_status,
                            date_mail_from=date_mail_from,
                            date_mail_to=date_mail_to,
                        ) or []
                    )
        except Exception as e:
            QMessageBox.warning(self, "Chargement dossier", f"Erreur lecture XXA_LOGMAIL_228794 :\n{e}")
            return

        table = self.pdf_table
        old_updates = table.updatesEnabled()
        table.setUpdatesEnabled(False)
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            table.setRowCount(len(rows_to_add))

            for row_index, (rep_filename, rep_path, entry_id, group_paths, status, invoice_date, iban, bic, date_mail, expediteur, sujet, transporter_name, indexed_tours, transporter_kundennr) in enumerate(rows_to_add):
                real_filename = os.path.basename(rep_path)
                display_filename = format_left_table_filename(real_filename)
                it0 = QTableWidgetItem(display_filename)
                it0.setToolTip(real_filename)
                it0.setData(Qt.UserRole, rep_path)
                it0.setData(Qt.UserRole + 6, real_filename)
                it0.setData(Qt.UserRole + 1, status)
                it0.setData(Qt.UserRole + 4, entry_id)
                it0.setData(Qt.UserRole + 5, group_paths)
                it0.setData(Qt.UserRole + 8, self._normalize_left_mail_date_value(date_mail))

                # Les numéros de dossier viennent d'abord de l'index SQL. On ne
                # relit le JSON que pour les anciennes lignes non encore indexées.
                folders_for_search = list(indexed_tours or [])
                if not folders_for_search:
                    folders_for_search = self._get_saved_folder_numbers_for_pdf(rep_path)

                extra_search_values = list(folders_for_search or [])
                try:
                    if date_mail:
                        extra_search_values.append(str(date_mail))
                        if hasattr(date_mail, "strftime"):
                            extra_search_values.append(date_mail.strftime("%d/%m/%Y %H:%M"))
                    if expediteur:
                        extra_search_values.append(expediteur)
                    if sujet:
                        extra_search_values.append(sujet)
                    if transporter_name:
                        extra_search_values.append(transporter_name)
                except Exception:
                    pass
                it0.setData(Qt.UserRole + 7, extra_search_values)
                it0.setData(Qt.UserRole + 9, folders_for_search)
                it0.setData(Qt.UserRole + 10, transporter_kundennr)

                tooltip_parts = [real_filename]
                if folders_for_search:
                    folders_txt = "Dossier(s) : " + ", ".join(folders_for_search[:10])
                    if len(folders_for_search) > 10:
                        folders_txt += "…"
                    tooltip_parts.append(folders_txt)
                if date_mail:
                    tooltip_parts.append(f"Date mail : {date_mail}")
                if expediteur:
                    tooltip_parts.append(f"Expéditeur : {expediteur}")
                if transporter_name:
                    tooltip_parts.append(f"Transporteur : {transporter_name}")
                if len(tooltip_parts) > 1:
                    it0.setToolTip("\n".join(tooltip_parts))

                table.setItem(row_index, 0, it0)
                table.setItem(row_index, 1, QTableWidgetItem(invoice_date))
                table.setItem(row_index, 2, QTableWidgetItem(iban))
                table.setItem(row_index, 3, QTableWidgetItem(bic))

                if not invoice_date or not iban or not bic:
                    j_date, j_iban, j_bic = self._get_saved_date_iban_bic_for_pdf(rep_path)
                    new_date = invoice_date or j_date
                    new_iban = iban or j_iban
                    new_bic = bic or j_bic

                    if new_date and not invoice_date:
                        table.item(row_index, 1).setText(new_date)
                    if new_iban and not iban:
                        table.item(row_index, 2).setText(new_iban)
                    if new_bic and not bic:
                        table.item(row_index, 3).setText(new_bic)

                    try:
                        if entry_id and (new_date or new_iban or new_bic):
                            self.logmail_repo.update_document_metadata_for_entry(
                                entry_id,
                                invoice_date=new_date,
                                iban=new_iban,
                                bic=new_bic,
                                status=None,
                            )
                    except Exception:
                        pass

                    invoice_date, iban, bic = new_date, new_iban, new_bic

                lkz = ""
                if transporter_kundennr:
                    try:
                        if transporter_kundennr not in lkz_cache:
                            lkz_cache[transporter_kundennr] = str(
                                self.transporter_repo.get_lkz_by_kundennr(transporter_kundennr) or ""
                            ).strip()
                        lkz = lkz_cache.get(transporter_kundennr, "")
                    except Exception:
                        lkz = ""
                if not lkz:
                    lkz = self._get_country_for_document(rep_path, iban, bic)
                table.setItem(row_index, 4, QTableWidgetItem(lkz))
        finally:
            table.blockSignals(False)
            table.setUpdatesEnabled(old_updates)

        self.refresh_left_table_processing_states()
        self.refresh_left_table_processing_claims()
        self.apply_left_table_search_filter()


    def _get_saved_json_path(self, pdf_path: str) -> str:
        file_name = os.path.basename(str(pdf_path or "").strip())
        base_name, _ = os.path.splitext(file_name)

        # Cas courant moderne : le nom contient déjà un suffixe unique du type
        # <nom>___<entry_id>.pdf. Inutile d'aller relire la BDD pour reconstruire
        # un autre nom JSON.
        if re.search(r"___\d+$", base_name):
            model_dir = MODELS_DIR
            return os.path.join(model_dir, f"{base_name}.json")

        # sécurité supplémentaire : si le fichier n'a pas encore de préfixe
        # mais qu'on connaît déjà l'entry_id courant, on l'utilise sans requête SQL.
        if ENTRY_FILE_SEPARATOR not in file_name:
            entry_id = str(getattr(self, "selected_invoice_entry_id", "") or "").strip()
            if not entry_id:
                try:
                    entry_id = str(
                        self.logmail_repo.get_entry_id_for_file(file_name) or ""
                    ).strip()
                except Exception:
                    entry_id = ""

            if entry_id:
                base_name = f"{entry_id}{ENTRY_FILE_SEPARATOR}{base_name}"

        model_dir = MODELS_DIR
        return os.path.join(model_dir, f"{base_name}.json")


    def _get_saved_json_path_for_pdf(self, pdf_path: str) -> str:
        """Compat.

        Avant la refacto, certains appels utilisaient une version "simple" du nom
        (sans préfixe entry_id). Or les JSON sont maintenant nommés avec
        `entry_id__<nom_fichier>.json`.

        👉 On délègue donc à _get_saved_json_path() qui gère le préfixe.
        """
        return self._get_saved_json_path(pdf_path)


    def _read_cached_json_file(self, json_path: str) -> dict:
        json_path = str(json_path or "").strip()
        if not json_path or not os.path.exists(json_path):
            return {}

        try:
            stat = os.stat(json_path)
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except Exception:
            signature = None

        cache = getattr(self, "_saved_json_cache", None)
        if cache is None:
            cache = {}
            self._saved_json_cache = cache

        cached = cache.get(json_path)
        if cached and cached.get("signature") == signature:
            data = cached.get("data")
            return data if isinstance(data, dict) else {}

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}

        if not isinstance(data, dict):
            data = {}

        cache[json_path] = {
            "signature": signature,
            "data": data,
        }
        return data


    def _get_saved_date_iban_bic_for_pdf(self, pdf_path: str) -> tuple[str, str, str]:
        # IMPORTANT: utiliser la version "préfixée" (entry_id__) si nécessaire.
        json_path = self._get_saved_json_path(pdf_path)
        data = self._read_cached_json_file(json_path)
        if not data:
            return ("", "", "")

        return (
            str(data.get("invoice_date", "")).strip(),
            str(data.get("iban", "")).strip(),
            str(data.get("bic", "")).strip(),
        )

    

    def _update_left_table_date_iban_bic(self, pdf_path: str, invoice_date: str, iban: str, bic: str):
        """Met à jour en temps réel Date / IBAN / BIC / Pays du tableau de gauche pour un PDF."""
        if not pdf_path:
            return
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        if self.pdf_table.columnCount() < 5:
            return

        invoice_date = (invoice_date or "").strip()
        iban = (iban or "").strip()
        bic = (bic or "").strip()

        # Pays (LKZ) : uniquement depuis le transporteur du dossier.
        lkz = ""
        try:
            if getattr(self, "selected_kundennr", None):
                lkz = str(self.transporter_repo.get_lkz_by_kundennr(str(self.selected_kundennr)) or "").strip()
        except Exception:
            lkz = ""

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue
            p = it0.data(Qt.UserRole)
            if p == pdf_path:
                self.pdf_table.setItem(row, 1, QTableWidgetItem(invoice_date))
                self.pdf_table.setItem(row, 2, QTableWidgetItem(iban))
                self.pdf_table.setItem(row, 3, QTableWidgetItem(bic))
                self.pdf_table.setItem(row, 4, QTableWidgetItem(lkz))
                return


    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_autoload_default_folder:
            self._did_autoload_default_folder = True
            self.load_default_folder()

    def refresh_left_table_saved_infos(self):
        """Recharge IBAN/BIC pour chaque PDF de la table."""
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        if self.pdf_table.columnCount() < 4:
            return

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            pdf_path = it0.data(Qt.UserRole)
            if not pdf_path:
                continue

            invoice_date, iban, bic = self._get_saved_date_iban_bic_for_pdf(pdf_path)

            it1 = self.pdf_table.item(row, 1)
            if it1 is None:
                self.pdf_table.setItem(row, 1, QTableWidgetItem(invoice_date))
            else:
                it1.setText(invoice_date)

            it2 = self.pdf_table.item(row, 2)
            if it2 is None:
                self.pdf_table.setItem(row, 2, QTableWidgetItem(iban))
            else:
                it2.setText(iban)

            it3 = self.pdf_table.item(row, 3)
            if it3 is None:
                self.pdf_table.setItem(row, 3, QTableWidgetItem(bic))
            else:
                it3.setText(bic)

            lkz = self._get_country_for_document(pdf_path, iban, bic)
            it4 = self.pdf_table.item(row, 4)
            if it4 is None:
                self.pdf_table.setItem(row, 4, QTableWidgetItem(lkz))
            else:
                it4.setText(lkz)
        self.apply_left_table_search_filter()

    def _get_saved_folder_numbers_for_pdf(self, pdf_path: str) -> list[str]:
        pdf_path = str(pdf_path or "").strip()
        if not pdf_path:
            return []

        json_path = self._get_saved_json_path(pdf_path)
        data = self._read_cached_json_file(json_path)

        tournrs: list[str] = []
        if isinstance(data, dict):
            try:
                if hasattr(self, "_extract_tournrs_from_saved"):
                    tournrs.extend(self._extract_tournrs_from_saved(data) or [])
            except Exception:
                pass

            for key in ("cmr_tour_nr", "ecart_tour_nr", "tour_nr", "TourNr", "tournr"):
                val = str(data.get(key) or "").strip()
                if val:
                    tournrs.append(val)

        return sorted({str(t).strip() for t in tournrs if str(t).strip()})

    def _is_folder_like_left_search_query(self, search_query: str) -> bool:
        raw_query = str(search_query or "").strip()
        if not raw_query:
            return False

        compact_query = (
            raw_query
            .replace(" ", "")
            .replace("\u00A0", "")
            .replace("-", "")
        )
        if len(compact_query) < 5:
            return False

        if compact_query.isdigit():
            return True

        try:
            return bool(self.DOSSIER_PATTERN.search(raw_query))
        except Exception:
            return False

    def _get_left_search_folder_index(self, folder: str) -> dict:
        folder = str(folder or "").strip()
        if not folder or not os.path.isdir(folder):
            return {"entry_to_tournrs": {}, "statuses": {}}

        cache_by_folder = getattr(self, "_left_search_folder_index_cache", None)
        if cache_by_folder is None:
            cache_by_folder = {}
            self._left_search_folder_index_cache = cache_by_folder

        cached = cache_by_folder.get(folder)
        if cached:
            return cached

        try:
            filenames = sorted(os.listdir(folder))
        except Exception:
            filenames = []

        supported_filenames: list[str] = []
        paths_by_name: dict[str, str] = {}
        for name in filenames:
            full_path = os.path.join(folder, name)
            if not os.path.isfile(full_path):
                continue
            try:
                if not is_supported_document(full_path):
                    continue
            except Exception:
                continue
            supported_filenames.append(name)
            paths_by_name[name] = full_path

        entry_to_tournrs: dict[str, set[str]] = {}
        statuses: dict[str, str] = {}

        if supported_filenames:
            try:
                entry_ids_by_name = self.logmail_repo.get_entry_ids_for_files(supported_filenames) or {}
            except Exception:
                entry_ids_by_name = {}

            entry_ids = sorted({str(v or "").strip() for v in entry_ids_by_name.values() if str(v or "").strip()})
            if entry_ids:
                try:
                    statuses = self.logmail_repo.get_processing_status_map_for_entries(entry_ids) or {}
                except Exception:
                    statuses = {}

            for name, pdf_path in paths_by_name.items():
                entry_id = str(entry_ids_by_name.get(name) or "").strip()
                if not entry_id:
                    continue
                try:
                    tournrs = self._get_saved_folder_numbers_for_pdf(pdf_path)
                except Exception:
                    tournrs = []
                if not tournrs:
                    continue
                entry_to_tournrs.setdefault(entry_id, set()).update(
                    str(t).strip() for t in tournrs if str(t).strip()
                )

        cache_payload = {
            "entry_to_tournrs": entry_to_tournrs,
            "statuses": statuses,
        }
        cache_by_folder[folder] = cache_payload
        return cache_payload

    def _find_additional_left_search_entry_matches(self, folder: str, search_query: str, status: str) -> dict[str, set[str]]:
        """
        Recherche des entry_id supplémentaires hors pool déjà chargé, à partir des
        JSON sauvegardés, pour permettre la recherche par numéro de dossier.
        Retourne {entry_id: {tour1, tour2, ...}}.
        """
        folder = str(folder or "").strip()
        query = str(search_query or "").strip().lower()
        normalized_status = str(status or "pending").strip().lower()
        if normalized_status == "eccarts":
            normalized_status = "ecart"

        if not folder or not os.path.isdir(folder) or not query:
            return {}

        index_data = self._get_left_search_folder_index(folder)
        entry_to_tournrs = index_data.get("entry_to_tournrs") or {}
        statuses = index_data.get("statuses") or {}

        matches: dict[str, set[str]] = {}
        for entry_id, tournrs in entry_to_tournrs.items():
            clean_entry_id = str(entry_id or "").strip()
            if not clean_entry_id:
                continue

            entry_status = str(statuses.get(clean_entry_id) or "pending").strip().lower()
            if entry_status == "eccarts":
                entry_status = "ecart"
            if normalized_status and entry_status != normalized_status:
                continue

            matched_tournrs = {
                str(t).strip()
                for t in (tournrs or [])
                if str(t).strip() and query in str(t).strip().lower()
            }
            if matched_tournrs:
                matches[clean_entry_id] = matched_tournrs

        return matches

    def _is_typing_in_input(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))



    def _get_country_for_bank(self, iban: str | None, bic: str | None) -> str:
        """Retourne le LKZ du transporteur à partir d'un couple IBAN/BIC (avec cache)."""
        iban = str(iban or "").strip()
        bic = str(bic or "").strip()
        if not iban or not bic:
            return ""

        cache = getattr(self, "_lkz_cache", None)
        if cache is None:
            cache = {}
            self._lkz_cache = cache

        key = (iban, bic)
        if key in cache:
            return cache.get(key) or ""

        lkz = ""
        try:
            rec = self.transporter_repo.find_transporter_by_bank(iban, bic)
            if rec:
                lkz = str(rec.get("LKZ") or rec.get("lkz") or "").strip()
        except Exception:
            lkz = ""

        cache[key] = lkz
        return lkz
    

    def _normalize_left_mail_date_value(self, value) -> str:
        """Retourne la date mail au format YYYY-MM-DD pour le filtre local UI."""
        if not value:
            return ""
        try:
            if hasattr(value, "strftime"):
                return value.strftime("%Y-%m-%d")
        except Exception:
            pass

        raw = str(value or "").strip()
        if not raw:
            return ""

        # Formats SQL courants : 2026-06-13, 2026-06-13 07:18:09
        m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

        # Format affiché : 13/06/2026 ou 13.06.2026
        m = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", raw)
        if m:
            return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

        return ""

    def _get_left_mail_date_filter_range(self):
        """Retourne (début inclus, fin exclue) pour le filtre date_mail SQL."""
        cb = getattr(self, "left_mail_date_filter_checkbox", None)
        date_edit = getattr(self, "left_mail_date_filter_date", None)
        try:
            if cb is None or date_edit is None or not cb.isChecked():
                return None, None
            qdate = date_edit.date()
            if not qdate or not qdate.isValid():
                return None, None
            start = qdate.toString("yyyy-MM-dd") + " 00:00:00"
            end = qdate.addDays(1).toString("yyyy-MM-dd") + " 00:00:00"
            return start, end
        except Exception:
            return None, None

    def _get_left_mail_date_filter_day(self) -> str:
        cb = getattr(self, "left_mail_date_filter_checkbox", None)
        date_edit = getattr(self, "left_mail_date_filter_date", None)
        try:
            if cb is None or date_edit is None or not cb.isChecked():
                return ""
            qdate = date_edit.date()
            if not qdate or not qdate.isValid():
                return ""
            return qdate.toString("yyyy-MM-dd")
        except Exception:
            return ""

    def on_left_mail_date_filter_changed(self, *_args):
        date_edit = getattr(self, "left_mail_date_filter_date", None)
        cb = getattr(self, "left_mail_date_filter_checkbox", None)
        try:
            if date_edit is not None and cb is not None:
                date_edit.setEnabled(cb.isChecked())
        except Exception:
            pass
        self._schedule_left_table_reload()

    def clear_left_mail_date_filter(self):
        cb = getattr(self, "left_mail_date_filter_checkbox", None)
        try:
            if cb is not None:
                cb.setChecked(False)
        except Exception:
            pass
        self._schedule_left_table_reload()

    def _schedule_left_table_reload(self, delay_ms: int = 250):
        timer = getattr(self, "_left_search_reload_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._reload_left_table_for_search)
            self._left_search_reload_timer = timer
        timer.start(max(0, int(delay_ms or 0)))

    def _get_left_search_query(self) -> str:
        widget = getattr(self, "left_search_input", None)
        if widget is None:
            return ""
        return str(widget.text() or "").strip()

    def on_left_search_text_changed(self, _text: str):
        """Relance le chargement depuis SQL pour chercher aussi dans les lignes non chargées."""
        self._schedule_left_table_reload(400)

    def _reload_left_table_for_search(self):
        current_folder = str(getattr(self, "current_folder_path", "") or "").strip()
        if current_folder and os.path.isdir(current_folder):
            self.load_folder(current_folder)
        else:
            self.apply_left_table_search_filter()

    def apply_left_table_search_filter(self):
        """Filtre combiné (statut + recherche globale + filtre pays)."""
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        mode = str(getattr(self, "left_filter_mode", "pending") or "pending").strip().lower()
        query = (getattr(self, "left_search_input", None).text() if getattr(self, "left_search_input", None) else "")
        query = (query or "").strip().lower()

        country_q = (getattr(self, "left_country_filter_input", None).text() if getattr(self, "left_country_filter_input", None) else "")
        country_q = (country_q or "").strip().lower()

        mail_date_day = self._get_left_mail_date_filter_day()

        cols_count = self.pdf_table.columnCount()

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                self.pdf_table.setRowHidden(row, True)
                continue

            status = str(it0.data(Qt.UserRole + 1) or "pending").strip().lower()
            if status == "eccarts":
                status = "ecart"

            # 1) filtre statut
            if mode == "pending":
                status_visible = (status == "pending")
            elif mode == "validated":
                status_visible = (status == "validated")
            elif mode == "errors":
                status_visible = (status == "error")
            elif mode == "ecart":
                status_visible = (status == "ecart")
            else:
                status_visible = True

            # 2) filtre recherche globale
            values = []
            for col in range(cols_count):
                it = self.pdf_table.item(row, col)
                if it:
                    values.append((it.text() or "").strip().lower())
            extra_search_values = []
            try:
                extra_search_values = [
                    str(v).strip().lower()
                    for v in (it0.data(Qt.UserRole + 7) or [])
                    if str(v).strip()
                ]
            except Exception:
                extra_search_values = []

            haystack = " | ".join(values + extra_search_values)
            loaded_query = str(getattr(self, "_loaded_left_search_query", "") or "").strip().lower()
            # Quand la table vient d'être rechargée par une recherche SQL/index,
            # les lignes affichées sont déjà les bons résultats. On ne les
            # masque donc pas si le match vient d'un champ non visible
            # (date_mail, expéditeur, transporteur, etc.).
            search_visible = (not query) or (query == loaded_query) or (query in haystack)

            # 3) filtre date mail local, utile quand le tableau est déjà chargé
            if mail_date_day:
                try:
                    row_mail_day = str(it0.data(Qt.UserRole + 8) or "").strip()
                except Exception:
                    row_mail_day = ""
                mail_date_visible = (row_mail_day == mail_date_day)
            else:
                mail_date_visible = True

            # 4) filtre pays (col 4)
            if country_q and cols_count >= 5:
                it = self.pdf_table.item(row, 4)
                lkz_txt = str(it.text() if it else "").strip().lower()
                country_visible = lkz_txt.startswith(country_q)
            else:
                country_visible = True

            self.pdf_table.setRowHidden(row, not (status_visible and search_visible and mail_date_visible and country_visible))


    def _get_country_for_document(self, pdf_path: str, iban: str | None, bic: str | None) -> str:
        """Pays (LKZ) affiché dans la liste de gauche.

        Le pays suit la nouvelle règle transporteur : on part du KundenNr du
        premier dossier sauvegardé. IBAN/BIC ne servent plus à déterminer le
        transporteur ni le pays.
        """
        kundennr = ""
        try:
            data = self._read_saved_invoice_json(pdf_path) or {}
            kundennr = str(data.get("transporter_kundennr") or data.get("selected_kundennr") or "").strip()
            if not kundennr:
                folders = data.get("folders") or []
                first_tour = ""
                if isinstance(folders, list):
                    for row in folders:
                        if isinstance(row, dict):
                            first_tour = str(row.get("tour_nr") or "").strip()
                        else:
                            first_tour = str(row or "").strip()
                        if first_tour:
                            break
                if not first_tour:
                    folder_numbers = data.get("folder_numbers") or []
                    if isinstance(folder_numbers, list) and folder_numbers:
                        first_tour = str(folder_numbers[0] or "").strip()
                    else:
                        first_tour = str(data.get("folder_number") or "").strip()
                if first_tour:
                    kundennr = str(self.tour_repo.get_ffnr_for_tour(first_tour) or "").strip()

            if kundennr:
                return str(self.transporter_repo.get_lkz_by_kundennr(kundennr) or "").strip()
        except Exception:
            pass

        return ""

    def _update_left_row_for_entry(self, entry_id: str, invoice_date: str, iban: str, bic: str, country: str = ""):
        """Met à jour la ligne 'groupe' (1 ligne par entry_id) dans la table de gauche."""
        if not entry_id or not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        entry_id = str(entry_id).strip()

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            row_entry_id = str(it0.data(Qt.UserRole + 4) or "").strip()  # ✅ entry_id stocké dans la table
            if row_entry_id != entry_id:
                continue

            # Cols existantes: 0 Nom | 1 Date | 2 IBAN | 3 BIC | (4 Pays si tu l'as)
            if self.pdf_table.columnCount() >= 2:
                self.pdf_table.setItem(row, 1, QTableWidgetItem((invoice_date or "").strip()))
            if self.pdf_table.columnCount() >= 3:
                self.pdf_table.setItem(row, 2, QTableWidgetItem((iban or "").strip()))
            if self.pdf_table.columnCount() >= 4:
                self.pdf_table.setItem(row, 3, QTableWidgetItem((bic or "").strip()))
            if self.pdf_table.columnCount() >= 5:
                self.pdf_table.setItem(row, 4, QTableWidgetItem((country or "").strip()))

            return