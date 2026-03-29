from __future__ import annotations
from csv import writer
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled


class MainWindowValidationMixin:

    def on_validate_invoice(self):
        if self._is_typing_in_input():
            return
        if not self.current_pdf_path:
            self.statusBar().showMessage("Aucun PDF sélectionné.", 3000)
            return
        if not self._block_validate_if_missing_cmr():
            return
        if not self._block_validate_if_transporter_not_matching_tours():
            return
        if not self._block_validate_if_ht_amounts_not_matching_tours():
            return

        # Vérifier que le numéro de facture est rempli
        invoice_nr = (self.invoice_number_input.text() or "").strip()
        if not invoice_nr:
            QMessageBox.warning(
                self,
                "Validation impossible",
                "Le champ 'N° facture' doit être rempli pour valider la facture."
            )
            return

        # Vérifier que la date de facture est remplie
        invoice_date = (self.date_input.text() or "").strip()
        if not invoice_date:
            QMessageBox.warning(
                self,
                "Validation impossible",
                "Le champ 'Date facture' doit être rempli pour valider la facture."
            )
            return

        # Vérifie les tournées AVANT toute validation
        tournrs = sorted({
            (r.get("tour_nr") or "").strip()
            for r in self.get_folder_rows()
            if (r.get("tour_nr") or "").strip()
        })

        if not tournrs:
            QMessageBox.warning(
                self,
                "Validation",
                "Aucun dossier (TourNr) trouvé : validation impossible."
            )
            return
        
        # Vérifie que le dossier n'est pas déjà en facturation
        for tour_nr in tournrs:
            try:
                already_invoiced = self.lisinvoice_repo.tour_exists(tour_nr)
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Validation impossible",
                    "Erreur lors du contrôle du dossier dans LISINVOICE_EDTRANS.\n\n"
                    f"Détail : {e}"
                )
                return

            if already_invoiced:
                QMessageBox.warning(
                    self,
                    "Validation impossible",
                    "Le dossier est deja en facturation"
                )
                return

        
        # --- Anti-doublon facture (XXARe) + proposition mise en erreur ---
        invoice_nr, kundennr = self._get_invoice_number_and_kundennr_for_dupecheck()

        if invoice_nr and kundennr:
            try:
                exists = bool(self.xxare_repo.invoice_exists(invoice_nr, kundennr, aufdk="K"))
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Validation impossible",
                    "Erreur lors du contrôle anti-doublon (XXARe).\n\n"
                    f"Détail: {e}"
                )
                return

            if exists:
                resp_dup = QMessageBox.question(
                    self,
                    "Facture déjà existante",
                    "Cette facture existe déjà, voulez-vous la mettre en erreur ?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if resp_dup == QMessageBox.Yes:
                    self._mark_current_entry_as_error(reason="duplicate_invoice")
                return

        resp = QMessageBox.question(
            self,
            "Validation facture",
            "Valider la facture ?\n",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return

        # 1) Toujours re-sauvegarder AVANT les updates SQL
        self.save_current_data(status="validated", show_message=False)

        aux_update_error = ""
        kundennr_for_aux = str(getattr(self, "selected_kundennr", "") or "").strip()
        aux_value = str(self.transporter_aux_input.text() or "").strip()
        if kundennr_for_aux and aux_value:
            try:
                self.transporter_repo.update_ktoKreA(kundennr_for_aux, aux_value)
                if hasattr(self, "_set_transporter_aux_locked"):
                    self._set_transporter_aux_locked(True, aux_value)
            except Exception as e:
                aux_update_error = str(e)

        entry_id = str(getattr(self, "selected_invoice_entry_id", "") or "").strip()
        if entry_id:
            try:
                self.logmail_repo.set_processing_status_for_entry(entry_id, "validated")
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Validation",
                    f"Facture sauvegardée, mais impossible de mettre à jour le statut SQL :\n{e}"
                )

        # 3) Déterminer la valeur à appliquer selon blocage
        doc_name = os.path.basename(self.current_pdf_path)
        blocked = bool((self.block_options.get(doc_name, {}) or {}).get("blocked", False))
        value = 601 if blocked else 600
        comment = str((self.block_options.get(doc_name, {}) or {}).get("comment", "") or "").strip()

        # 4) Updates SQL
        errors = []
        ocr_user = str(getattr(self, "current_username", "") or "").strip()
        for t in tournrs:
            try:
                self.tour_repo.set_infosymbol18_for_tournr(t, value=value)
                self.tour_repo.set_ocr_user_for_tournr(t, ocr_user=ocr_user)
                self.tour_repo.set_block_status_for_tournr(
                    t,
                    is_blocked=blocked,
                    motif=comment,
                    ocr_user=ocr_user,
                )
            except Exception as e:
                errors.append(f"{t} : {e}")


        # 4 bis) Alimentation LISINVOICE_EDTRANS
        lisinvoice_errors = []
        try:
            lisinvoice_errors = self._push_lisinvoice_rows()
        except Exception as e:
            lisinvoice_errors = [str(e)]


        # 5) Copie DMS
        dms_path = ""
        dms_error = ""
        try:
            dms_path = self._copy_validated_pdf_to_dms()
        except Exception as e:
            dms_error = str(e)

        # 5 bis) Export CSV
        csv_path = ""
        csv_error = ""
        try:
            csv_path = self._export_validation_csv()
        except Exception as e:
            csv_error = str(e)


        # 6) Message final unique
        all_error_parts = []

        if errors:
            all_error_parts.append("Erreurs SQL tournées :\n" + "\n".join(errors))

        if lisinvoice_errors:
            all_error_parts.append(
                "Erreurs LISINVOICE_EDTRANS :\n" + "\n".join(lisinvoice_errors)
            )

        if aux_update_error:
            all_error_parts.append(
                "Erreur mise à jour compte auxiliaire transporteur :\n" + aux_update_error
            )

        if all_error_parts:
            msg = "Facture VALIDÉE et sauvegardée.\n\n" + "\n\n".join(all_error_parts)

            if dms_path:
                msg += f"\n\nPDF copié vers :\n{dms_path}"
            elif dms_error:
                msg += f"\n\nAttention : copie DMS échouée :\n{dms_error}"

            if csv_path:
                msg += f"\n\nCSV exporté vers :\n{csv_path}"
            elif csv_error:
                msg += f"\n\nAttention : export CSV échoué :\n{csv_error}"

            QMessageBox.warning(self, "Validation", msg)
        else:
            suffix = " (document BLOQUÉ)" if blocked else ""
            msg = f"Facture VALIDÉE et sauvegardée. Dossier(s){suffix}."

            if dms_path:
                msg += f"\n\nPDF copié vers :\n{dms_path}"
            elif dms_error:
                msg += f"\n\nCopie DMS échouée :\n{dms_error}"

            if csv_path:
                msg += f"\n\nCSV exporté vers :\n{csv_path}"
            elif csv_error:
                msg += f"\n\nExport CSV échoué :\n{csv_error}"

            QMessageBox.information(self, "Validation", msg)



        # 7) Recharge le pool "En attente"
        try:
            current_folder = getattr(self, "current_folder_path", None)
            if current_folder and os.path.isdir(current_folder):
                self.load_folder(current_folder)

                if self.pdf_table.rowCount() > 0:
                    self.pdf_table.selectRow(0)
                    self.on_pdf_selected(0, 0)
        except Exception:
            pass


    def _get_saved_status_for_pdf(self, pdf_path: str) -> str:
        json_path = self._get_saved_json_path(pdf_path)
        if not os.path.exists(json_path):
            return "draft"
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return (data.get("status") or "draft").strip()
        except Exception:
            return "draft"

    def set_left_filter(self, mode: str):
        self.left_filter_mode = mode

        current_folder = str(getattr(self, "current_folder_path", "") or "").strip()
        if current_folder and os.path.isdir(current_folder):
            self.load_folder(current_folder)
        else:
            self.apply_left_table_search_filter()



    def apply_left_filter_to_table(self):
        """Applique les filtres (statut + recherche + pays) sur le tableau de gauche."""
        if hasattr(self, "apply_left_table_search_filter"):
            self.apply_left_table_search_filter()


    def _has_saved_json_for_pdf(self, pdf_path: str) -> bool:
        if not pdf_path:
            return False
        return os.path.exists(self._get_saved_json_path(pdf_path))

    def _set_left_row_status(self, pdf_path: str, status: str):
        """Stocke le status dans la colonne 0 (UserRole+1) pour tes filtres."""
        if not pdf_path or not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                it0.setData(Qt.UserRole + 1, (status or "draft").strip())
                break

    def _set_transporter_match_color(self, ok: bool | None):
        self._transporter_aux_match_ok = ok

        if ok is None:
            self.transporter_info.setStyleSheet("")
            if hasattr(self, "_refresh_transporter_aux_style"):
                self._refresh_transporter_aux_style()
            return

        if ok:
            bg = "#d4edda"
            border = "#28a745"
        else:
            bg = "#f8d7da"
            border = "#dc3545"

        self.transporter_info.setStyleSheet(f"background-color: {bg}; border: 2px solid {border};")
        if hasattr(self, "_refresh_transporter_aux_style"):
            self._refresh_transporter_aux_style()

    def update_transporter_vs_dossiers_status(self):
        """
        Règle demandée :
        - si IBAN/BIC => transporteur trouvé en base
        - et si TOUS les dossiers sont trouvés via :
            SELECT tournr FROM xxatour WHERE tournr IN (...)
        => VERT
        sinon => ROUGE
        """
        dossiers = sorted({d.strip() for d in self.get_folder_numbers() if d and d.strip()})
        if not dossiers:
            self._set_transporter_match_color(None)
            return

        # transporteur non trouvé
        if not self.selected_kundennr:
            self._set_transporter_match_color(False)
            return

        try:
            found = self.tour_repo.get_existing_tournrs_in_xxatour(dossiers)
            missing = set(dossiers) - set(found)
            self._set_transporter_match_color(len(missing) == 0)

            # optionnel: un petit message barre de statut
            if missing:
                self.statusBar().showMessage(f"Transporteur/dossiers incohérents : {len(missing)} dossier(s) non trouvés en xxatour.", 5000)
        except Exception as e:
            # en cas d'erreur SQL => rouge
            self._set_transporter_match_color(False)
            self.statusBar().showMessage(f"Erreur contrôle xxatour : {e}", 5000)

    def _read_saved_invoice_json(self, pdf_path: str) -> dict:
        json_path = self._get_saved_json_path(pdf_path)
        if not os.path.exists(json_path):
            return {}
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}

    def _extract_tournrs_from_saved(self, data: dict) -> list[str]:
        tournrs = []
        folders = data.get("folders") or []
        if isinstance(folders, list):
            for f in folders:
                if isinstance(f, dict):
                    t = f.get("tour_nr") or f.get("TourNr") or f.get("tournr") or ""
                else:
                    t = str(f)
                t = str(t).strip()
                if t:
                    tournrs.append(t)

        if not tournrs:
            t = str(data.get("folder_number") or "").strip()
            if t:
                tournrs.append(t)

        # unique, stable
        return sorted(set(tournrs))

    def _set_left_row_visual(self, row: int, state: str, tooltip: str = ""):
        """
        state: 'ok' | 'error' | 'unknown'
        """
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        it0 = self.pdf_table.item(row, 0)
        if it0:
            it0.setData(Qt.UserRole + 2, state)  # pour le filtre "Erreurs"

        if state == "ok":
            color = QColor(212, 237, 218)   # vert clair
        elif state == "error":
            color = QColor(248, 215, 218)   # rouge clair
        else:
            color = None

        for col in range(self.pdf_table.columnCount()):
            it = self.pdf_table.item(row, col)
            if it is None:
                it = QTableWidgetItem("")
                self.pdf_table.setItem(row, col, it)

            if color is None:
                it.setBackground(QBrush())
            else:
                it.setBackground(color)

            it.setToolTip(tooltip or "")

    def refresh_left_row_processing_state(self, row: int):
        it0 = self.pdf_table.item(row, 0)

        if not it0:
            return
        pdf_path = it0.data(Qt.UserRole)
        if not pdf_path:
            self._set_left_row_visual(row, "unknown", "")
            return

        data = self._read_saved_invoice_json(pdf_path)
        if not data:
            # pas encore sauvegardé => neutre
            self._set_left_row_visual(row, "unknown", "Non sauvegardé.")
            return
        
        # Tag "supprime" => toujours en erreurs
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tags_norm = {str(t).strip().lower() for t in tags if str(t).strip()}

        if "supprime" in tags_norm:
            it0.setData(Qt.UserRole + 3, 1)  # flag "deleted"
            self._set_left_row_visual(row, "error", "Tag 'supprime' : fichier marqué comme supprimé.")
            return

        iban = str(data.get("iban") or "").strip()
        bic = str(data.get("bic") or "").strip()
        tournrs = self._extract_tournrs_from_saved(data)

        if not iban or not bic:
            self._set_left_row_visual(row, "error", "IBAN/BIC manquant dans le JSON.")
            return
        if not tournrs:
            self._set_left_row_visual(row, "error", "Aucun dossier (TourNr) dans le JSON.")
            return

        # 1) transporteur trouvé par iban/bic ?
        try:
            rec = self.transporter_repo.find_transporter_by_bank(iban, bic)
        except Exception as e:
            self._set_left_row_visual(row, "error", f"Erreur SQL transporteur: {e}")
            return

        if not rec:
            self._set_left_row_visual(row, "error", "Transporteur introuvable en base pour cet IBAN/BIC.")
            return

        # 2) tous les dossiers existent dans xxatour ?
        try:
            found = self.tour_repo.get_existing_tournrs_in_xxatour(tournrs)
            missing = sorted(set(tournrs) - set(found))
        except Exception as e:
            self._set_left_row_visual(row, "error", f"Erreur SQL xxatour: {e}")
            return

        if missing:
            more = "" if len(missing) <= 6 else f" (+{len(missing)-6})"
            self._set_left_row_visual(row, "error", f"Dossier(s) manquant(s) en xxatour: {', '.join(missing[:6])}{more}")
            return

        self._set_left_row_visual(row, "ok", "OK : transporteur trouvé + tous les dossiers présents en base.")

    def refresh_left_table_processing_states(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return
        for row in range(self.pdf_table.rowCount()):
            self.refresh_left_row_processing_state(row)    



    def on_ctrl_s_save(self):
        self.save_current_data(show_message=False)

        # ✅ MAJ table de gauche (ligne groupe entry_id)
        entry_id = str(self.selected_invoice_entry_id or "").strip()
        if entry_id:
            iban = self.iban_input.text().strip()
            bic = self.bic_input.text().strip()
            invoice_date = self.date_input.text().strip()

            # pays: si tu as déjà current_transporter_country ou helper
            country = ""
            try:
                if getattr(self, "selected_kundennr", None):
                    country = str(self.transporter_repo.get_lkz_by_kundennr(str(self.selected_kundennr)) or "").strip()
            except Exception:
                country = ""

            self._update_left_row_for_entry(entry_id, invoice_date, iban, bic, country)


    def on_ctrl_m_save_supplier_model(self):
        self.save_supplier_model(show_message=False)


    def _format_percent(self, v: float | None) -> str:
        if v is None:
            return ""
        try:
            fv = float(v)
        except Exception:
            return ""
        if abs(fv - round(fv)) < 1e-9:
            return str(int(round(fv)))
        return f"{fv:.2f}"

    def on_delete_folder_row(self):
        # pas de PDF => pas de sauvegarde/tag
        if not self.current_pdf_path:
            return

        # lignes sélectionnées (ou ligne courante)
        rows = sorted({idx.row() for idx in self.folder_table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            cr = self.folder_table.currentRow()
            if cr >= 0:
                rows = [cr]

        if not rows:
            return

        removed_any = False

        for r in rows:
            dossier_le, amount_le, _ = self._get_row_widgets(r)
            dossier_txt = (dossier_le.text() if dossier_le else "").strip()
            amount_txt = (amount_le.text() if amount_le else "").strip()

            # ne pas supprimer la ligne "vide" de fin
            if not dossier_txt and not amount_txt:
                continue

            self.folder_table.removeRow(r)
            removed_any = True

        if not removed_any:
            return

        # re-garantir une ligne vide en bas + totaux
        self._ensure_empty_folder_row()
        self.update_folder_totals()
        self.update_transporter_vs_dossiers_status()

        # tag + sauvegarde
        self._pending_tags_to_add.add("supprime")
        self.save_current_data(show_message=False)

    def mark_pdf_as_deleted(self, pdf_path: str, filename: str = ""):
        if not pdf_path:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Supprimer")
        msg.setText(
            "Marquer ce fichier comme supprimé ?\n\n"
            f"{filename or os.path.basename(strip_entry_prefix(os.path.basename(pdf_path)))}\n\n"
            "→ Ajoute le tag 'supprime' au JSON et apparaîtra dans le filtre 'Erreurs'."
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        # si c'est le PDF ouvert, on sauvegarde aussi l'état courant de l'UI
        try:
            if self.current_pdf_path == pdf_path:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        json_path = self._get_saved_json_path(pdf_path)

        # load existing JSON (si existe)
        existing = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except Exception:
                existing = {}

        # add tag
        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []

        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.add("supprime")
        existing["tags"] = sorted(tags_set)

        # optionnel: garder une trace
        existing["deleted_at"] = datetime.now().isoformat(timespec="seconds")

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)


        # met aussi le statut SQL à "error"
        try:
            entry_id = ""
            for r in range(self.pdf_table.rowCount()):
                it0 = self.pdf_table.item(r, 0)
                if it0 and it0.data(Qt.UserRole) == pdf_path:
                    entry_id = str(it0.data(Qt.UserRole + 4) or "").strip()
                    break

            if not entry_id:
                entry_id = str(self.logmail_repo.get_entry_id_for_file(os.path.basename(pdf_path)) or "").strip()

            if entry_id:
                self.logmail_repo.set_processing_status_for_entry(entry_id, "error")
        except Exception as e:
            QMessageBox.warning(
                self,
                "Suppression",
                f"Le tag 'supprime' a été enregistré, mais impossible de passer le statut SQL à 'error' :\n{e}"
            )


        # refresh la ligne dans la table gauche + refiltre
        # recharge le dossier courant pour refléter le statut SQL
        current_folder = str(getattr(self, "current_folder_path", "") or "").strip()
        if current_folder and os.path.isdir(current_folder):
            self.load_folder(current_folder)
        else:
            for r in range(self.pdf_table.rowCount()):
                it0 = self.pdf_table.item(r, 0)
                if it0 and it0.data(Qt.UserRole) == pdf_path:
                    self.refresh_left_row_processing_state(r)
                    break
            self.apply_left_table_search_filter()

        self.statusBar().showMessage("Fichier marqué comme supprimé.", 2500)

    def _refresh_transporter_after_bank_autofill(self):
        # équivalent au clic sur IBAN/BIC : on repasse en recherche par banque
        self.transporter_selected_mode = False
        self.selected_kundennr = None

        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        if not iban or not bic:
            return  # on attend d'avoir les deux

        self.check_bank_information()
        self.load_transporter_information(force_by_kundennr=False)

    def compact_folder_rows(self):
        # évite les appels re-entrants
        if getattr(self, "_compacting_folder_rows", False):
            return
        self._compacting_folder_rows = True
        try:
            kept = []
            for r in range(self.folder_table.rowCount()):
                dossier_le, amount_le, _ = self._get_row_widgets(r)
                dossier = (dossier_le.text() if dossier_le else "").strip()
                amount = (amount_le.text() if amount_le else "").strip()

                # on garde les lignes non vides
                if dossier or amount:
                    kept.append((dossier, amount))

            # rebuild table (sans trous)
            self.folder_table.setRowCount(0)
            for dossier, amount in kept:
                self._add_folder_row(dossier=dossier, amount=amount)

            # garde une ligne vide en bas
            self._ensure_empty_folder_row()

            # refresh totaux / statuts
            self.update_folder_totals()
            self.update_transporter_vs_dossiers_status()

        finally:
            self._compacting_folder_rows = False

    def _find_pdf_path_by_filename(self, filename: str) -> str | None:
        """Retrouve le chemin PDF (UserRole) à partir du nom affiché en colonne 0."""
        filename = (filename or "").strip()
        if not filename:
            return None
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if not it0:
                continue
            if left_table_filename_matches(it0, filename):
                return it0.data(Qt.UserRole)
        return None

    def refresh_left_table_processing_claims(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        def _state_background_for_item(item0):
            state = str(item0.data(Qt.UserRole + 2) or "unknown").strip().lower()
            if state == "ok":
                return QColor(212, 237, 218)
            if state == "error":
                return QColor(248, 215, 218)
            return None

        entry_ids = []
        row_entry_map: dict[int, str] = {}

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue
            entry_id = str(it0.data(Qt.UserRole + 4) or "").strip()
            if not entry_id or entry_id.startswith("__NO_ENTRY__"):
                continue
            row_entry_map[row] = entry_id
            entry_ids.append(entry_id)

        try:
            processing_map = self.logmail_repo.get_processing_users_for_entries(entry_ids) or {}
        except Exception:
            processing_map = {}

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            entry_id = row_entry_map.get(row, "")
            processing_user = str(processing_map.get(entry_id, "") if entry_id else "").strip()
            current_user = str(getattr(self, "current_username", "") or "").strip()

            locked = bool(processing_user)
            locked_by_other = locked and processing_user.casefold() != current_user.casefold()
            locked_by_self = locked and not locked_by_other
            base_bg = _state_background_for_item(it0)

            if locked_by_other:
                bg = QColor(255, 199, 206)
                fg = QBrush(QColor(156, 0, 6))
                tooltip_suffix = f"En cours de traitement par {processing_user}."
            elif locked_by_self:
                bg = base_bg
                fg = QBrush()
                tooltip_suffix = "Document en cours de traitement par vous."
            else:
                bg = base_bg
                fg = QBrush()
                tooltip_suffix = ""

            for col in range(self.pdf_table.columnCount()):
                it = self.pdf_table.item(row, col)
                if it is None:
                    it = QTableWidgetItem("")
                    self.pdf_table.setItem(row, col, it)

                font = it.font()
                font.setBold(locked)
                it.setFont(font)
                it.setForeground(fg)

                if bg is None:
                    it.setBackground(QBrush())
                else:
                    it.setBackground(bg)

                current_tt = str(it.toolTip() or "").strip()
                tt_lines = [
                    line.strip()
                    for line in current_tt.splitlines()
                    if line.strip()
                    and not line.strip().startswith("En cours de traitement par ")
                    and line.strip() != "Document en cours de traitement par vous."
                ]
                base_tt = "\n".join(tt_lines).strip()

                if tooltip_suffix:
                    it.setToolTip(f"{base_tt}\n{tooltip_suffix}".strip() if base_tt else tooltip_suffix)
                else:
                    it.setToolTip(base_tt)


    def _block_validate_if_transporter_not_matching_tours(self) -> bool:
        tournrs = sorted({
            (r.get("tour_nr") or "").strip()
            for r in self.get_folder_rows()
            if (r.get("tour_nr") or "").strip()
        })

        if not tournrs:
            QMessageBox.warning(
                self,
                "Validation",
                "Aucun dossier (TourNr) saisi."
            )
            return False

        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()

        # fallback si le champ contient "Nom (12345)"
        if not kundennr:
            m = re.search(r"\(([^()]+)\)\s*$", self.transporter_input.text() or "")
            if m:
                kundennr = m.group(1).strip()

        if not kundennr:
            QMessageBox.warning(
                self,
                "Validation",
                "Aucun transporteur OCR / KundenNr trouvé. Validation impossible."
            )
            return False

        try:
            matching = self.tour_repo.get_tournrs_matching_ffnr(tournrs, kundennr)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Validation",
                f"Erreur contrôle transporteur / tournées dans xxatour :\n{e}"
            )
            return False

        invalid = [t for t in tournrs if t not in matching]

        if invalid:
            tour_bad = invalid[0]

            try:
                ws_transporter = self.tour_repo.get_ffnr_for_tour(tour_bad)
            except Exception:
                ws_transporter = ""

            QMessageBox.warning(
                self,
                "Validation impossible",
                "Le transporteur ne correspond pas au transporteur de la tournée Winsped.\n\n"
                f"Transporteur OCR : {kundennr}\n"
                f"Transporteur Winsped : {ws_transporter or '(inconnu)'}\n"
                f"pour la tournée {tour_bad}"
            )
            return False

        return True

    def _block_validate_if_ht_amounts_not_matching_tours(self) -> bool:
        rows = self.get_folder_rows()

        # on ne garde que les lignes avec dossier + montant OCR
        pairs = []
        for r in rows:
            tour_nr = str(r.get("tour_nr") or "").strip()
            amount_txt = str(r.get("amount_ht_ocr") or "").strip()
            if tour_nr and amount_txt:
                pairs.append((tour_nr, amount_txt))

        if not pairs:
            QMessageBox.warning(
                self,
                "Validation impossible",
                "Aucun montant HT OCR renseigné pour les tournées."
            )
            return False

        tournrs = [t for t, _ in pairs]

        try:
            kosten_map = self.tour_repo.get_kosten_by_tournrs(tournrs)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Validation impossible",
                f"Erreur contrôle montants xxatour :\n{e}"
            )
            return False

        invalid = []

        for tour_nr, amount_txt in pairs:
            try:
                ocr_amount = self._parse_amount(amount_txt)
            except Exception:
                ocr_amount = None

            ws_amount = kosten_map.get(tour_nr)

            if ocr_amount is None or ws_amount is None:
                invalid.append((tour_nr, amount_txt, ws_amount))
                continue

            # tolérance centime
            if abs(float(ocr_amount) - float(ws_amount)) > 0.01:
                invalid.append((tour_nr, amount_txt, ws_amount))

        if invalid:
            tour_bad, ocr_bad, ws_bad = invalid[0]
            ws_txt = "" if ws_bad is None else f"{float(ws_bad):.2f}"

            QMessageBox.warning(
                self,
                "Validation impossible",
                "Le montant HT OCR ne correspond pas au montant HT de la tournée Winsped.\n\n"
                f"Montant HT OCR : {ocr_bad}\n"
                f"Montant HT Winsped : {ws_txt or '(inconnu)'}\n"
                f"pour la tournée {tour_bad}"
            )
            return False

        return True
    
    def _copy_validated_pdf_to_dms(self):
        pdf_path = str(getattr(self, "current_pdf_path", "") or "").strip()
        if not pdf_path or not os.path.isfile(pdf_path):
            raise FileNotFoundError("PDF courant introuvable.")

        target_dir = str(DMS_EXPORT_FOLDER or "").strip()
        if not target_dir:
            raise RuntimeError("DMS_EXPORT_FOLDER n'est pas configuré.")

        os.makedirs(target_dir, exist_ok=True)

        def copy_src_to_target(src_path: str) -> str:
            src_path = str(src_path or "").strip()
            if not src_path or not os.path.isfile(src_path):
                return ""

            if os.path.dirname(os.path.abspath(src_path)) == os.path.abspath(target_dir):
                # déjà dans le dossier cible
                return os.path.abspath(src_path)

            name = os.path.basename(src_path)
            dest_path = os.path.join(target_dir, name)
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(name)
                i = 1
                while True:
                    candidate = os.path.join(target_dir, f"{base}_{i}{ext}")
                    if not os.path.exists(candidate):
                        dest_path = candidate
                        break
                    i += 1

            shutil.copy2(src_path, dest_path)
            return dest_path

        self._dms_copied_paths = getattr(self, "_dms_copied_paths", {}) or {}

        # Copie principal
        copied_main_path = copy_src_to_target(pdf_path)
        if copied_main_path:
            self._dms_copied_paths[os.path.abspath(pdf_path)] = copied_main_path

        # Copier tous les fichiers du même groupe (entry_pdf_paths)
        for p in (self.entry_pdf_paths or []):
            if not p:
                continue
            p = str(p or "").strip()
            if p and p != pdf_path:
                copied_path = copy_src_to_target(p)
                if copied_path:
                    self._dms_copied_paths[os.path.abspath(p)] = copied_path

        # split CMR pages en fichiers dédiés dans DMS
        try:
            cmr_splits = self._split_cmr_pages_for_validation(pdf_path, target_dir, entry_id=str(getattr(self, "selected_invoice_entry_id", "") or ""))
            self._cmr_splits = getattr(self, "_cmr_splits", {}) or {}
            self._cmr_splits[pdf_path] = cmr_splits
            for split_path in (cmr_splits or {}).values():
                if split_path:
                    self._dms_copied_paths[os.path.abspath(split_path)] = os.path.abspath(split_path)
        except Exception:
            self._cmr_splits = getattr(self, "_cmr_splits", {}) or {}

        return copied_main_path or ""
    
    def _collect_validation_csv_rows(self) -> list[list[str]]:
        """
        Retourne les lignes CSV :
        dossier ; aufintnr ; aufnr ; type ; chemin_document
        """
        rows: list[list[str]] = []

        # 1) Lignes facture : une ligne par dossier
        invoice_tours = sorted({
            str(r.get("tour_nr") or "").strip()
            for r in (self.get_folder_rows() or [])
            if str(r.get("tour_nr") or "").strip()
        })

        invoice_path = str(getattr(self, "current_pdf_path", "") or "").strip()
        invoice_path_copied = str(self._dms_copied_paths.get(os.path.abspath(invoice_path), invoice_path) or "").strip()

        for tour_nr in invoice_tours:
            rows.append([tour_nr, "", "", "Facture", invoice_path_copied])

        # 2) Lignes CMR : une ligne par couple dossier / aufnr
        seen = set()
        cmr_splits = getattr(self, "_cmr_splits", {}) or {}

        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}

            # nouveau format page-aware
            page_links = data.get("cmr_page_links")
            if isinstance(page_links, list) and page_links:
                for link in page_links:
                    tour_nr = str(link.get("tour_nr") or "").strip()
                    aufnr = str(link.get("auf_nr") or "").strip()
                    page_no = int(link.get("page") or 0)
                    if not tour_nr or not aufnr:
                        continue

                    cmr_path = p
                    file_splits = cmr_splits.get(p, {}) if isinstance(cmr_splits, dict) else {}
                    if isinstance(file_splits, dict) and page_no and page_no in file_splits:
                        cmr_path = file_splits.get(page_no, p)

                    key = (tour_nr, aufnr, "CMR", cmr_path)
                    if key in seen:
                        continue
                    seen.add(key)

                    try:
                        aufintnr = self.tour_repo.get_aufintnr_by_aufnr(aufnr)
                    except Exception:
                        aufintnr = ""

                    rows.append([tour_nr, aufintnr, aufnr, "CMR", str(self._dms_copied_paths.get(os.path.abspath(cmr_path), cmr_path) or "").strip()])
                continue

            # ancien format legacy
            tour_nr = str(data.get("cmr_tour_nr") or "").strip()
            aufnr = str(data.get("cmr_auf_nr") or "").strip()
            if tour_nr and aufnr:
                key = (tour_nr, aufnr, "CMR", p)
                if key not in seen:
                    seen.add(key)
                    try:
                        aufintnr = self.tour_repo.get_aufintnr_by_aufnr(aufnr)
                    except Exception:
                        aufintnr = ""
                    rows.append([tour_nr, aufintnr, aufnr, "CMR", str(p or "").strip()])

        return rows


    def _export_validation_csv(self) -> str:
        """
        Crée un CSV horodaté dans le dossier configurable.
        Retourne le chemin du fichier créé.
        """
        target_dir = str(CSV_EXPORT_DIR or "").strip()
        if not target_dir:
            raise RuntimeError("CSV_EXPORT_DIR n'est pas configuré.")

        os.makedirs(target_dir, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        invoice_no = str(self.invoice_number_input.text() or "").strip()
        safe_invoice_no = re.sub(r'[<>:"/\\\\|?*]+', "_", invoice_no) if invoice_no else "SANS_NUMERO"
        filename = f"{ts}_{safe_invoice_no}.csv"
        csv_path = os.path.join(target_dir, filename)

        rows = self._collect_validation_csv_rows()

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["dossier", "aufintnr", "aufnr", "type", "chemin_document"])
            writer.writerows(rows)

        return csv_path
    

    def _get_invoice_number_and_kundennr_for_dupecheck(self) -> tuple[str, str]:
        """Retourne (NrBuch, FakAdr) pour la vérification XXARe."""
        invoice_nr = (self.invoice_number_input.text() or "").strip()
        kundennr = (self.selected_kundennr or "").strip()

        # fallback si selected_kundennr vide mais champ "Nom (12345)"
        if not kundennr:
            m = re.search(r"\((\d+)\)\s*$", self.transporter_input.text() or "")
            if m:
                kundennr = m.group(1)

        return invoice_nr, kundennr


    def _add_tag_to_current_json(self, tag: str, extra: dict | None = None) -> None:
        """Ajoute un tag dans le JSON du document courant (sans écraser le reste)."""
        if not self.current_pdf_path:
            return
        try:
            json_path = self._get_saved_json_path(self.current_pdf_path)
            if not os.path.exists(json_path):
                return
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}

            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            if not isinstance(tags, list):
                tags = []
            tags_set = {str(t).strip() for t in tags if str(t).strip()}
            if tag:
                tags_set.add(tag)
            data["tags"] = sorted(tags_set)

            if extra:
                data.update(extra)

            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            return


    def _mark_current_entry_as_error(self, reason: str = "duplicate_invoice") -> None:
        """Met l'entrée en ERROR (listing erreurs) + trace dans le JSON."""
        try:
            # status=error => update SQL (processing_status) + JSON
            self.save_current_data(status="error", show_message=False)
        except Exception:
            pass

        self._add_tag_to_current_json("duplicate_invoice", extra={"error_reason": reason} if reason else None)

        # refresh + se placer sur l'onglet erreurs
        try:
            self.set_left_filter("errors")
        except Exception:
            try:
                self.load_folder(self.current_folder_path)
            except Exception:
                pass


    def _maybe_prompt_duplicate_invoice(self) -> bool:
        """Vérifie XXARe et propose de mettre en erreur si doublon.

        Retourne True si pas de doublon, sinon False.
        """
        invoice_nr, kundennr = self._get_invoice_number_and_kundennr_for_dupecheck()
        if not invoice_nr or not kundennr:
            return True

        key = f"{invoice_nr}::{kundennr}"
        if getattr(self, "_last_dupe_prompt_key", None) == key:
            return False

        try:
            exists = bool(self.xxare_repo.invoice_exists(invoice_nr, kundennr, aufdk="D"))
        except Exception:
            return True

        if not exists:
            try:
                self.invoice_number_input.setStyleSheet("")
            except Exception:
                pass
            return True

        self._last_dupe_prompt_key = key
        try:
            self.invoice_number_input.setStyleSheet("background-color:#fff3cd;")
        except Exception:
            pass

        resp = QMessageBox.question(
            self,
            "Facture déjà existante",
            "Cette facture existe déjà, voulez-vous la mettre en erreur ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if resp == QMessageBox.Yes:
            self._mark_current_entry_as_error(reason="duplicate_invoice")
        return False


    def on_invoice_number_editing_finished(self):
        """Contrôle doublon dès qu'on quitte le champ N° facture."""
        try:
            self._maybe_prompt_duplicate_invoice()
        except Exception:
            pass

    def _to_sql_decimal_2(self, value) -> Decimal | None:
        if value is None:
            return None

        s = str(value).strip()
        if not s:
            return None

        s = s.replace("\u00A0", "").replace(" ", "")
        s = s.replace(",", ".")

        try:
            return Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None

    def _parse_invoice_date_for_sql(self, text: str):
        s = str(text or "").strip()
        if not s:
            raise ValueError("Date de facture vide.")

        formats = (
            "%d/%m/%Y",
            "%d.%m.%Y",
            "%d-%m-%Y",
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y.%m.%d",
            "%d/%m/%y",
            "%d.%m.%y",
            "%d-%m-%y",
        )

        for fmt in formats:
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                pass

        raise ValueError(f"Format de date non reconnu pour RechDat : {s}")

    def _resolve_lisinvoice_taux(self, tour_nrs: list[str]) -> Decimal:
        """
        1) on préfère le taux saisi/extrait dans la zone TVA
        2) sinon fallback sur la TVA théorique BDD des tournées
        3) on refuse s'il y a plusieurs taux distincts
        """
        rates: set[Decimal] = set()

        for row in (self.get_vat_rows() or []):
            rate = self._to_sql_decimal_2(row.get("rate"))
            base = self._to_sql_decimal_2(row.get("base"))
            vat = self._to_sql_decimal_2(row.get("vat"))

            if rate is None and base is None and vat is None:
                continue

            if rate is not None:
                rates.add(rate)

        if len(rates) == 1:
            return next(iter(rates))

        if len(rates) > 1:
            raise ValueError(
                "Plusieurs taux TVA détectés dans la facture : impossible d'alimenter "
                "LISINVOICE_EDTRANS car la colonne Taux est unique."
            )

        theo_rates: set[Decimal] = set()

        for tour_nr in tour_nrs:
            try:
                val = self.tour_repo.get_theoretical_vat_percent_by_tournr(tour_nr)
            except Exception:
                val = None

            dec = self._to_sql_decimal_2(val)
            if dec is not None:
                theo_rates.add(dec)

        if len(theo_rates) == 1:
            return next(iter(theo_rates))

        if len(theo_rates) > 1:
            raise ValueError(
                "Plusieurs taux TVA théoriques trouvés sur les tournées : impossible "
                "d'alimenter LISINVOICE_EDTRANS car la colonne Taux est unique."
            )

        raise ValueError("Aucun taux TVA exploitable trouvé pour LISINVOICE_EDTRANS.")

    def _build_lisinvoice_rows(self) -> list[dict]:
        invoice_nr, kundennr = self._get_invoice_number_and_kundennr_for_dupecheck()

        if not invoice_nr:
            raise ValueError("Numéro de facture vide.")
        if not kundennr:
            raise ValueError("KundenNr transporteur vide.")

        rech_dat = self._parse_invoice_date_for_sql(self.date_input.text())

        # une ligne LISINVOICE par tournée
        pairs_by_tour: dict[str, Decimal] = {}

        for row in self.get_folder_rows():
            tour_nr = str(row.get("tour_nr") or "").strip()
            ht = self._to_sql_decimal_2(row.get("amount_ht_ocr"))

            if tour_nr and ht is not None:
                pairs_by_tour[tour_nr] = ht

        if not pairs_by_tour:
            raise ValueError("Aucune tournée avec montant HT exploitable.")

        taux = self._resolve_lisinvoice_taux(list(pairs_by_tour.keys()))
        factor = (Decimal("100.00") + taux) / Decimal("100.00")

        try:
            kunden_value = int(str(kundennr).strip())
        except Exception:
            kunden_value = str(kundennr).strip()

        rows: list[dict] = []

        for tour_nr, ht in pairs_by_tour.items():
            ttc = (ht * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            rows.append({
                "rech_nr": str(invoice_nr).strip(),
                "rech_dat": rech_dat,
                "ht": ht,
                "ttc": ttc,
                "taux": taux,
                "kunden_nr": kunden_value,
                "tour_nr": str(tour_nr).strip(),
                "import_value": "NON",
            })

        return rows

    def _push_lisinvoice_rows(self) -> list[str]:
        errors: list[str] = []

        rows = self._build_lisinvoice_rows()

        for row in rows:
            try:
                self.lisinvoice_repo.upsert_invoice_row(
                    rech_nr=row["rech_nr"],
                    rech_dat=row["rech_dat"],
                    ht=row["ht"],
                    ttc=row["ttc"],
                    taux=row["taux"],
                    kunden_nr=row["kunden_nr"],
                    tour_nr=row["tour_nr"],
                    import_value=row["import_value"],
                )
            except Exception as e:
                errors.append(f'{row["tour_nr"]} : {e}')

        return errors

