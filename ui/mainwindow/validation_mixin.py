from __future__ import annotations
from csv import writer

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

        # 2) Met à jour le modèle transporteur (patterns)
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            pass

        # 3) Déterminer la valeur à appliquer selon blocage
        doc_name = os.path.basename(self.current_pdf_path)
        blocked = bool((self.block_options.get(doc_name, {}) or {}).get("blocked", False))
        value = 601 if blocked else 600
        comment = str((self.block_options.get(doc_name, {}) or {}).get("comment", "") or "").strip()

        # 4) Updates SQL
        errors = []
        for t in tournrs:
            try:
                self.tour_repo.set_infosymbol18_for_tournr(t, value=value)
                self.tour_repo.set_block_status_for_tournr(t, is_blocked=blocked, motif=comment)
            except Exception as e:
                errors.append(f"{t} : {e}")

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
        if errors:
            msg = "Facture VALIDÉE et sauvegardée.\n\nErreurs SQL :\n" + "\n".join(errors)

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
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        mode = str(getattr(self, "left_filter_mode", "pending") or "pending").strip().lower()

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                self.pdf_table.setRowHidden(row, True)
                continue

            status = str(it0.data(Qt.UserRole + 1) or "pending").strip().lower()

            if mode == "pending":
                visible = (status == "pending")
            elif mode == "validated":
                visible = (status == "validated")
            elif mode == "errors":
                visible = (status == "error")
            else:
                visible = True

            self.pdf_table.setRowHidden(row, not visible)

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
        """
        ok=True  => vert
        ok=False => rouge
        ok=None  => neutre
        """
        if ok is None:
            self.transporter_info.setStyleSheet("")
            # si tu veux aussi colorer le champ TVA transporteur :
            self.transporter_vat_input.setStyleSheet("background-color: #f3f3f3;")
            return

        if ok:
            bg = "#d4edda"  # vert clair
            border = "#28a745"
        else:
            bg = "#f8d7da"  # rouge clair
            border = "#dc3545"

        self.transporter_info.setStyleSheet(f"background-color: {bg}; border: 2px solid {border};")
        self.transporter_vat_input.setStyleSheet(f"background-color: {bg}; border: 2px solid {border};")

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
        # Ctrl+S = pas de popup, juste statusbar
        self.save_current_data(show_message=False)

        # MAJ modèle supplier en silencieux
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            pass

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
            if it0 and (it0.text() or "").strip() == filename:
                return it0.data(Qt.UserRole)
        return None

    def refresh_left_table_processing_claims(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

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
            processing_user = processing_map.get(entry_id, "") if entry_id else ""

            locked = bool(processing_user)
            locked_by_other = locked and processing_user != getattr(self, "current_username", "")

            for col in range(self.pdf_table.columnCount()):
                it = self.pdf_table.item(row, col)
                if it is None:
                    it = QTableWidgetItem("")
                    self.pdf_table.setItem(row, col, it)

                font = it.font()
                font.setBold(locked)
                it.setFont(font)

                if locked_by_other:
                    it.setForeground(QBrush(QColor(200, 0, 0)))
                elif locked:
                    it.setForeground(QBrush(QColor(140, 0, 0)))
                else:
                    it.setForeground(QBrush())

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

        filename = os.path.basename(pdf_path)
        target_path = os.path.join(target_dir, filename)

        # évite l’écrasement si le fichier existe déjà
        if os.path.exists(target_path):
            base, ext = os.path.splitext(filename)
            i = 1
            while True:
                candidate = os.path.join(target_dir, f"{base}_{i}{ext}")
                if not os.path.exists(candidate):
                    target_path = candidate
                    break
                i += 1

        shutil.copy2(pdf_path, target_path)
        return target_path
    
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

        for tour_nr in invoice_tours:
            rows.append([tour_nr, "", "", "Facture", invoice_path])

        # 2) Lignes CMR : une ligne par couple dossier / aufnr
        seen = set()
        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}

            # nouveau format page-aware
            page_links = data.get("cmr_page_links")
            if isinstance(page_links, list) and page_links:
                for link in page_links:
                    tour_nr = str(link.get("tour_nr") or "").strip()
                    aufnr = str(link.get("auf_nr") or "").strip()
                    if not tour_nr or not aufnr:
                        continue

                    key = (tour_nr, aufnr, "CMR", p)
                    if key in seen:
                        continue
                    seen.add(key)

                    try:
                        aufintnr = self.tour_repo.get_aufintnr_by_aufnr(aufnr)
                    except Exception:
                        aufintnr = ""

                    rows.append([tour_nr, aufintnr, aufnr, "CMR", str(p or "").strip()])
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
        target_dir = str(CSV_EXPORT_FOLDER or "").strip()
        if not target_dir:
            raise RuntimeError("CSV_EXPORT_FOLDER n'est pas configuré.")

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

