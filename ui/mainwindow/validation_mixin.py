from __future__ import annotations

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
    
        # ✅ Met à jour le modèle transporteur (patterns) à la validation
        try:
            self.save_supplier_model(show_message=False)
        except Exception:
            # ne bloque pas la validation si le modèle échoue
            pass

        # 2) Déterminer la valeur à appliquer selon blocage
        doc_name = os.path.basename(self.current_pdf_path)
        blocked = bool((self.block_options.get(doc_name, {}) or {}).get("blocked", False))
        value = 601 if blocked else 600
        comment = str((self.block_options.get(doc_name, {}) or {}).get("comment", "") or "").strip()

        # 3) Dossiers (TourNr) -> updates SQL
        tournrs = sorted({
            (r.get("tour_nr") or "").strip()
            for r in self.get_folder_rows()
            if (r.get("tour_nr") or "").strip()
        })

        if not tournrs:
            QMessageBox.warning(self, "Validation", "Aucun dossier (TourNr) trouvé : pas de mise à jour SQL.")
            return

        errors = []
        for t in tournrs:
            try:
                self.tour_repo.set_infosymbol18_for_tournr(t, value=value)
                self.tour_repo.set_block_status_for_tournr(t, is_blocked=blocked, motif=comment)
            except Exception as e:
                errors.append(f"{t} : {e}")

        if errors:
            QMessageBox.warning(
                self,
                "Validation",
                "Facture VALIDÉE et sauvegardée.\n\nErreurs SQL:\n" + "\n".join(errors)
            )
        else:
            suffix = " (document BLOQUÉ)" if blocked else ""
            QMessageBox.information(
                self,
                "Validation",
                f"Facture VALIDÉE et sauvegardée. Dossier(s){suffix}."
            )

        # Recharge le pool "En attente" pour faire entrer la suivante
        try:
            current_folder = getattr(self, "current_folder_path", None)
            if current_folder and os.path.isdir(current_folder):
                self.load_folder(current_folder)

                # optionnel : sélectionne automatiquement la première ligne du nouveau pool
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
        self.apply_left_filter_to_table()

    def apply_left_filter_to_table(self):
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if not it0:
                continue

            deleted = bool(it0.data(Qt.UserRole + 3))
            if deleted:
                visible = (self.left_filter_mode == "errors")
                self.pdf_table.setRowHidden(row, not visible)
                continue           

            status = (it0.data(Qt.UserRole + 1) or "draft").strip()

            if self.left_filter_mode == "pending":
                visible = (status != "validated")
            elif self.left_filter_mode == "validated":
                visible = (status == "validated")
            elif self.left_filter_mode == "errors":
                state = (it0.data(Qt.UserRole + 2) or "").strip()
                visible = (state == "error")

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

    def _add_fee_row(self, gebnr: str, bez: str, amount: str = ""):
        row = self.fees_table.rowCount()
        self.fees_table.insertRow(row)

        it0 = QTableWidgetItem(str(gebnr))
        it0.setFlags(it0.flags() & ~Qt.ItemIsEditable)
        it1 = QTableWidgetItem(str(bez))
        it1.setFlags(it1.flags() & ~Qt.ItemIsEditable)

        self.fees_table.setItem(row, 0, it0)
        self.fees_table.setItem(row, 1, it1)

        le = QLineEdit()
        le.setPlaceholderText("Montant")
        le.setClearButtonEnabled(True)
        le.setText("" if amount is None else str(amount))
        le.mousePressEvent = lambda e, f=le: self.set_active_field(f)  # si tu veux le click->champ actif
        self.fees_table.setCellWidget(row, 2, le)

    def on_add_fee(self):
        dlg = GebSearchDialog(self.geb_repo, self)

        if dlg.exec() != QDialog.Accepted or not dlg.selected:
            return

        gebnr = dlg.selected["gebnr"]
        bez = dlg.selected["bez"]

        # éviter doublon
        for r in range(self.fees_table.rowCount()):
            it = self.fees_table.item(r, 0)
            if it and it.text().strip() == gebnr:
                return

        self._add_fee_row(gebnr, bez, "")

    def on_remove_fee(self):
        rows = sorted({idx.row() for idx in self.fees_table.selectionModel().selectedRows()}, reverse=True)
        for r in rows:
            self.fees_table.removeRow(r)

    def get_fee_rows(self):
        out = []
        for r in range(self.fees_table.rowCount()):
            gebnr = (self.fees_table.item(r, 0).text().strip() if self.fees_table.item(r, 0) else "")
            bez = (self.fees_table.item(r, 1).text().strip() if self.fees_table.item(r, 1) else "")
            le = self.fees_table.cellWidget(r, 2)
            amount = (le.text().strip() if le else "")
            if gebnr or bez or amount:
                out.append({"gebnr": gebnr, "bez": bez, "amount": amount})
        return out

    def rebuild_fees_from_json(self, data: dict):
        self.fees_table.setRowCount(0)
        fees = data.get("fees", [])
        if isinstance(fees, list):
            for f in fees:
                if not isinstance(f, dict):
                    continue
                gebnr = str(f.get("gebnr", "") or "").strip()
                bez = str(f.get("bez", "") or "").strip()
                amount = str(f.get("amount", "") or "").strip()
                if gebnr or bez or amount:
                    self._add_fee_row(gebnr, bez, amount)

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
            f"{filename or os.path.basename(pdf_path)}\n\n"
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

        # refresh la ligne dans la table gauche + refiltre
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                self.refresh_left_row_processing_state(r)
                break

        self.apply_left_filter_to_table()
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

