from __future__ import annotations
from csv import writer
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled


class MainWindowValidationMixin:

    def _get_validation_document_path(self) -> str | None:
        view_path = str(getattr(self, "view_pdf_path", "") or "").strip()
        current_path = str(getattr(self, "current_pdf_path", "") or "").strip()

        try:
            group_paths = [
                str(p or "").strip()
                for p in (getattr(self, "entry_pdf_paths", []) or [])
                if str(p or "").strip() and os.path.isfile(str(p or "").strip())
            ]
            chosen = self._choose_invoice_source_document(
                group_paths,
                fallback_path=view_path or current_path,
            )
            if chosen and os.path.isfile(chosen):
                return chosen
        except Exception:
            pass

        for candidate in (view_path, current_path):
            if candidate and os.path.isfile(candidate):
                return candidate

        return None

    def on_validate_invoice(self):
        if self._is_typing_in_input():
            return
        if not self.current_pdf_path:
            self.statusBar().showMessage("Aucun PDF sélectionné.", 3000)
            return
        if hasattr(self, "_warn_if_invoice_validated_locked") and self._warn_if_invoice_validated_locked("valider à nouveau"):
            return
        if not self._block_validate_if_missing_cmr():
            return
        if not self._block_validate_if_transporter_not_matching_tours():
            return
        if not self._block_validate_if_iban_not_matching_transporter():
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

        # Vérifier que la date de facture est remplie ET réellement valide
        if hasattr(self, "_validate_invoice_date_for_validation"):
            if not self._validate_invoice_date_for_validation():
                return
        else:
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
        validation_pdf_path = self._get_validation_document_path() or str(getattr(self, "current_pdf_path", "") or "").strip()
        self._last_validation_invoice_pdf_path = validation_pdf_path
        self.save_current_data(status="validated", show_message=False, pdf_path=validation_pdf_path)

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
        doc_name = os.path.basename(validation_pdf_path or self.current_pdf_path or "")
        if hasattr(self, "_get_effective_block_state_for_database"):
            blocked, comment = self._get_effective_block_state_for_database(
                getattr(self, "block_options", {}) or {},
                preferred_doc_name=doc_name,
            )
        else:
            blocked = bool((self.block_options.get(doc_name, {}) or {}).get("blocked", False))
            comment = str((self.block_options.get(doc_name, {}) or {}).get("comment", "") or "").strip()

        # 4) Updates SQL
        if hasattr(self, "_apply_block_state_to_database"):
            mail_meta = self._get_block_mail_metadata(doc_name) if blocked and hasattr(self, "_get_block_mail_metadata") else {"expediteur": "", "sujet": ""}
            errors = self._apply_block_state_to_database(
                tournrs,
                blocked=blocked,
                comment=comment,
                mail_expediteur=mail_meta.get("expediteur", ""),
                mail_objet=mail_meta.get("sujet", ""),
            )
        else:
            errors = []
            value = 601 if blocked else 600
            ocr_user = str(getattr(self, "current_username", "") or "").strip()
            for t in tournrs:
                try:
                    self.tour_repo.set_infosymbol18_for_tournr(t, value=value)
                    self.tour_repo.set_ocr_user_for_tournr(t, ocr_user=ocr_user)
                    mail_meta = self._get_block_mail_metadata(doc_name) if blocked and hasattr(self, "_get_block_mail_metadata") else {"expediteur": "", "sujet": ""}
                    self.tour_repo.set_block_status_for_tournr(
                        t,
                        is_blocked=blocked,
                        motif=comment,
                        ocr_user=ocr_user,
                        ocr_expediteur=mail_meta.get("expediteur", ""),
                        ocr_objet=mail_meta.get("sujet", ""),
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
            dms_path = self._copy_validated_pdf_to_dms(pdf_path=validation_pdf_path)
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



        # La facture courante vient d'être validée : on verrouille immédiatement
        # l'édition, même avant le prochain rechargement de la liste.
        try:
            if hasattr(self, "_apply_invoice_validated_lock"):
                self._apply_invoice_validated_lock(True)
        except Exception:
            pass

        # 7) Après validation d'un document provenant des écarts,
        #    revenir automatiquement sur le filtre En attente.
        try:
            current_folder = getattr(self, "current_folder_path", None)
            if current_folder and os.path.isdir(current_folder):
                previous_filter = str(getattr(self, "left_filter_mode", "pending") or "pending").strip().lower()

                if previous_filter == "ecart":
                    self.set_left_filter("pending")
                else:
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
        - le transporteur vient du premier dossier (xxatour.FFNR)
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

    def _score_invoice_source_document(self, pdf_path: str, data: dict | None = None) -> int:
        pdf_path = str(pdf_path or "").strip()
        if not pdf_path:
            return -1

        data = data if isinstance(data, dict) else (self._read_saved_invoice_json(pdf_path) or {})
        score = 0

        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tags_norm = {str(t).strip().lower() for t in tags if str(t).strip()}

        explicit_source_flags = [
            data.get("invoice_source_document"),
            data.get("is_invoice_source_document"),
            data.get("ocr_source_document"),
        ]
        if any(bool(v) for v in explicit_source_flags):
            score += 100000

        invoice_number = str(data.get("invoice_number") or "").strip()
        invoice_date = str(data.get("invoice_date") or "").strip()
        iban = str(data.get("iban") or "").strip()
        bic = str(data.get("bic") or "").strip()
        transporter = str(data.get("transporter_text") or "").strip()
        folders = self._extract_tournrs_from_saved(data)
        vat_lines = data.get("vat_lines") or []
        if not isinstance(vat_lines, list):
            vat_lines = []
        ocr_text = str(data.get("ocr_text") or "")

        if invoice_number:
            score += 250
        if invoice_date:
            score += 140
        if iban and bic:
            score += 180
        elif iban or bic:
            score += 60
        if transporter:
            score += 40
        if folders:
            score += 220 + min(120, len(folders) * 12)
        if vat_lines:
            score += 80 + min(60, len(vat_lines) * 10)
        if ocr_text:
            score += min(220, max(20, len(ocr_text) // 40))

        filename = os.path.basename(pdf_path).lower()
        if any(token in filename for token in ("invoice", "facture", "fv", "fac")):
            score += 35
        if any(token in filename for token in ("cmr", "pod", "bl", "bonliv")):
            score -= 120
        if "_cmr_" in filename or filename.endswith("_cmr.pdf"):
            score -= 250
        if "supprime" in tags_norm:
            score -= 1000

        return score

    def _choose_invoice_source_document(self, group_paths: list[str] | None, fallback_path: str | None = None) -> str:
        clean_paths: list[str] = []
        seen = set()

        for p in (group_paths or []):
            p = str(p or "").strip()
            if not p or not os.path.isfile(p):
                continue
            ap = os.path.abspath(p)
            if ap in seen:
                continue
            seen.add(ap)
            clean_paths.append(p)

        fallback_path = str(fallback_path or "").strip()
        if fallback_path and os.path.isfile(fallback_path):
            ap = os.path.abspath(fallback_path)
            if ap not in seen:
                clean_paths.append(fallback_path)
                seen.add(ap)

        if not clean_paths:
            return fallback_path if fallback_path and os.path.isfile(fallback_path) else ""

        best_path = clean_paths[0]
        best_score = self._score_invoice_source_document(best_path)

        for p in clean_paths[1:]:
            score = self._score_invoice_source_document(p)
            if score > best_score:
                best_path = p
                best_score = score

        return best_path

    def _set_invoice_source_document(self, pdf_path: str) -> None:
        pdf_path = str(pdf_path or "").strip()
        if not pdf_path:
            return

        target_abs = os.path.abspath(pdf_path)
        entry_id = str(self._resolve_current_entry_id(pdf_path) or "").strip()
        sibling_paths: list[str] = []

        current_dir = os.path.dirname(pdf_path)
        if entry_id:
            try:
                rows = self.logmail_repo.get_files_for_entry(entry_id) or []
            except Exception:
                rows = []

            for r in rows:
                name = str(r.get("nom_pdf") or r.get("Nom_PDF") or r.get("filename") or "").strip()
                if not name:
                    continue
                candidate = os.path.join(current_dir, name)
                if os.path.isfile(candidate):
                    sibling_paths.append(candidate)

        for p in (getattr(self, "entry_pdf_paths", []) or []):
            p = str(p or "").strip()
            if p and os.path.isfile(p):
                sibling_paths.append(p)

        sibling_paths.append(pdf_path)

        seen = set()
        for candidate in sibling_paths:
            candidate = str(candidate or "").strip()
            if not candidate or not os.path.isfile(candidate):
                continue
            abs_candidate = os.path.abspath(candidate)
            if abs_candidate in seen:
                continue
            seen.add(abs_candidate)

            data = self._read_saved_invoice_json(candidate) or {}
            if not isinstance(data, dict):
                data = {}

            is_target = abs_candidate == target_abs
            changed = False
            for key in ("invoice_source_document", "is_invoice_source_document", "ocr_source_document"):
                new_value = is_target
                if bool(data.get(key)) != new_value:
                    data[key] = new_value
                    changed = True

            if not changed:
                continue

            json_path = self._get_saved_json_path(candidate)
            try:
                os.makedirs(os.path.dirname(json_path), exist_ok=True)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

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

        tournrs = self._extract_tournrs_from_saved(data)

        if not tournrs:
            self._set_left_row_visual(row, "error", "Aucun dossier (TourNr) dans le JSON.")
            return

        first_tour = ""
        folders_saved = data.get("folders") or []
        if isinstance(folders_saved, list):
            for f in folders_saved:
                if isinstance(f, dict):
                    first_tour = str(f.get("tour_nr") or f.get("TourNr") or f.get("tournr") or "").strip()
                else:
                    first_tour = str(f or "").strip()
                if first_tour:
                    break
        if not first_tour:
            first_tour = str(data.get("folder_number") or "").strip()
        if not first_tour:
            first_tour = str(tournrs[0] or "").strip()

        # 1) transporteur déterminé par le premier dossier ?
        try:
            kundennr = str(self.tour_repo.get_ffnr_for_tour(first_tour) or "").strip()
        except Exception as e:
            self._set_left_row_visual(row, "error", f"Erreur SQL transporteur via dossier {first_tour}: {e}")
            return

        if not kundennr:
            self._set_left_row_visual(row, "error", f"Aucun KundenNr transporteur sur le premier dossier {first_tour} (xxatour.FFNR vide).")
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

        # 3) tous les dossiers doivent porter le même FFNR que le premier.
        try:
            matching = self.tour_repo.get_tournrs_matching_ffnr(tournrs, kundennr)
            invalid = [t for t in tournrs if t not in matching]
        except Exception as e:
            self._set_left_row_visual(row, "error", f"Erreur SQL cohérence transporteur/dossiers: {e}")
            return

        if invalid:
            more = "" if len(invalid) <= 6 else f" (+{len(invalid)-6})"
            self._set_left_row_visual(
                row,
                "error",
                f"Dossier(s) avec un autre transporteur que {kundennr}: {', '.join(invalid[:6])}{more}"
            )
            return

        self._set_left_row_visual(row, "ok", f"OK : transporteur {kundennr} depuis le premier dossier + dossiers présents en base.")

    def refresh_left_table_processing_states(self):
        """Met à jour les couleurs du volet gauche avec des requêtes groupées.

        L'ancienne version appelait refresh_left_row_processing_state(row), qui
        pouvait relire un JSON puis faire 2 à 3 requêtes SQL par ligne. Sur un
        client avec des milliers de factures, c'était très coûteux. Ici on
        réutilise les numéros de dossier stockés dans la ligne (issus de
        XXA_OCR_SEARCH_INDEX quand possible) et on récupère tous les FFNR en une
        seule requête batch. Les lignes anciennes sans données indexées gardent
        l'ancien fallback, donc aucune fonctionnalité n'est retirée.
        """
        if not hasattr(self, "pdf_table") or self.pdf_table is None:
            return

        table = self.pdf_table
        search_active = bool(str(getattr(self, "_loaded_left_search_query", "") or "").strip())
        row_payloads: list[tuple[int, list[str], str]] = []
        all_tours: set[str] = set()
        fallback_rows: list[int] = []

        for row in range(table.rowCount()):
            it0 = table.item(row, 0)
            if not it0:
                fallback_rows.append(row)
                continue

            tours = []
            try:
                tours = [str(t).strip() for t in (it0.data(Qt.UserRole + 9) or []) if str(t).strip()]
            except Exception:
                tours = []

            # En mode recherche, on ne fait plus le fallback JSON : les dossiers
            # doivent venir exclusivement de XXA_OCR_SEARCH_INDEX.
            # Hors recherche, on conserve l'ancien contrôle unitaire pour l'affichage
            # classique des lignes non encore indexées.
            if not tours:
                if search_active:
                    self._set_left_row_visual(row, "error", "Aucun dossier (TourNr) dans XXA_OCR_SEARCH_INDEX.")
                else:
                    fallback_rows.append(row)
                continue

            kundennr = ""
            try:
                kundennr = str(it0.data(Qt.UserRole + 10) or "").strip()
            except Exception:
                kundennr = ""

            row_payloads.append((row, tours, kundennr))
            all_tours.update(tours)

        try:
            ffnr_by_tour = self.tour_repo.get_ffnr_map_for_tournrs(sorted(all_tours)) if all_tours else {}
        except Exception:
            ffnr_by_tour = None

        if ffnr_by_tour is None:
            # Erreur SQL globale : on retombe sur l'ancien comportement, qui
            # affichera l'erreur précise par ligne.
            for row in range(table.rowCount()):
                self.refresh_left_row_processing_state(row)
            return

        for row, tours, kundennr in row_payloads:
            if not tours:
                self._set_left_row_visual(row, "error", "Aucun dossier (TourNr) dans l'index.")
                continue

            first_tour = tours[0]
            if not kundennr:
                kundennr = str(ffnr_by_tour.get(first_tour) or "").strip()

            if not kundennr:
                self._set_left_row_visual(row, "error", f"Aucun KundenNr transporteur sur le premier dossier {first_tour} (xxatour.FFNR vide).")
                continue

            missing = [t for t in tours if t not in ffnr_by_tour]
            if missing:
                more = "" if len(missing) <= 6 else f" (+{len(missing)-6})"
                self._set_left_row_visual(row, "error", f"Dossier(s) manquant(s) en xxatour: {', '.join(missing[:6])}{more}")
                continue

            invalid = [t for t in tours if str(ffnr_by_tour.get(t) or "").strip() != kundennr]
            if invalid:
                more = "" if len(invalid) <= 6 else f" (+{len(invalid)-6})"
                self._set_left_row_visual(
                    row,
                    "error",
                    f"Dossier(s) avec un autre transporteur que {kundennr}: {', '.join(invalid[:6])}{more}",
                )
                continue

            self._set_left_row_visual(row, "ok", f"OK : transporteur {kundennr} depuis le premier dossier + dossiers présents en base.")

        if not search_active:
            for row in fallback_rows:
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

    def _find_left_row_for_pdf(self, pdf_path: str) -> int:
        pdf_path = str(pdf_path or "").strip()
        if not pdf_path or not hasattr(self, "pdf_table") or self.pdf_table is None:
            return -1

        for row in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(row, 0)
            if it0 and str(it0.data(Qt.UserRole) or "").strip() == pdf_path:
                return row
        return -1

    def _resolve_entry_id_for_pdf(self, pdf_path: str) -> str:
        pdf_path = str(pdf_path or "").strip()
        if not pdf_path:
            return ""

        row = self._find_left_row_for_pdf(pdf_path)
        if row >= 0:
            it0 = self.pdf_table.item(row, 0)
            if it0:
                entry_id = str(it0.data(Qt.UserRole + 4) or "").strip()
                if entry_id:
                    return entry_id

        try:
            return str(self.logmail_repo.get_entry_id_for_file(os.path.basename(pdf_path)) or "").strip()
        except Exception:
            return ""

    def _load_saved_json_for_pdf_action(self, pdf_path: str) -> tuple[str, dict]:
        json_path = self._get_saved_json_path(pdf_path)
        existing: dict = {}
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing = json.load(f) or {}
            except Exception:
                existing = {}
        return json_path, existing

    def _write_saved_json_for_pdf_action(self, json_path: str, data: dict) -> None:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _refresh_after_pdf_state_change(self, pdf_path: str, *, remove_row: bool = False) -> None:
        current_folder = str(getattr(self, "current_folder_path", "") or "").strip()
        if current_folder and os.path.isdir(current_folder):
            self.load_folder(current_folder)
            return

        entry_id = ""
        if remove_row:
            try:
                entry_id = self._resolve_entry_id_for_pdf(pdf_path)
            except Exception:
                entry_id = ""

        if remove_row and entry_id and hasattr(self, "pdf_table") and self.pdf_table is not None:
            for row in range(self.pdf_table.rowCount() - 1, -1, -1):
                it0 = self.pdf_table.item(row, 0)
                row_entry_id = str(it0.data(Qt.UserRole + 4) or "").strip() if it0 else ""
                if row_entry_id == entry_id:
                    self.pdf_table.removeRow(row)
        else:
            row = self._find_left_row_for_pdf(pdf_path)
            if row >= 0:
                if remove_row:
                    self.pdf_table.removeRow(row)
                else:
                    self.refresh_left_row_processing_state(row)

        self.apply_left_table_search_filter()

    def move_pdf_to_errors(self, pdf_path: str, filename: str = ""):
        if not pdf_path:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Déplacer vers les erreurs")
        msg.setText(
            "Déplacer ce fichier vers les erreurs ?\n\n"
            f"{filename or os.path.basename(strip_entry_prefix(os.path.basename(pdf_path)))}\n\n"
            "→ Ajoute le tag 'supprime' au JSON et le fichier apparaîtra dans le filtre 'Erreurs'."
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        try:
            if self.current_pdf_path == pdf_path:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        json_path, existing = self._load_saved_json_for_pdf_action(pdf_path)

        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []

        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.discard("supprime_definitif")
        tags_set.add("supprime")
        existing["tags"] = sorted(tags_set)
        existing["deleted_at"] = datetime.now().isoformat(timespec="seconds")
        existing.pop("deleted_permanently_at", None)

        self._write_saved_json_for_pdf_action(json_path, existing)

        try:
            self.logmail_repo.set_doc_type_for_file(os.path.basename(pdf_path), None)
            entry_id = self._resolve_entry_id_for_pdf(pdf_path)
            if entry_id:
                self.logmail_repo.set_processing_status_for_entry(entry_id, "error")
        except Exception as e:
            QMessageBox.warning(
                self,
                "Déplacer vers les erreurs",
                f"Le tag 'supprime' a été enregistré, mais la mise à jour SQL a échoué :\n{e}"
            )

        self._refresh_after_pdf_state_change(pdf_path)
        self.statusBar().showMessage("Fichier déplacé vers les erreurs.", 2500)

    def mark_pdf_as_deleted(self, pdf_path: str, filename: str = ""):
        self.move_pdf_to_errors(pdf_path, filename)

    def _collect_permanent_delete_documents_for_entry(self, pdf_path: str, entry_id: str = "") -> list[tuple[str, str]]:
        """Retourne les documents à supprimer pour le même entry_id que pdf_path.

        Chaque élément est un tuple (nom_pdf, chemin_probable). Le chemin peut
        pointer vers un fichier qui n'existe plus ; il sert aussi à retrouver le
        JSON de sauvegarde associé au document.
        """
        pdf_path = str(pdf_path or "").strip()
        entry_id = str(entry_id or "").strip()
        base_dir = os.path.dirname(pdf_path) if pdf_path else ""

        documents: dict[str, str] = {}

        def add_document(name: str = "", path: str = "") -> None:
            path = str(path or "").strip()
            name = os.path.basename(str(name or "").strip())
            if not name and path:
                name = os.path.basename(path)
            if not name:
                return
            if not path and base_dir:
                path = os.path.join(base_dir, name)
            documents.setdefault(name, path)

        # 1) Groupe déjà connu par la ligne de gauche : c'est la source la plus
        # fidèle à ce que l'utilisateur voit à l'écran.
        try:
            row = self._find_left_row_for_pdf(pdf_path)
            if row >= 0:
                it0 = self.pdf_table.item(row, 0)
                if it0:
                    group_paths = it0.data(Qt.UserRole + 5) or []
                    if isinstance(group_paths, (list, tuple, set)):
                        for group_path in group_paths:
                            add_document(path=str(group_path or ""))
        except Exception:
            pass

        # 2) Tous les fichiers SQL du même entry_id. Cela couvre les documents du
        # groupe qui ne seraient pas dans la ligne représentative affichée.
        if entry_id:
            try:
                rows = self.logmail_repo.get_files_for_entry(entry_id) or []
            except Exception:
                rows = []

            for r in rows:
                if isinstance(r, dict):
                    name = r.get("nom_pdf") or r.get("Nom_PDF") or r.get("filename") or ""
                else:
                    name = str(r or "")
                name = str(name or "").strip()
                if not name:
                    continue
                add_document(name=name, path=os.path.join(base_dir, name) if base_dir else name)

        # 3) Fallback obligatoire : au minimum le document sélectionné.
        add_document(path=pdf_path)

        return [(name, path) for name, path in documents.items()]

    def _mark_saved_json_as_permanently_deleted(self, pdf_path: str, deleted_at: str) -> None:
        json_path, existing = self._load_saved_json_for_pdf_action(pdf_path)

        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []

        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.discard("supprime")
        tags_set.add("supprime_definitif")
        existing["tags"] = sorted(tags_set)
        existing["deleted_permanently_at"] = deleted_at

        self._write_saved_json_for_pdf_action(json_path, existing)

    def mark_pdf_as_permanently_deleted(self, pdf_path: str, filename: str = ""):
        if not pdf_path:
            return

        entry_id = self._resolve_entry_id_for_pdf(pdf_path)
        documents = self._collect_permanent_delete_documents_for_entry(pdf_path, entry_id)
        if not documents:
            documents = [(os.path.basename(pdf_path), pdf_path)]

        display_names = [format_left_table_filename(name) for name, _path in documents if str(name or "").strip()]
        preview_names = display_names[:10]
        preview = "\n".join(f"• {name}" for name in preview_names)
        if len(display_names) > 10:
            preview += f"\n• … +{len(display_names) - 10} autre(s) document(s)"

        if entry_id and len(documents) > 1:
            question = (
                f"Supprimer définitivement les {len(documents)} documents rattachés au même entry_id ?\n\n"
                f"entry_id : {entry_id}\n\n"
                f"{preview}\n\n"
                "→ Tous ces documents seront marqués comme supprimés définitivement "
                "et n'apparaîtront plus dans le filtre 'Erreurs'."
            )
        else:
            question = (
                "Supprimer définitivement ce fichier de l'application ?\n\n"
                f"{filename or os.path.basename(strip_entry_prefix(os.path.basename(pdf_path)))}\n\n"
                "→ Le fichier sera marqué comme supprimé définitivement et n'apparaîtra plus dans le filtre 'Erreurs'."
            )

        msg = QMessageBox(self)
        msg.setWindowTitle("Suppression définitive")
        msg.setText(question)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec() != QMessageBox.Yes:
            return

        try:
            current_path = os.path.abspath(str(getattr(self, "current_pdf_path", "") or ""))
            document_paths = {
                os.path.abspath(str(path or ""))
                for _name, path in documents
                if str(path or "").strip()
            }
            if current_path and current_path in document_paths:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        deleted_at = datetime.now().isoformat(timespec="seconds")
        json_errors: list[str] = []
        for name, path in documents:
            try:
                self._mark_saved_json_as_permanently_deleted(path or os.path.join(os.path.dirname(pdf_path), name), deleted_at)
            except Exception as e:
                json_errors.append(f"{name or os.path.basename(str(path or ''))} : {e}")

        try:
            if entry_id and hasattr(self.logmail_repo, "set_doc_type_for_entry"):
                self.logmail_repo.set_doc_type_for_entry(entry_id, "deleted")
            else:
                for name, path in documents:
                    self.logmail_repo.set_doc_type_for_file(name or os.path.basename(path), "deleted")
        except Exception as e:
            QMessageBox.warning(
                self,
                "Suppression définitive",
                f"Le tag local a été enregistré, mais le type SQL n'a pas pu être mis à jour :\n{e}"
            )

        if json_errors:
            QMessageBox.warning(
                self,
                "Suppression définitive",
                "Certains JSON locaux n'ont pas pu être mis à jour :\n" + "\n".join(json_errors[:10])
            )

        self._refresh_after_pdf_state_change(pdf_path, remove_row=True)
        if len(documents) > 1:
            self.statusBar().showMessage(
                f"{len(documents)} documents du même entry_id supprimés définitivement de l'application.",
                2500,
            )
        else:
            self.statusBar().showMessage("Fichier supprimé définitivement de l'application.", 2500)

    def _refresh_transporter_after_bank_autofill(self):
        # IBAN/BIC restent utiles pour contrôler la banque, mais ils ne doivent
        # plus déterminer le transporteur. Le transporteur reste celui du
        # premier dossier (xxatour.FFNR).
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        if iban and bic:
            self.check_bank_information()
        self.load_transporter_information(force_by_kundennr=False)

    def compact_folder_rows(self):
        # évite les appels re-entrants
        if getattr(self, "_compacting_folder_rows", False):
            return
        self._compacting_folder_rows = True

        # Le compactage reconstruit le tableau avec setRowCount(0). Qt remet
        # alors automatiquement l'ascenseur en haut. On mémorise la position
        # pour la restaurer après reconstruction, notamment quand l'utilisateur
        # édite une ligne assez basse dans une longue liste de dossiers.
        v_scroll = self.folder_table.verticalScrollBar()
        h_scroll = self.folder_table.horizontalScrollBar()
        old_v = v_scroll.value() if v_scroll else 0
        old_h = h_scroll.value() if h_scroll else 0
        old_updates = self.folder_table.updatesEnabled()

        try:
            kept = []
            for r in range(self.folder_table.rowCount()):
                dossier_le, amount_le, _ = self._get_row_widgets(r)
                dossier = (dossier_le.text() if dossier_le else "").strip()
                amount = (amount_le.text() if amount_le else "").strip()

                # on garde les lignes non vides
                if dossier or amount:
                    kept.append((dossier, amount))

            try:
                self._prepare_folder_status_caches([dossier for dossier, _amount in kept])
            except Exception:
                pass

            self.folder_table.setUpdatesEnabled(False)
            self._folder_bulk_loading = True
            try:
                # rebuild table (sans trous)
                self.folder_table.setRowCount(0)
                for dossier, amount in kept:
                    self._add_folder_row(dossier=dossier, amount=amount)

                # garde une ligne vide en bas
                self._ensure_empty_folder_row()
            finally:
                self._folder_bulk_loading = False

            # refresh totaux / statuts
            try:
                self._refresh_all_folder_row_statuses()
            except Exception:
                pass
            self.update_folder_totals()
            self._last_transporter_source_tour_nr = self._get_first_folder_number() if hasattr(self, "_get_first_folder_number") else None
            self.load_transporter_information(force_by_kundennr=False)
            self.update_transporter_vs_dossiers_status()

        finally:
            self.folder_table.setUpdatesEnabled(old_updates)
            try:
                if v_scroll:
                    v_scroll.setValue(min(old_v, v_scroll.maximum()))
                if h_scroll:
                    h_scroll.setValue(min(old_h, h_scroll.maximum()))
                QTimer.singleShot(0, lambda: v_scroll and v_scroll.setValue(min(old_v, v_scroll.maximum())))
            except Exception:
                pass
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

    def _get_validation_block_options(self) -> dict:
        """Retourne les motifs de blocage connus au moment de la validation."""
        block_options = getattr(self, "block_options", {}) or {}
        if isinstance(block_options, dict) and block_options:
            return block_options

        # Fallback : si l'état mémoire n'est pas encore rempli, on relit le JSON
        # courant. Cela évite de valider à tort après un rechargement partiel.
        try:
            pdf_path = self._get_validation_document_path() or str(getattr(self, "current_pdf_path", "") or "").strip()
            if pdf_path and hasattr(self, "_read_saved_invoice_json"):
                data = self._read_saved_invoice_json(pdf_path) or {}
                loaded = data.get("block_options", {}) or {}
                if isinstance(loaded, dict):
                    return loaded
        except Exception:
            pass

        return {}

    def _has_active_iban_block_reason(self) -> bool:
        """Vrai si un document du groupe a un blocage actif avec motif IBAN."""
        block_options = self._get_validation_block_options()
        if not isinstance(block_options, dict):
            return False

        for info in block_options.values():
            if not isinstance(info, dict) or not bool(info.get("blocked", False)):
                continue

            reason = str(info.get("reason") or "").strip()
            comment = str(info.get("comment") or "").strip()

            # Le dialogue stocke normalement reason="IBAN". On garde aussi le
            # fallback sur comment pour compatibilité avec d'anciens JSON.
            if reason.upper() == "IBAN":
                return True
            if re.search(r"\bIBAN\b", comment, flags=re.IGNORECASE):
                return True

        return False

    def _block_validate_if_iban_not_matching_transporter(self) -> bool:
        """Bloque la validation si l'IBAN OCR ne correspond pas à XXAKunBank.

        Exception métier : la validation reste possible si la facture est
        volontairement bloquée avec le motif IBAN. Dans ce cas les tournées
        partent en état bloqué, ce qui permet de traiter le litige sans perdre
        la facture.
        """
        iban = self.iban_input.text().strip() if hasattr(self, "iban_input") else ""

        # Le contrôle demandé porte sur un IBAN OCRisé valide. Les champs vides
        # ou invalides sont déjà visibles dans l'UI et ne doivent pas provoquer
        # un faux écart transporteur/banque.
        if not iban or not validate_iban(iban):
            try:
                if hasattr(self, "check_bank_information"):
                    self.check_bank_information()
            except Exception:
                pass
            return True

        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()

        if not kundennr:
            try:
                kundennr, _source_tour_nr = self._resolve_supplier_kundennr_from_folders()
                kundennr = str(kundennr or "").strip()
            except Exception:
                kundennr = ""

        # Le contrôle transporteur juste avant celui-ci affichera déjà un message
        # clair si aucun KundenNr n'est déterminable.
        if not kundennr:
            return True

        try:
            banks = self.bank_repo.get_all_bank_infos_by_kundennr(kundennr) or []
        except Exception as e:
            QMessageBox.warning(
                self,
                "Validation impossible",
                "Erreur lors du contrôle IBAN du transporteur dans XXAKunBank.\n\n"
                f"Transporteur : {kundennr}\n"
                f"Détail : {e}"
            )
            return False

        db_pairs = [
            (
                str((b or {}).get("iban") or "").strip(),
                str((b or {}).get("bic") or "").strip(),
            )
            for b in banks
            if str((b or {}).get("iban") or "").strip() or str((b or {}).get("bic") or "").strip()
        ]

        # On met à jour l'état UI pour rester cohérent avec le fond rouge déjà
        # utilisé à l'affichage.
        self.current_db_bank_pairs = db_pairs
        if db_pairs:
            self.current_db_iban = db_pairs[0][0]
            self.current_db_bic = db_pairs[0][1]
        else:
            self.current_db_iban = ""
            self.current_db_bic = ""

        try:
            if hasattr(self, "check_bank_information"):
                self.check_bank_information()
            if hasattr(self, "_refresh_transporter_bank_transfer_button"):
                self._refresh_transporter_bank_transfer_button()
        except Exception:
            pass

        iban_matches = False
        try:
            iban_matches = bool(self._transporter_has_iban(iban))
        except Exception:
            iban_norm = str(iban or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()
            iban_matches = any(
                iban_norm == str(db_iban or "").replace(" ", "").replace("\u00A0", "").replace("-", "").upper().strip()
                for db_iban, _db_bic in db_pairs
            )

        if iban_matches:
            return True

        if self._has_active_iban_block_reason():
            return True

        db_values = "aucun IBAN en base"
        try:
            if hasattr(self, "_format_current_transporter_bank_values"):
                db_values = self._format_current_transporter_bank_values()
        except Exception:
            pass

        QMessageBox.warning(
            self,
            "Validation impossible",
            "L'IBAN OCR ne correspond pas à l'IBAN enregistré sur la fiche banque du transporteur.\n\n"
            f"Transporteur : {kundennr}\n"
            f"IBAN OCR : {iban}\n"
            f"IBAN/BIC XXAKunBank : {db_values}\n\n"
            "Validation bloquée. Pour valider malgré cet écart, ajoute un motif de blocage IBAN."
        )
        return False

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

            resp = QMessageBox.question(
                self,
                "Validation impossible",
                "Le montant HT OCR ne correspond pas au montant HT de la tournée Winsped.\n\n"
                f"Montant HT OCR : {ocr_bad}\n"
                f"Montant HT Winsped : {ws_txt or '(inconnu)'}\n"
                f"pour la tournée {tour_bad}\n\n"
                "Voulez vous transferer cette facture vers les Ecarts ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if resp == QMessageBox.Yes:
                self._mark_current_entry_as_ecart(
                    reason="ht_amount_mismatch",
                    extra={
                        "ecart_tour_nr": str(tour_bad or "").strip(),
                        "ecart_amount_ht_ocr": str(ocr_bad or "").strip(),
                        "ecart_amount_ht_winsped": ws_txt or "",
                    },
                )

            return False

        return True
    
    def _copy_validated_pdf_to_dms(self, pdf_path: str | None = None):
        pdf_path = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()
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
        # Important : les liens cmr_page_links peuvent être portés par un document
        # secondaire du groupe (ex : PDF CMR), et pas forcément par le PDF facture
        # utilisé comme document principal de validation. On parcourt donc tous les
        # documents du groupe au lieu de ne découper que pdf_path.
        try:
            self._cmr_splits = {}
            entry_id = str(getattr(self, "selected_invoice_entry_id", "") or "").strip()

            split_candidates = []
            for p in [pdf_path, *((self.entry_pdf_paths or []))]:
                p = str(p or "").strip()
                if not p or not os.path.isfile(p):
                    continue
                abs_p = os.path.abspath(p)
                if abs_p in {os.path.abspath(x) for x in split_candidates}:
                    continue
                split_candidates.append(p)

            for p in split_candidates:
                cmr_splits = self._split_cmr_pages_for_validation(
                    p,
                    target_dir,
                    entry_id=entry_id,
                )
                if not cmr_splits:
                    continue

                # Stockage avec la clé originale et la clé absolue pour éviter les
                # ratés si le chemin est relu sous une forme légèrement différente.
                self._cmr_splits[p] = cmr_splits
                self._cmr_splits[os.path.abspath(p)] = cmr_splits

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

        invoice_path = str(getattr(self, "_last_validation_invoice_pdf_path", "") or self._get_validation_document_path() or getattr(self, "current_pdf_path", "") or "").strip()
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
                    file_splits = {}
                    if isinstance(cmr_splits, dict):
                        file_splits = cmr_splits.get(p, {}) or cmr_splits.get(os.path.abspath(p), {}) or {}
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


    def _add_tag_to_current_json(self, tag: str, extra: dict | None = None, pdf_path: str | None = None) -> None:
        """Ajoute un tag dans le JSON du document courant (sans écraser le reste)."""
        target_pdf_path = str(pdf_path or self._get_validation_document_path() or getattr(self, "current_pdf_path", "") or "").strip()
        if not target_pdf_path:
            return
        try:
            json_path = self._get_saved_json_path(target_pdf_path)
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
            self.save_current_data(status="error", show_message=False, pdf_path=self._get_validation_document_path())
        except Exception:
            pass

        self._add_tag_to_current_json("duplicate_invoice", extra={"error_reason": reason} if reason else None, pdf_path=self._get_validation_document_path())

        # refresh + se placer sur l'onglet erreurs
        try:
            self.set_left_filter("errors")
        except Exception:
            try:
                self.load_folder(self.current_folder_path)
            except Exception:
                pass


    def _mark_current_entry_as_ecart(self, reason: str = "ht_amount_mismatch", extra: dict | None = None) -> None:
        """Met l'entrée dans le listing Ecarts + trace dans le JSON."""
        try:
            self.save_current_data(status="ecart", show_message=False, pdf_path=self._get_validation_document_path())
        except Exception:
            pass

        payload = {"ecart_reason": reason} if reason else {}
        if extra:
            payload.update(extra)

        self._add_tag_to_current_json("ecart", extra=payload or None, pdf_path=self._get_validation_document_path())

        try:
            self.set_left_filter("ecart")
        except Exception:
            try:
                self.load_folder(self.current_folder_path)
            except Exception:
                pass




    def _mark_current_entry_as_eccarts(self, reason: str = "ht_amount_mismatch", extra: dict | None = None) -> None:
        """Compat ancien nom : redirige vers le statut ecart."""
        self._mark_current_entry_as_ecart(reason=reason, extra=extra)

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
        1) on préfère le premier taux saisi/extrait dans la zone TVA
        2) sinon fallback sur le premier taux TVA théorique BDD des tournées

        LISINVOICE_EDTRANS ne contient qu'une seule colonne Taux : en cas de
        plusieurs taux détectés, on conserve volontairement le premier taux
        exploitable au lieu de bloquer l'alimentation.
        """
        for row in (self.get_vat_rows() or []):
            rate = self._to_sql_decimal_2(row.get("rate"))
            base = self._to_sql_decimal_2(row.get("base"))
            vat = self._to_sql_decimal_2(row.get("vat"))

            if rate is None and base is None and vat is None:
                continue

            if rate is not None:
                return rate

        for tour_nr in tour_nrs:
            try:
                val = self.tour_repo.get_theoretical_vat_percent_by_tournr(tour_nr)
            except Exception:
                val = None

            dec = self._to_sql_decimal_2(val)
            if dec is not None:
                return dec

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

