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

        # s’assurer que la facture est dans la liste + en premier
        if invoice_path in paths:
            paths.remove(invoice_path)
        paths.insert(0, invoice_path)

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

        block_options[doc_name] = dlg.get_result()
        data["block_options"] = block_options
        self._write_model_json(json_path, data)

        # en mémoire aussi (utile si tu veux t’en servir ailleurs)
        self.block_options = block_options

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
        action_delete = menu.addAction("Supprimer")
        action_delete.setEnabled(bool(pdf_path))
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

        if chosen == action_delete:
            self.mark_pdf_as_deleted(pdf_path, linked_filename)
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

        if chosen == action_delete:
            self.mark_pdf_as_deleted(pdf_path, linked_filename)
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
        self.pdf_table.setRowCount(0)

        mode = str(getattr(self, "left_filter_mode", "pending") or "pending").strip().lower()
        if mode == "errors":
            sql_status = "error"
        else:
            sql_status = mode

        # Charger les paramètres UI depuis settings
        from app.settings import load_settings, get_ui_value
        settings = load_settings()
        max_pages_pending = int(get_ui_value(settings, "max_pages_pending", 100))
        max_pages_error = int(get_ui_value(settings, "max_pages_error", 50))
        max_pages_validated = int(get_ui_value(settings, "max_pages_validated", 200))

        # Déterminer la limite selon le mode
        if sql_status == "pending":
            limit = max_pages_pending
        elif sql_status == "error":
            limit = max_pages_error
        elif sql_status == "validated":
            limit = max_pages_validated
        else:
            limit = None

        try:
            rows = self.logmail_repo.get_document_rows_for_folder(folder, sql_status, limit=limit)
        except Exception as e:
            QMessageBox.warning(self, "Chargement dossier", f"Erreur lecture XXA_LOGMAIL_228794 :\n{e}")
            return

        rows_to_add = []

        entry_ids = [str(r.get("entry_id") or "").strip() for r in rows if str(r.get("entry_id") or "").strip()]
        try:
            files_by_entry = self.logmail_repo.get_files_for_entries(entry_ids) or {}
        except Exception:
            files_by_entry = {}

        for r in rows:
            entry_id = str(r.get("entry_id") or "").strip()

            stored_filename = str(r.get("nom_pdf") or "").strip()
            if not stored_filename:
                continue

            rep_path = os.path.join(folder, stored_filename)
            if not os.path.exists(rep_path):
                continue

            display_filename = format_left_table_filename(stored_filename)

            try:
                if not is_supported_document(rep_path):
                    continue
            except Exception:
                pass

            files = files_by_entry.get(entry_id) or []

            group_paths = []
            for f in files:
                name = str(f.get("nom_pdf") or "").strip()
                if not name:
                    continue
                p = os.path.join(folder, name)
                if os.path.exists(p):
                    group_paths.append(p)

            if not group_paths:
                group_paths = [rep_path]

            rows_to_add.append(
                (
                    display_filename,
                    rep_path,
                    entry_id,
                    group_paths,
                    str(r.get("processing_status") or "pending").strip().lower(),
                    str(r.get("invoice_date") or "").strip(),
                    str(r.get("iban") or "").strip(),
                    str(r.get("bic") or "").strip(),
                )
            )

        self.pdf_table.setRowCount(len(rows_to_add))

        for row_index, (rep_filename, rep_path, entry_id, group_paths, status, invoice_date, iban, bic) in enumerate(rows_to_add):
            real_filename = os.path.basename(rep_path)
            display_filename = format_left_table_filename(real_filename)
            it0 = QTableWidgetItem(display_filename)
            it0.setToolTip(real_filename)
            it0.setData(Qt.UserRole, rep_path)
            it0.setData(Qt.UserRole + 6, real_filename)
            it0.setData(Qt.UserRole + 1, status)
            it0.setData(Qt.UserRole + 4, entry_id)
            it0.setData(Qt.UserRole + 5, group_paths)

            self.pdf_table.setItem(row_index, 0, it0)
            self.pdf_table.setItem(row_index, 1, QTableWidgetItem(invoice_date))
            self.pdf_table.setItem(row_index, 2, QTableWidgetItem(iban))
            self.pdf_table.setItem(row_index, 3, QTableWidgetItem(bic))            

            # ✅ Fallback JSON (si la table XXA_LOGMAIL ne stocke pas encore IBAN/BIC/date)
            # -> On affiche immédiatement ces valeurs dans la liste de gauche
            # -> Et on les "rattrape" en BDD pour ne plus dépendre du JSON au prochain démarrage.
            if not invoice_date or not iban or not bic:
                j_date, j_iban, j_bic = self._get_saved_date_iban_bic_for_pdf(rep_path)

                new_date = invoice_date or j_date
                new_iban = iban or j_iban
                new_bic  = bic or j_bic

                # mise à jour UI (sinon tu ne vois rien tant que tu ne cliques pas la ligne)
                if new_date and not invoice_date:
                    self.pdf_table.item(row_index, 1).setText(new_date)
                if new_iban and not iban:
                    self.pdf_table.item(row_index, 2).setText(new_iban)
                if new_bic and not bic:
                    self.pdf_table.item(row_index, 3).setText(new_bic)

                # rattrapage BDD (sans changer le status)
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

            # Pays (dépend souvent du transporteur trouvé via IBAN/BIC)
            lkz = self._get_country_for_document(rep_path, iban, bic)
            self.pdf_table.setItem(row_index, 4, QTableWidgetItem(lkz))


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


    def _get_saved_date_iban_bic_for_pdf(self, pdf_path: str) -> tuple[str, str, str]:
        # IMPORTANT: utiliser la version "préfixée" (entry_id__) si nécessaire.
        json_path = self._get_saved_json_path(pdf_path)
        if not os.path.exists(json_path):
            return ("", "", "")

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return (
                str(data.get("invoice_date", "")).strip(),
                str(data.get("iban", "")).strip(),
                str(data.get("bic", "")).strip(),
            )
        except Exception:
            return ("", "", "")

    

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

        # Pays (LKZ) : priorité au transporteur sélectionné, sinon déduction via IBAN/BIC
        lkz = ""
        try:
            if getattr(self, "selected_kundennr", None):
                lkz = str(self.transporter_repo.get_lkz_by_kundennr(str(self.selected_kundennr)) or "").strip()
        except Exception:
            lkz = ""
        if not lkz:
            lkz = self._get_country_for_bank(iban, bic)

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
    

    def apply_left_table_search_filter(self):
        """Filtre combiné (statut + recherche globale + filtre pays)."""
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        mode = str(getattr(self, "left_filter_mode", "pending") or "pending").strip().lower()
        query = (getattr(self, "left_search_input", None).text() if getattr(self, "left_search_input", None) else "")
        query = (query or "").strip().lower()

        country_q = (getattr(self, "left_country_filter_input", None).text() if getattr(self, "left_country_filter_input", None) else "")
        country_q = (country_q or "").strip().lower()

        cols_count = self.pdf_table.columnCount()

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                self.pdf_table.setRowHidden(row, True)
                continue

            status = str(it0.data(Qt.UserRole + 1) or "pending").strip().lower()

            # 1) filtre statut
            if mode == "pending":
                status_visible = (status == "pending")
            elif mode == "validated":
                status_visible = (status == "validated")
            elif mode == "errors":
                status_visible = (status == "error")

            else:
                status_visible = True

            # 2) filtre recherche globale
            values = []
            for col in range(cols_count):
                it = self.pdf_table.item(row, col)
                if it:
                    values.append((it.text() or "").strip().lower())
            haystack = " | ".join(values)
            search_visible = (not query) or (query in haystack)

            # 3) filtre pays (col 4)
            if country_q and cols_count >= 5:
                it = self.pdf_table.item(row, 4)
                lkz_txt = str(it.text() if it else "").strip().lower()
                country_visible = lkz_txt.startswith(country_q)
            else:
                country_visible = True

            self.pdf_table.setRowHidden(row, not (status_visible and search_visible and country_visible))


    def _get_country_for_document(self, pdf_path: str, iban: str | None, bic: str | None) -> str:
        """Pays (LKZ) affiché dans la liste de gauche.

        Priorité :
        1) si le JSON sauvegardé contient transporter_kundennr => on lit LKZ dans XXAKun
        2) sinon fallback sur la recherche par banque (IBAN/BIC)
        """
        try:
            data = self._read_saved_invoice_json(pdf_path) or {}
            kundennr = str(data.get("transporter_kundennr") or "").strip()
            if kundennr:
                lkz = str(self.transporter_repo.get_lkz_by_kundennr(kundennr) or "").strip()
                if lkz:
                    return lkz
        except Exception:
            pass

        return self._get_country_for_bank(iban, bic)

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