from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled
import fitz


class MainWindowCmrMixin:
    def _refresh_pdf_context_markers(self):
        """Affiche un cadre rouge pour la facture et orange pour les pages CMR.

        Sur les pages CMR, ajoute aussi en haut un petit bandeau orange avec
        le ou les dossiers associés.
        """
        try:
            viewer = getattr(self, "pdf_viewer", None)
            if viewer is None or not hasattr(viewer, "set_page_decorations"):
                return

            doc_path = str(getattr(self, "view_pdf_path", None) or getattr(self, "current_pdf_path", None) or "").strip()
            if not doc_path or not os.path.exists(doc_path):
                viewer.clear_page_decorations()
                return

            page_count = 0
            try:
                page_count = int(viewer.page_count() or 0)
            except Exception:
                page_count = 0
            if page_count <= 0:
                page_count = self._get_pdf_page_count(doc_path)

            decorations = []
            is_main_invoice = False
            try:
                is_main_invoice = os.path.abspath(doc_path) == os.path.abspath(str(getattr(self, "current_pdf_path", "") or ""))
            except Exception:
                is_main_invoice = False

            if page_count <= 0:
                page_count = 1

            for idx in range(page_count):
                deco = {"page": idx}
                page_no = idx + 1
                page_links = self._get_cmr_page_links_for_page(doc_path, page_no)
                if page_links:
                    dossier_list = []
                    seen = set()
                    for link in page_links:
                        tour_nr = str((link or {}).get("tour_nr") or "").strip()
                        if tour_nr and tour_nr not in seen:
                            seen.add(tour_nr)
                            dossier_list.append(tour_nr)
                    label = ""
                    if dossier_list:
                        label = ("Dossier associé : " if len(dossier_list) == 1 else "Dossiers associés : ") + ", ".join(dossier_list)
                    deco.update({
                        "border_color": (255, 140, 0, 235),
                        "label_text": label,
                        "label_color": (255, 140, 0, 245),
                        "label_background": (255, 255, 255, 210),
                    })
                elif is_main_invoice:
                    deco.update({
                        "border_color": (215, 20, 20, 235),
                    })

                if deco.get("border_color") or deco.get("label_text"):
                    decorations.append(deco)

            viewer.set_page_decorations(decorations)
        except Exception:
            try:
                if hasattr(self, "pdf_viewer") and hasattr(self.pdf_viewer, "clear_page_decorations"):
                    self.pdf_viewer.clear_page_decorations()
            except Exception:
                pass

    def _get_folder_choices_for_entry(self, entry_id: str) -> list[dict]:
        """
        Retourne la liste des dossiers (tour_nr + amount_ht_ocr) UNIQUEMENT depuis
        le tableau de droite SI l'entry_id correspond au document sélectionné.
        Sinon fallback: on cherche dans les JSON des documents du même entry_id.
        """
        entry_id = (entry_id or "").strip()
        if not entry_id:
            return []

        # 1) Source prioritaire : le tableau de droite (UI) si on est sur le même entry_id
        if self.selected_invoice_entry_id == entry_id:
            folders = self.get_folder_rows() or []
            folders = [f for f in folders if str(f.get("tour_nr") or "").strip()]
            if folders:
                return folders

        # 2) Fallback : lire les JSON d'un doc du même entry_id
        try:
            rows = self.logmail_repo.get_files_for_entry(entry_id) or []
        except Exception:
            rows = []

        found: dict[str, dict] = {}
        for r in rows:
            name = str(r.get("nom_pdf") or "").strip()
            if not name:
                continue
            pdf_path = self._find_pdf_path_by_filename(name)
            if not pdf_path:
                continue
            data = self._read_saved_invoice_json(pdf_path) or {}
            folders = data.get("folders") or []
            if not isinstance(folders, list):
                continue

            for f in folders:
                tournr = str(f.get("tour_nr") or "").strip()
                if tournr and tournr not in found:
                    found[tournr] = {
                        "tour_nr": tournr,
                        "amount_ht_ocr": str(f.get("amount_ht_ocr") or "").strip(),
                    }

        return list(found.values())

    def _get_current_pdf_page_number(self) -> int:
        """
        Retourne la page actuellement affichée dans le viewer (1-based).
        Fallback: 1 si indisponible.
        """
        try:
            if hasattr(self.pdf_viewer, "get_current_page_number"):
                return max(1, int(self.pdf_viewer.get_current_page_number()))
        except Exception:
            pass

        try:
            return max(1, int(getattr(self.pdf_viewer, "current_page", 0)) + 1)
        except Exception:
            return 1


    def _get_pdf_page_count(self, pdf_path: str) -> int:
        try:
            if is_image_document(pdf_path):
                return 1

            doc = fitz.open(pdf_path)
            try:
                return int(doc.page_count)
            finally:
                doc.close()
        except Exception:
            return 1 if is_image_document(pdf_path) else 0


    def _build_cmr_pages_summary(self, pdf_path: str) -> str:
        page_count = self._get_pdf_page_count(pdf_path)
        links_by_page = defaultdict(list)
        for link in self._get_cmr_page_links(pdf_path):
            try:
                links_by_page[int(link.get("page", 0) or 0)].append(link)
            except Exception:
                continue

        if page_count <= 0:
            return ""

        lines = []
        for page_no in range(1, page_count + 1):
            page_links = links_by_page.get(page_no, [])
            if page_links:
                parts = []
                for link in page_links:
                    parts.append(f"tournée {link.get('tour_nr', '')} / commande {link.get('auf_nr', '')}")
                lines.append(f"Page {page_no} → " + " ; ".join(parts))
            else:
                lines.append(f"Page {page_no} → non rattachée")

        return "\n".join(lines)

    def attach_cmr_to_dossier_from_right_list(self, pdf_path: str, filename: str, entry_id: str | None = None):
        """
        Rattache la PAGE actuellement affichée d'un PDF CMR à un dossier/commande
        du même entry_id.

        - Les choix de dossiers viennent du tableau de droite (si l'entry_id est celui affiché),
          sinon fallback : lecture des JSON des docs du même entry_id.
        - Le rattachement est stocké au niveau PAGE dans `cmr_page_links`.
        - Une même page peut porter plusieurs rattachements : au moment d'ajouter
          un rattachement sur une page déjà utilisée, l'utilisateur choisit
          Remplacer ou Ajouter.
        - Compatibilité ancienne logique conservée via cmr_tour_nr / cmr_auf_nr.
        """
        if not pdf_path:
            return
        if not filename:
            filename = os.path.basename(pdf_path)

        entry_id = (entry_id or self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(filename))
        entry_id = (entry_id or "").strip()

        if not entry_id:
            QMessageBox.information(self, "Rattacher CMR", "Impossible de déterminer l'entry_id de ce document.")
            return

        page_count = self._get_pdf_page_count(pdf_path)
        page_no = self._get_current_pdf_page_number()

        if page_count > 0 and page_no > page_count:
            page_no = 1

        folders = self._get_folder_choices_for_entry(entry_id)
        if not folders:
            QMessageBox.information(
                self,
                "Rattacher CMR",
                "Aucun dossier disponible.\n\n"
                "➡️ Renseigne d'abord les numéros de dossier dans le tableau de droite "
                "(sur un document du même entry_id), puis sauvegarde."
            )
            return

        tour_numbers: list[str] = []
        seen = set()
        for f in folders:
            t = str((f or {}).get("tour_nr") or "").strip()
            if t and t not in seen:
                seen.add(t)
                tour_numbers.append(t)

        if not tour_numbers:
            QMessageBox.information(self, "Rattacher CMR", "Aucun numéro de dossier valide.")
            return

        details_rows = []
        try:
            details_rows = self.tour_repo.get_palette_details_with_trajet_by_tournrs(tour_numbers) or []
        except Exception:
            details_rows = []

        title = "Rattacher CMR à une commande"
        if is_image_document(pdf_path):
            title += " (image)"
        elif page_count > 1:
            title += f" (page {page_no}/{page_count})"

        dlg = FolderSelectDialog(tour_numbers, details_rows, parent=self, title=title)
        if dlg.exec() != QDialog.Accepted or not dlg.selected_tour_nr or not dlg.selected_auf_nr:
            return

        tour_nr = str(dlg.selected_tour_nr).strip()
        auf_nr = str(dlg.selected_auf_nr).strip()
        if not tour_nr:
            return

        existing_page_links = self._get_cmr_page_links_for_page(pdf_path, page_no)
        mode = "replace"
        if existing_page_links:
            lines = []
            for link in existing_page_links[:10]:
                lines.append(f"- tournée {link.get('tour_nr', '')} / commande {link.get('auf_nr', '')}")
            if len(existing_page_links) > 10:
                lines.append(f"... + {len(existing_page_links) - 10} autre(s)")

            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Question)
            msg.setWindowTitle("Rattachement CMR")
            msg.setText(
                f"La page {page_no} possède déjà {len(existing_page_links)} rattachement(s) :\n\n"
                + "\n".join(lines)
                + "\n\nQue veux-tu faire ?"
            )
            btn_replace = msg.addButton("Remplacer", QMessageBox.AcceptRole)
            btn_add = msg.addButton("Ajouter", QMessageBox.ActionRole)
            btn_cancel = msg.addButton("Annuler", QMessageBox.RejectRole)
            msg.setDefaultButton(btn_add)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_cancel:
                return
            mode = "add" if clicked == btn_add else "replace"

        try:
            if self.current_pdf_path == pdf_path:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        json_path = self._get_saved_json_path(pdf_path)
        existing = self._read_saved_invoice_json(pdf_path) or {}

        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []
        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.add("cmr")
        existing["tags"] = sorted(tags_set)

        existing["entry_id"] = entry_id
        now_txt = datetime.now().isoformat(timespec="seconds")
        existing["cmr_attached_at"] = now_txt

        # Sauvegarde page -> tournée / commande.
        # En mode ajout, on garde les autres rattachements de la même page.
        links = existing.get("cmr_page_links")
        if not isinstance(links, list):
            links = []

        if mode == "replace":
            links = [x for x in links if int(x.get("page", 0) or 0) != int(page_no)]
        else:
            # éviter uniquement les doublons strictement identiques
            for x in links:
                if (
                    int(x.get("page", 0) or 0) == int(page_no)
                    and str(x.get("tour_nr") or "").strip() == tour_nr
                    and str(x.get("auf_nr") or "").strip() == auf_nr
                ):
                    QMessageBox.information(
                        self,
                        "Rattacher CMR",
                        f"Cette page est déjà rattachée au dossier {tour_nr} / commande {auf_nr}."
                    )
                    return

        links.append(
            {
                "page": int(page_no),
                "tour_nr": tour_nr,
                "auf_nr": auf_nr,
                "attached_at": now_txt,
            }
        )
        links.sort(key=lambda x: (int(x.get("page", 0) or 0), str(x.get("tour_nr") or ""), str(x.get("auf_nr") or "")))
        existing["cmr_page_links"] = links

        # Compat ancienne logique: on conserve aussi le dernier rattachement posé.
        existing["cmr_tour_nr"] = tour_nr
        existing["cmr_auf_nr"] = auf_nr

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        try:
            self._invalidate_cmr_status_cache()
        except Exception:
            pass

        # Refresh UI gauche
        cmr_count_for_page = len(self._get_cmr_page_links_for_page(pdf_path, page_no))
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                if page_count > 1:
                    it0.setToolTip(
                        f"CMR page {page_no} : {cmr_count_for_page} rattachement(s). "
                        f"Dernier : dossier {tour_nr} / commande {auf_nr}"
                    )
                else:
                    it0.setToolTip(f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}")
                break

        action_txt = "ajouté" if mode == "add" else "rattaché"
        if page_count > 1:
            self.statusBar().showMessage(
                f"CMR page {page_no} {action_txt} au dossier {tour_nr} / commande {auf_nr}.",
                3000
            )
        else:
            self.statusBar().showMessage(
                f"CMR {action_txt} au dossier {tour_nr} / commande {auf_nr}.",
                3000
            )

        self.apply_left_filter_to_table()

        try:
            if self.selected_invoice_entry_id and self.selected_invoice_entry_id.strip() == entry_id:
                for r in range(self.folder_table.rowCount()):
                    self._update_folder_row_status(r)
        except Exception:
            pass

        try:
            if getattr(self, "last_loaded_tour_nr", None):
                self.load_tour_information(self.last_loaded_tour_nr)
        except Exception:
            pass

        # Si tu as un panneau d'info sous le PDF, tu peux y afficher le résumé des pages CMR
        try:
            summary = self._build_cmr_pages_summary(pdf_path)
            if summary and hasattr(self, "tour_info") and self.tour_info is not None:
                current = self.tour_info.toPlainText().strip()
                block = "CMR par page :\n" + summary
                self.tour_info.setPlainText((current + "\n\n" + block).strip() if current else block)
        except Exception:
            pass

        try:
            self._refresh_pdf_context_markers()
        except Exception:
            pass

    def _choose_representative_pdf(self, group_paths: list[str]) -> str:
        """
        Choisit en priorité le document qui porte réellement les données facture.
        Fallback sur l'ancienne heuristique si nécessaire.
        """
        if not group_paths:
            return ""

        if hasattr(self, "_choose_invoice_source_document"):
            chosen = str(self._choose_invoice_source_document(group_paths) or "").strip()
            if chosen:
                return chosen

        best_iban_bic_and_folders = None
        best_iban_bic = None

        for p in group_paths:
            data = self._read_saved_invoice_json(p) or {}
            if not data:
                continue

            iban = str(data.get("iban") or "").strip()
            bic = str(data.get("bic") or "").strip()
            folders = self._extract_tournrs_from_saved(data) if hasattr(self, "_extract_tournrs_from_saved") else []

            if iban and bic and folders:
                best_iban_bic_and_folders = p
                break
            if iban and bic and best_iban_bic is None:
                best_iban_bic = p

        return best_iban_bic_and_folders or best_iban_bic or group_paths[0]


    def on_attach_cmr_main(self):
        pdf_path = self.view_pdf_path or self.current_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.information(self, "Rattacher CMR", "Aucun document affiché.")
            return

        filename = os.path.basename(pdf_path)
        entry_id = self.selected_invoice_entry_id
        self.attach_cmr_to_dossier_from_right_list(pdf_path, filename, entry_id=entry_id)


    def _make_cmr_link_key(self, pdf_path: str, link: dict) -> str:
        """Clé stable pour retrouver/supprimer un rattachement CMR."""
        return "|".join(
            [
                os.path.abspath(str(pdf_path or "")),
                str(int((link or {}).get("page", 0) or 0)),
                str((link or {}).get("tour_nr") or "").strip(),
                str((link or {}).get("auf_nr") or "").strip(),
                str((link or {}).get("attached_at") or "").strip(),
            ]
        )

    def _write_saved_invoice_json(self, pdf_path: str, data: dict) -> None:
        """Écrit le JSON de sauvegarde associé à un document."""
        json_path = self._get_saved_json_path(pdf_path)
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data if isinstance(data, dict) else {}, f, ensure_ascii=False, indent=2)

    def _collect_cmr_attachments_for_current_entry(self) -> list[dict]:
        """
        Construit la liste des CMR rattachées (depuis les JSON des docs du même entry_id).
        Version page-aware, avec chemins + clé de suppression/ouverture.
        """
        out: list[dict] = []
        seen = set()

        paths = self.entry_pdf_paths or []
        for p in paths:
            data = self._read_saved_invoice_json(p) or {}

            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            tags_norm = {str(t).strip().lower() for t in tags if str(t).strip()}

            page_links = data.get("cmr_page_links")
            if isinstance(page_links, list) and page_links:
                for link in page_links:
                    page_no = int(link.get("page", 0) or 0)
                    tour_nr = str(link.get("tour_nr") or "").strip()
                    auf_nr = str(link.get("auf_nr") or "").strip()
                    attached_at = str(link.get("attached_at") or "").strip()

                    key = self._make_cmr_link_key(p, link)
                    if key in seen:
                        continue
                    seen.add(key)

                    out.append(
                        {
                            "key": key,
                            "pdf_path": p,
                            "filename": os.path.basename(p),
                            "page": page_no,
                            "tour_nr": tour_nr,
                            "auf_nr": auf_nr,
                            "attached_at": attached_at,
                        }
                    )
                continue

            tour_nr = str(data.get("cmr_tour_nr") or "").strip()
            if "cmr" not in tags_norm and not tour_nr:
                continue

            legacy_link = {
                "page": 0,
                "tour_nr": tour_nr,
                "auf_nr": str(data.get("cmr_auf_nr") or "").strip(),
                "attached_at": str(data.get("cmr_attached_at") or "").strip(),
            }
            key = self._make_cmr_link_key(p, legacy_link)
            if key in seen:
                continue
            seen.add(key)

            out.append(
                {
                    "key": key,
                    "pdf_path": p,
                    "filename": os.path.basename(p),
                    "page": 0,
                    "tour_nr": tour_nr,
                    "auf_nr": legacy_link["auf_nr"],
                    "attached_at": legacy_link["attached_at"],
                }
            )

        return out

    def _get_cmr_attachments_for_tour(self, tour_nr: str) -> list[dict]:
        tour_nr = str(tour_nr or "").strip()
        if not tour_nr:
            return []
        return [x for x in self._collect_cmr_attachments_for_current_entry() if str(x.get("tour_nr") or "").strip() == tour_nr]

    def _open_cmr_attachment(self, attachment: dict) -> None:
        """Affiche le document CMR et positionne le viewer sur la page rattachée."""
        if not isinstance(attachment, dict):
            return
        pdf_path = str(attachment.get("pdf_path") or "").strip()
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.warning(self, "CMR", "Fichier CMR introuvable.")
            return

        self.view_pdf_path = pdf_path
        try:
            if pdf_path in (self.entry_pdf_paths or []):
                self.current_doc_index = self.entry_pdf_paths.index(pdf_path)
        except Exception:
            pass

        self.display_pdf()

        page_no = int(attachment.get("page", 0) or 0)
        if page_no > 0:
            try:
                self.pdf_viewer.go_to_page(page_no - 1)
            except Exception:
                pass
            try:
                self.update_page_indicator()
            except Exception:
                pass

        try:
            self.update_doc_indicator()
        except Exception:
            pass

        self.statusBar().showMessage(
            f"CMR ouverte : {os.path.basename(pdf_path)}" + (f" page {page_no}" if page_no else ""),
            3000,
        )

    def _delete_cmr_attachment(self, attachment: dict) -> bool:
        """Supprime un seul rattachement CMR du JSON source."""
        if not isinstance(attachment, dict):
            return False
        pdf_path = str(attachment.get("pdf_path") or "").strip()
        key = str(attachment.get("key") or "").strip()
        if not pdf_path or not key:
            return False

        data = self._read_saved_invoice_json(pdf_path) or {}
        links = data.get("cmr_page_links")
        if isinstance(links, list) and links:
            new_links = []
            removed = False
            for link in links:
                if self._make_cmr_link_key(pdf_path, link) == key and not removed:
                    removed = True
                    continue
                new_links.append(link)

            if not removed:
                return False

            data["cmr_page_links"] = new_links
            if new_links:
                last = new_links[-1]
                data["cmr_tour_nr"] = str(last.get("tour_nr") or "").strip()
                data["cmr_auf_nr"] = str(last.get("auf_nr") or "").strip()
                data["cmr_attached_at"] = str(last.get("attached_at") or "").strip()
            else:
                data["cmr_tour_nr"] = ""
                data["cmr_auf_nr"] = ""
                data["cmr_attached_at"] = ""

            self._write_saved_invoice_json(pdf_path, data)
            try:
                self._refresh_pdf_context_markers()
            except Exception:
                pass
            try:
                self._invalidate_cmr_status_cache()
            except Exception:
                pass
            return True

        # Compat ancien format sans cmr_page_links : on vide seulement les champs CMR.
        if str(data.get("cmr_tour_nr") or "").strip():
            data["cmr_tour_nr"] = ""
            data["cmr_auf_nr"] = ""
            data["cmr_attached_at"] = ""
            self._write_saved_invoice_json(pdf_path, data)
            try:
                self._invalidate_cmr_status_cache()
            except Exception:
                pass
            return True

        return False

    def _show_cmr_attachments_dialog_for_tour(self, tour_nr: str) -> None:
        """Liste les CMR rattachées à un dossier, avec ouverture et suppression."""
        tour_nr = str(tour_nr or "").strip()
        if not tour_nr:
            return

        attachments = self._get_cmr_attachments_for_tour(tour_nr)
        if not attachments:
            QMessageBox.information(self, "CMR", f"Aucune CMR rattachée au dossier {tour_nr}.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"CMR rattachées au dossier {tour_nr}")
        dlg.resize(760, 360)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"CMR rattachées au dossier {tour_nr} :"))

        table = QTableWidget(len(attachments), 5, dlg)
        table.setHorizontalHeaderLabels(["Fichier", "Page", "Commande", "Date rattachement", "Dossier"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)

        for row, att in enumerate(attachments):
            values = [
                str(att.get("filename") or ""),
                str(att.get("page") or ""),
                str(att.get("auf_nr") or ""),
                str(att.get("attached_at") or ""),
                str(att.get("tour_nr") or ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, row)
                table.setItem(row, col, item)

        layout.addWidget(table)

        btn_row = QHBoxLayout()
        btn_open = QPushButton("Ouvrir")
        btn_delete = QPushButton("Supprimer le rattachement")
        btn_close = QPushButton("Fermer")
        btn_row.addWidget(btn_open)
        btn_row.addWidget(btn_delete)
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        def selected_attachment():
            r = table.currentRow()
            if r < 0 and attachments:
                r = 0
                table.selectRow(0)
            if r < 0 or r >= len(attachments):
                return None
            return attachments[r]

        def do_open():
            att = selected_attachment()
            if not att:
                return
            dlg.accept()
            self._open_cmr_attachment(att)

        def do_delete():
            att = selected_attachment()
            if not att:
                return
            resp = QMessageBox.question(
                dlg,
                "Supprimer CMR",
                "Supprimer ce rattachement CMR ?\n\n"
                f"Fichier : {att.get('filename', '')}\n"
                f"Page : {att.get('page', '')}\n"
                f"Commande : {att.get('auf_nr', '')}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
            if self._delete_cmr_attachment(att):
                dlg.accept()
                try:
                    for r in range(self.folder_table.rowCount()):
                        self._update_folder_row_status(r)
                except Exception:
                    pass
                QMessageBox.information(self, "CMR", "Rattachement supprimé.")
            else:
                QMessageBox.warning(self, "CMR", "Impossible de supprimer le rattachement.")

        btn_open.clicked.connect(do_open)
        btn_delete.clicked.connect(do_delete)
        btn_close.clicked.connect(dlg.reject)
        table.cellDoubleClicked.connect(lambda _r, _c: do_open())
        if attachments:
            table.selectRow(0)
        dlg.exec()

    def _get_current_invoice_tours(self) -> set[str]:
        tours = set()
        for f in (self.get_folder_rows() or []):
            t = str(f.get("tour_nr") or "").strip()
            if t:
                tours.add(t)
        return tours

    def _get_cmr_attached_tours_for_entry(self) -> set[str]:
        """
        TourNr qui ont au moins une CMR rattachée (via JSON des docs du même entry_id).
        Compatible ancien + nouveau format page-aware.
        """
        tours = set()
        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}

            page_links = data.get("cmr_page_links")
            if isinstance(page_links, list) and page_links:
                for link in page_links:
                    t = str(link.get("tour_nr") or "").strip()
                    if t:
                        tours.add(t)
                continue

            t = str(data.get("cmr_tour_nr") or "").strip()
            if t:
                tours.add(t)

        return tours

    def _get_cmr_attached_orders_for_entry(self) -> dict[str, set[str]]:
        """
        Retourne les commandes CMR rattachées pour l'entry courant :
        {tour_nr: set(auf_nr)}

        Compatibilité :
        - nouveau format: cmr_page_links
        - ancien format: cmr_tour_nr / cmr_auf_nr
        """
        attached = defaultdict(set)
        legacy = defaultdict(int)

        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}

            page_links = data.get("cmr_page_links")
            if isinstance(page_links, list) and page_links:
                for link in page_links:
                    tour_nr = str(link.get("tour_nr") or "").strip()
                    auf_nr = str(link.get("auf_nr") or "").strip()

                    if not tour_nr:
                        continue

                    if auf_nr:
                        attached[tour_nr].add(auf_nr)
                    else:
                        legacy[tour_nr] += 1
                continue

            tour_nr = str(data.get("cmr_tour_nr") or "").strip()
            auf_nr = str(data.get("cmr_auf_nr") or "").strip()

            if not tour_nr:
                continue

            if auf_nr:
                attached[tour_nr].add(auf_nr)
            else:
                legacy[tour_nr] += 1

        self._cmr_legacy_cache = dict(legacy)
        return dict(attached)

    def _get_row_cmr_widget(self, row: int):
        return self.folder_table.cellWidget(row, 3)

    def _check_all_dossiers_have_cmr(self) -> tuple[bool, list[str]]:
        invoice_tours = self._get_current_invoice_tours()
        if not invoice_tours:
            return True, []

        cmr_tours = self._get_cmr_attached_tours_for_entry()
        missing = sorted(invoice_tours - cmr_tours)
        return (len(missing) == 0), missing

    def _get_required_orders_by_tour(self, tours: set[str]) -> dict[str, set[str]]:
        key = tuple(sorted(tours))
        if getattr(self, "_req_orders_cache_key", None) == key:
            return getattr(self, "_req_orders_cache", {}) or {}

        req = defaultdict(set)
        try:
            rows = self.tour_repo.get_palette_details_with_trajet_by_tournrs(list(tours)) or []
        except Exception:
            rows = []

        for r in rows:
            tour = str(r.get("Dossier") or "").strip()
            auf = str(r.get("AufNr") or "").strip()
            if tour and auf:
                req[tour].add(auf)

        self._req_orders_cache_key = key
        self._req_orders_cache = dict(req)
        return self._req_orders_cache


    def _check_all_orders_have_cmr(self) -> tuple[bool, dict[str, list[str]]]:
        """
        ok=True si toutes les commandes (AufNr) de tous les dossiers ont une CMR.
        Une commande est couverte si :
        - elle a une CMR rattachée dans l'appli
        - OU elle existe déjà en GED
        """
        invoice_tours = self._get_current_invoice_tours()

        if not invoice_tours:
            return True, {}

        required = self._get_required_orders_by_tour(invoice_tours)
        attached = self._get_cmr_attached_orders_for_entry()
        legacy = getattr(self, "_cmr_legacy_cache", {}) or {}

        # toutes les commandes requises
        all_required_aufnrs = sorted({
            auf
            for req_set in required.values()
            for auf in req_set
            if str(auf).strip()
        })

        try:
            ged_aufnrs = self.tour_repo.get_aufnrs_with_cmr_in_ged(all_required_aufnrs)
        except Exception:
            ged_aufnrs = set()

        missing_by_tour = {}

        for tour in sorted(invoice_tours):
            req = set(required.get(tour, set()))
            att = set(attached.get(tour, set()))

            # compat ancienne CMR sans auf_nr : si une seule commande dans la tournée, on accepte
            if not att and legacy.get(tour, 0) > 0 and len(req) == 1:
                att = set(req)

            if req:
                covered = set(att)

                # ajoute les commandes déjà présentes en GED
                for auf in req:
                    if auf in ged_aufnrs:
                        covered.add(auf)

                miss = sorted(req - covered)
                if miss:
                    missing_by_tour[tour] = miss
            else:
                missing_by_tour[tour] = ["(aucune commande trouvée en BDD)"]

        return (len(missing_by_tour) == 0), missing_by_tour



    def _is_cmr_missing_block_enabled_for_current_invoice(self) -> bool:
        """
        Autorise la validation si la facture courante est bloquée avec un motif
        de type "CMR manquant".

        On supporte :
        - le nouveau format block_options par document
        - l'ancien format top-level blocked / block_comment
        - le cas où le blocage a été posé sur le document affiché, la facture
          principale, ou une PJ du même groupe.
        """
        try:
            data = self._read_saved_invoice_json(getattr(self, 'current_pdf_path', '') or '') or {}
        except Exception:
            data = {}

        block_options = {}
        try:
            block_options.update(data.get('block_options', {}) or {})
        except Exception:
            pass
        try:
            block_options.update(getattr(self, 'block_options', {}) or {})
        except Exception:
            pass

        # On n'autorise le bypass QUE si le blocage est posé sur le
        # document courant (facture sélectionnée) ou sur le document
        # actuellement affiché. Surtout pas sur n'importe quelle PJ du groupe,
        # sinon une ancienne PJ bloquée pourrait autoriser la validation par erreur.
        candidate_names = set()
        for p in [getattr(self, 'current_pdf_path', None), getattr(self, 'view_pdf_path', None)]:
            if not p:
                continue
            name = os.path.basename(str(p))
            if not name:
                continue
            candidate_names.add(name)
            try:
                candidate_names.add(strip_entry_prefix(name))
            except Exception:
                pass

        def _matches_cmr_missing(info: dict) -> bool:
            if not isinstance(info, dict):
                return False
            if not bool(info.get('blocked', False)):
                return False
            tokens = [
                str(info.get('reason', '') or ''),
                str(info.get('comment', '') or ''),
                str(info.get('free_comment', '') or ''),
            ]
            normalized = ' '.join(tokens).strip().lower()
            return 'cmr manquant' in normalized

        for name in candidate_names:
            if _matches_cmr_missing(block_options.get(name, {}) or {}):
                return True

        if bool(data.get('blocked', False)):
            normalized = str(data.get('block_comment', '') or '').strip().lower()
            if 'cmr manquant' in normalized:
                return True

        return False

    def _block_validate_if_missing_cmr(self) -> bool:
        ok, missing_by_tour = self._check_all_orders_have_cmr()
        if ok:
            return True

        if self._is_cmr_missing_block_enabled_for_current_invoice():
            return True

        lines = []
        for tour, miss in missing_by_tour.items():
            lines.append(f"{tour}: {', '.join(miss)}")

        QMessageBox.warning(
            self,
            "Validation impossible",
            "Toutes les commandes doivent avoir une CMR, soit rattachée à Winsped, soit présente en GED.\n\n"
            "Commandes sans CMR :\n" + "\n".join(lines)
        )
        return False

    def relink_left_document_to_other_group(self, row: int):
        it0 = self.pdf_table.item(row, 0)
        if not it0:
            return

        group_paths = it0.data(Qt.UserRole + 5)
        if isinstance(group_paths, (list, tuple)) and group_paths:
            src_paths = [p for p in group_paths if p and os.path.exists(p)]
        else:
            p = it0.data(Qt.UserRole)
            src_paths = [p] if p and os.path.exists(p) else []

        if not src_paths:
            QMessageBox.information(self, "Regrouper", "Impossible de retrouver le fichier source.")
            return

        if len(src_paths) > 1:
            labels = [f"{i+1}) {strip_entry_prefix(os.path.basename(p))}" for i, p in enumerate(src_paths)]
            default_idx = 0
            if getattr(self, "current_pdf_path", None) in src_paths:
                default_idx = src_paths.index(self.current_pdf_path)

            choice, ok = QInputDialog.getItem(
                self, "Regrouper", "Document à rattacher :", labels, default_idx, False
            )
            if not ok or not choice:
                return
            src_path = src_paths[int(choice.split(")")[0]) - 1]
        else:
            src_path = src_paths[0]

        src_name = os.path.basename(src_path)
        src_entry_id = (self.logmail_repo.get_entry_id_for_file(src_name) or "").strip()

        candidates = []
        targets = []

        for r in range(self.pdf_table.rowCount()):
            it = self.pdf_table.item(r, 0)
            if not it:
                continue

            target_entry = str(it.data(Qt.UserRole + 4) or "").strip()
            target_path = it.data(Qt.UserRole)
            if not target_entry or not target_path:
                continue

            if src_entry_id and target_entry == src_entry_id:
                continue

            rep_name = strip_entry_prefix(os.path.basename(str(target_path)))
            group_paths2 = it.data(Qt.UserRole + 5)
            n_docs = len(group_paths2) if isinstance(group_paths2, (list, tuple)) else 1
            label = f"{rep_name}   ({n_docs} doc)   [{target_entry}]"
            candidates.append(label)
            targets.append(target_entry)

        if not candidates:
            QMessageBox.information(self, "Regrouper", "Aucune cible disponible (pas d'autre groupe).")
            return

        choice, ok = QInputDialog.getItem(
            self, "Rattacher à un Dossier", "Choisis le fichier/groupe cible :", candidates, 0, False
        )
        if not ok or not choice:
            return

        idx = candidates.index(choice)
        target_entry_id = targets[idx]

        try:
            self.logmail_repo.set_entry_id_for_file(src_name, target_entry_id)
        except Exception as e:
            QMessageBox.warning(self, "Regrouper", f"Erreur SQL:\n{e}")
            return

        try:
            data = self._read_saved_invoice_json(src_path) or {}
            data["entry_id"] = target_entry_id
            json_path = self._get_saved_json_path(src_path)
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        try:
            self.load_folder(os.path.dirname(src_path))
        except Exception:
            pass

        self.statusBar().showMessage(f"{src_name} rattaché au groupe {target_entry_id}.", 3000)

    def _split_pdf_one_page_per_file_for_cmr(self, pdf_path: str, entry_id: str) -> list[str]:
        """
        Conserve cette méthode si tu veux encore pouvoir découper physiquement un PDF.
        Elle n'est plus nécessaire pour le rattachement page par page, mais reste disponible.
        """
        pdf_path = str(pdf_path or "").strip()
        if not pdf_path or not os.path.exists(pdf_path):
            return []

        src_name = os.path.basename(pdf_path)
        folder = os.path.dirname(pdf_path)
        base_name, ext = os.path.splitext(src_name)

        doc = fitz.open(pdf_path)
        try:
            if doc.page_count <= 1:
                return [pdf_path]

            out_paths = []
            src_json = self._read_saved_invoice_json(pdf_path) or {}

            for i in range(doc.page_count):
                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=i, to_page=i)

                new_name = f"{base_name}_p{i+1:02d}.pdf"
                new_path = os.path.join(folder, new_name)

                if os.path.exists(new_path):
                    root, ext2 = os.path.splitext(new_name)
                    n = 1
                    while True:
                        candidate = os.path.join(folder, f"{root}_{n}{ext2}")
                        if not os.path.exists(candidate):
                            new_path = candidate
                            new_name = os.path.basename(candidate)
                            break
                        n += 1

                new_doc.save(new_path)
                new_doc.close()

                self.logmail_repo.clone_logmail_row_for_split_file(src_name, new_name, entry_id=entry_id)

                new_json = dict(src_json)
                new_json["entry_id"] = entry_id
                new_json["tags"] = sorted({*(new_json.get("tags") or []), "cmr"})
                new_json["cmr_tour_nr"] = ""
                new_json["cmr_auf_nr"] = ""
                new_json["cmr_attached_at"] = ""
                new_json["source_split_from"] = src_name
                new_json["source_split_page"] = i + 1

                json_path = self._get_saved_json_path(new_path)
                os.makedirs(os.path.dirname(json_path), exist_ok=True)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(new_json, f, ensure_ascii=False, indent=2)

                out_paths.append(new_path)

            try:
                os.remove(pdf_path)
            except Exception:
                pass

            try:
                src_json_path = self._get_saved_json_path(pdf_path)
                if os.path.exists(src_json_path):
                    os.remove(src_json_path)
            except Exception:
                pass

            return out_paths
        finally:
            doc.close()

    def _split_cmr_pages_for_validation(self, pdf_path: str, target_dir: str, entry_id: str | None = None) -> dict[int, str]:
        """
        Extrait et écrit un PDF par page CMR rattachée à ce document.
        Retourne mapping {page_no -> path_pdf}.

        Une page peut désormais contenir plusieurs rattachements CMR. On ne coupe
        donc qu'une seule fois la page, puis toutes les lignes d'export DMS qui
        pointent vers cette page réutilisent le même PDF découpé.
        """
        pdf_path = str(pdf_path or "").strip()
        target_dir = str(target_dir or "").strip()
        if not pdf_path or not os.path.exists(pdf_path) or not target_dir:
            return {}

        links = self._get_cmr_page_links(pdf_path)
        if not isinstance(links, list) or not links:
            return {}

        links_by_page: dict[int, list[dict]] = defaultdict(list)
        for link in links:
            try:
                page_no = int(link.get("page", 0) or 0)
            except Exception:
                page_no = 0
            if page_no > 0:
                links_by_page[page_no].append(link)

        if not links_by_page:
            return {}

        os.makedirs(target_dir, exist_ok=True)
        doc = fitz.open(pdf_path)
        try:
            split_paths: dict[int, str] = {}
            src_name = os.path.basename(pdf_path)
            base_name, ext = os.path.splitext(src_name)

            for page_no, page_links in sorted(links_by_page.items()):
                if page_no <= 0 or page_no > doc.page_count:
                    continue

                first = page_links[0] if page_links else {}
                tour_nr = re.sub(r"[^0-9A-Za-z_-]", "", str(first.get("tour_nr") or "").strip())
                auf_nr = re.sub(r"[^0-9A-Za-z_-]", "", str(first.get("auf_nr") or "").strip())
                suffix = [f"CMR_p{page_no:02d}"]
                if len(page_links) > 1:
                    suffix.append(f"MULTI{len(page_links)}")
                if tour_nr:
                    suffix.append(f"T{tour_nr}")
                if auf_nr:
                    suffix.append(f"A{auf_nr}")

                new_name = f"{base_name}_" + "_".join(suffix) + ext
                new_path = os.path.join(target_dir, new_name)

                if os.path.exists(new_path):
                    root, ext2 = os.path.splitext(new_name)
                    n = 1
                    while True:
                        candidate = os.path.join(target_dir, f"{root}_{n}{ext2}")
                        if not os.path.exists(candidate):
                            new_path = candidate
                            break
                        n += 1

                new_doc = fitz.open()
                new_doc.insert_pdf(doc, from_page=page_no - 1, to_page=page_no - 1)
                new_doc.save(new_path)
                new_doc.close()

                try:
                    self.logmail_repo.clone_logmail_row_for_split_file(src_name, os.path.basename(new_path), entry_id=entry_id)
                except Exception:
                    pass

                try:
                    src_json = self._read_saved_invoice_json(pdf_path) or {}
                    new_data = dict(src_json)
                    new_data["entry_id"] = str(entry_id or "").strip()
                    tags = new_data.get("tags") or []
                    if isinstance(tags, str):
                        tags = [tags]
                    if not isinstance(tags, list):
                        tags = []
                    tags = sorted({*(tags or []), "cmr"})
                    new_data["tags"] = tags
                    new_data["cmr_page_links"] = page_links
                    new_data["source_split_from"] = src_name
                    new_data["source_split_page"] = page_no

                    json_path = self._get_saved_json_path(new_path)
                    os.makedirs(os.path.dirname(json_path), exist_ok=True)
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(new_data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

                split_paths[page_no] = new_path

            return split_paths
        finally:
            doc.close()

    def _get_cmr_page_links(self, pdf_path: str) -> list[dict]:
        data = self._read_saved_invoice_json(pdf_path) or {}
        links = data.get("cmr_page_links")
        if isinstance(links, list):
            return [x for x in links if isinstance(x, dict)]
        return []

    def _get_cmr_page_links_for_page(self, pdf_path: str, page_no: int) -> list[dict]:
        out = []
        for link in self._get_cmr_page_links(pdf_path):
            try:
                if int(link.get("page", 0) or 0) == int(page_no):
                    out.append(link)
            except Exception:
                continue
        return out

    def _save_cmr_page_link(self, pdf_path: str, page_no: int, tour_nr: str, auf_nr: str, mode: str = "replace"):
        data = self._read_saved_invoice_json(pdf_path) or {}
        links = data.get("cmr_page_links")
        if not isinstance(links, list):
            links = []

        if mode != "add":
            new_links = [x for x in links if int(x.get("page", 0) or 0) != int(page_no)]
        else:
            new_links = list(links)

        now_txt = datetime.now().isoformat(timespec="seconds")
        if mode == "add":
            for x in new_links:
                if (
                    int(x.get("page", 0) or 0) == int(page_no)
                    and str(x.get("tour_nr") or "").strip() == str(tour_nr or "").strip()
                    and str(x.get("auf_nr") or "").strip() == str(auf_nr or "").strip()
                ):
                    return

        new_links.append(
            {
                "page": int(page_no),
                "tour_nr": str(tour_nr or "").strip(),
                "auf_nr": str(auf_nr or "").strip(),
                "attached_at": now_txt,
            }
        )
        new_links.sort(key=lambda x: (int(x.get("page", 0) or 0), str(x.get("tour_nr") or ""), str(x.get("auf_nr") or "")))

        data["cmr_page_links"] = new_links
        data["cmr_tour_nr"] = str(tour_nr or "").strip()
        data["cmr_auf_nr"] = str(auf_nr or "").strip()
        data["cmr_attached_at"] = now_txt

        self._write_saved_invoice_json(pdf_path, data)

    def _get_cmr_page_link(self, pdf_path: str, page_no: int) -> dict | None:
        links = self._get_cmr_page_links_for_page(pdf_path, page_no)
        return links[0] if links else None
