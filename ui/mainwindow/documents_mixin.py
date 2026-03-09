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
            if not name.lower().endswith(".pdf"):
                continue
            full_path = os.path.join(current_dir, name)
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
        model_dir = r"C:\git\OCR\OCR\models"
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

        linked_filename = (it0.text() or "").strip()
        if not linked_filename:
            return

        menu = QMenu(self)

        pdf_path = it0.data(Qt.UserRole)  # ✅ définir AVANT de l'utiliser

        action_link = menu.addAction("Rattacher ce document à la facture sélectionnée")
        action_attach_cmr = menu.addAction("Rattacher CMR à un dossier…")
        action_attach_cmr.setEnabled(bool(pdf_path))  # ✅ maintenant OK

        menu.addSeparator()
        action_delete = menu.addAction("Supprimer")
        action_delete.setEnabled(bool(pdf_path))
        action_fetch_links = menu.addAction("Télécharger documents via liens (CMR)…")
        action_fetch_links.setEnabled(bool(pdf_path))
        # --- cible = ligne actuellement sélectionnée (la facture cible)
        target_row = self.pdf_table.currentRow()
        target_filename = None
        target_entry_id = None

        if target_row >= 0:
            it = self.pdf_table.item(target_row, 0)
            if it:
                target_filename = (it.text() or "").strip()
                target_entry_id = self.logmail_repo.get_entry_id_for_file(target_filename)

        # fallback: si mémorisé via clic gauche
        if not target_entry_id and self.selected_invoice_filename:
            target_filename = self.selected_invoice_filename
            target_entry_id = self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(target_filename)

        can_link = bool(target_entry_id and target_filename and linked_filename and linked_filename != target_filename)
        action_link.setEnabled(can_link)
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
                target_filename = (it.text() or "").strip()
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
        """Remplit la liste de PDFs à partir d'un dossier (avec IBAN/BIC + status pour filtres)."""
        self.current_folder_path = folder
        # --- Sécurité : si la table a été détruite, on évite le crash ---
        if not hasattr(self, "pdf_table") or self.pdf_table is None or not isValid(self.pdf_table):
            tbl = self.findChild(QTableWidget, "pdf_table")
            if tbl is not None and isValid(tbl):
                self.pdf_table = tbl
            else:
                return

        self.pdf_table.setRowCount(0)

        try:
            pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Impossible de lire le dossier :\n{folder}\n\n{e}")
            return

        self.pdf_table.setRowCount(0)

        try:
            pdf_files = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(".pdf")]
        except Exception as e:
            QMessageBox.critical(self, "Erreur", f"Impossible de lire le dossier :\n{folder}\n\n{e}")
            return

        # ✅ mapping nom_pdf -> entry_id (en batch)
        try:
            entry_map = self.logmail_repo.get_entry_ids_for_files(pdf_files) or {}
        except Exception:
            entry_map = {}

        # ✅ group by entry_id
        groups: dict[str, list[str]] = {}
        for fn in pdf_files:
            entry_id = entry_map.get(fn)
            if not entry_id:
                entry_id = f"__NO_ENTRY__::{fn}"
            groups.setdefault(entry_id, []).append(fn)


        # ✅ construire les lignes (1 ligne par entry_id)

        rows_to_add = []
        for entry_id, files in groups.items():
            group_paths = [os.path.join(folder, f) for f in files if os.path.exists(os.path.join(folder, f))]
            if not group_paths:
                continue

            rep_path = self._choose_representative_pdf(group_paths)
            if not rep_path:
                rep_path = group_paths[0]
            rep_filename = os.path.basename(rep_path)
            status = self._get_saved_status_for_pdf(rep_path)

            rows_to_add.append((rep_filename, rep_path, entry_id, group_paths, status))

        # ordre métier depuis XXA_LOGMAIL pour les vrais entry_id
        try:
            entry_order_map = self.logmail_repo.get_entry_creation_order_map(
                [entry_id for _, _, entry_id, _, _ in rows_to_add]
            ) or {}
        except Exception:
            entry_order_map = {}

        def _sort_key(row):
            rep_filename, rep_path, entry_id, group_paths, status = row
            order_key = entry_order_map.get(entry_id, "9999-12-31 23:59:59")
            return (order_key, rep_filename.lower())

        rows_to_add.sort(key=_sort_key)

        # Pool uniquement pour les factures en attente
        if getattr(self, "left_filter_mode", "pending") == "pending":
            pool_size = int(getattr(self, "pending_pool_size", 3) or 3)
            rows_to_add = [r for r in rows_to_add if str(r[4] or "").strip().lower() != "validated"][:pool_size]

        for row, (rep_filename, rep_path, entry_id, group_paths, status) in enumerate(rows_to_add):
            invoice_date, iban, bic = self._get_saved_date_iban_bic_for_pdf(rep_path)

            self.pdf_table.insertRow(row)

            extra = max(0, len(group_paths) - 1)
            display_name = rep_filename if extra == 0 else f"{rep_filename} (+{extra})"

            it0 = QTableWidgetItem(display_name)

            names = "\n".join([os.path.basename(p) for p in group_paths])
            it0.setToolTip(f"entry_id: {entry_id}\nDocuments: {len(group_paths)}\n\n{names}")
            it0.setData(Qt.UserRole, rep_path)           # chemin du PDF représentant
            it0.setData(Qt.UserRole + 1, status)         # status pour filtres
            it0.setData(Qt.UserRole + 4, entry_id)       # ✅ entry_id du groupe
            it0.setData(Qt.UserRole + 5, group_paths)    # ✅ liste complète des PDFs du groupe

           

            self.pdf_table.setItem(row, 0, it0)
            self.pdf_table.setItem(row, 1, QTableWidgetItem(invoice_date))
            self.pdf_table.setItem(row, 2, QTableWidgetItem(iban))
            self.pdf_table.setItem(row, 3, QTableWidgetItem(bic))

        self.current_pdf_path = None
        self.clear_fields()


        # Appliquer le filtre courant (pending/validated/errors)
        if hasattr(self, "apply_left_filter_to_table"):
            self.apply_left_filter_to_table()
        self.refresh_left_table_processing_states()
        self.apply_left_filter_to_table()
        self.refresh_left_table_processing_claims()

        # reset panneau de droite
        self.current_pdf_path = None
        self.clear_fields()
        self.apply_left_table_search_filter()

    def _get_saved_json_path(self, pdf_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        return os.path.join(model_dir, f"{base_name}.json")

    def _get_saved_json_path_for_pdf(self, pdf_path: str) -> str:
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = r"C:\git\OCR\OCR\models"
        return os.path.join(model_dir, f"{base_name}.json")

    def _get_saved_date_iban_bic_for_pdf(self, pdf_path: str) -> tuple[str, str, str]:
        json_path = self._get_saved_json_path_for_pdf(pdf_path)
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
        """Met à jour en temps réel les colonnes Date / IBAN / BIC du tableau de gauche pour un PDF."""
        if not pdf_path:
            return
        if not hasattr(self, "pdf_table"):
            return
        if self.pdf_table.columnCount() < 4:
            return

        invoice_date = (invoice_date or "").strip()
        iban = (iban or "").strip()
        bic = (bic or "").strip()

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue
            p = it0.data(Qt.UserRole)
            if p == pdf_path:
                self.pdf_table.setItem(row, 1, QTableWidgetItem(invoice_date))
                self.pdf_table.setItem(row, 2, QTableWidgetItem(iban))
                self.pdf_table.setItem(row, 3, QTableWidgetItem(bic))
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
        self.apply_left_table_search_filter()

    def _is_typing_in_input(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit))
    

    def apply_left_table_search_filter(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        query = ""
        if hasattr(self, "left_search_input") and self.left_search_input is not None:
            query = (self.left_search_input.text() or "").strip().lower()

        for row in range(self.pdf_table.rowCount()):
            values = []

            for col in range(min(self.pdf_table.columnCount(), 4)):
                it = self.pdf_table.item(row, col)
                values.append((it.text() if it else "").strip().lower())

            haystack = " | ".join(values)

            visible = (not query) or (query in haystack)
            self.pdf_table.setRowHidden(row, not visible)