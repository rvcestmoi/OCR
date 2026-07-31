from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled


class MainWindowTransportTablesMixin:

    def _is_tour_already_in_lisinvoice(self, tour_nr: str) -> tuple[bool, str]:
        """Retourne si le dossier est déjà présent dans LISINVOICE_EDTRANS.

        Le résultat est mis en cache pour éviter de relancer la requête SQL à
        chaque repaint / recalcul de la ligne. La validation garde son contrôle
        SQL direct, donc ce cache ne remplace pas la sécurité métier finale.
        """
        tour_nr = str(tour_nr or "").strip()
        if not tour_nr:
            return False, ""

        cache = getattr(self, "_lisinvoice_tour_exists_cache", None)
        if cache is None:
            cache = {}
            self._lisinvoice_tour_exists_cache = cache

        if tour_nr in cache:
            return bool(cache[tour_nr]), ""

        try:
            exists = bool(self.lisinvoice_repo.tour_exists(tour_nr))
            cache[tour_nr] = exists
            return exists, ""
        except Exception as e:
            return False, str(e)

    def _is_tour_already_in_printed_shipments(self, tour_nr: str, kunden_nr: str | None = None) -> tuple[bool, str]:
        """Retourne si le dossier existe déjà dans XXAV_InvC_PrintedShipments.

        Cette vérification dépend du transporteur courant :
        TourNr = numéro de dossier, FANr = KundenNr transporteur, AufDK = 'K'.
        """
        tour_nr = str(tour_nr or "").strip()
        kunden_nr = str(kunden_nr or getattr(self, "selected_kundennr", "") or "").strip()
        if not tour_nr or not kunden_nr:
            return False, ""

        cache = getattr(self, "_printed_shipment_tour_exists_cache", None)
        if cache is None:
            cache = {}
            self._printed_shipment_tour_exists_cache = cache

        key = (tour_nr, kunden_nr)
        if key in cache:
            return bool(cache[key]), ""

        try:
            exists = bool(self.lisinvoice_repo.printed_shipment_exists(tour_nr, kunden_nr))
            cache[key] = exists
            return exists, ""
        except Exception as e:
            return False, str(e)

    def _apply_tour_invoicing_style(self, dossier_le: QLineEdit, tour_nr: str) -> bool:
        """Affiche le n° de dossier en rouge s'il est déjà dans LISINVOICE_EDTRANS,
        ou en orange s'il existe déjà dans XXAV_InvC_PrintedShipments pour le
        transporteur courant.
        """
        already_invoiced, err = self._is_tour_already_in_lisinvoice(tour_nr)

        if err:
            dossier_le.setToolTip(f"Erreur contrôle facturation LISINVOICE_EDTRANS : {err}")
            return False

        if already_invoiced:
            dossier_le.setStyleSheet("color:#dc3545;")
            dossier_le.setToolTip("Le dossier est déjà en facturation (LISINVOICE_EDTRANS).")
            return True

        kunden_nr = str(getattr(self, "selected_kundennr", "") or "").strip()
        already_printed, printed_err = self._is_tour_already_in_printed_shipments(tour_nr, kunden_nr)
        if printed_err:
            dossier_le.setToolTip(f"Erreur contrôle factures imprimées XXAV_InvC_PrintedShipments : {printed_err}")
            return False

        if already_printed:
            dossier_le.setStyleSheet("color:#fd7e14;")
            dossier_le.setToolTip(
                "Le dossier existe déjà dans XXAV_InvC_PrintedShipments "
                f"pour le transporteur {kunden_nr} (FANr={kunden_nr}, AufDK='K')."
            )
            return False

        dossier_le.setToolTip("")
        return False

    def _is_valid_transporter_aux_account(self, value: str) -> bool:
        return bool(str(value or "").strip().startswith("0"))

    def _set_transporter_aux_db_update_allowed(self, allowed: bool) -> None:
        self._transporter_aux_db_update_allowed = bool(allowed)
        self._update_transporter_aux_db_button_state()

    def _update_transporter_aux_db_button_state(self, *args) -> None:
        btn = getattr(self, "btn_transporter_aux_save", None)
        if btn is None:
            return

        allowed = bool(getattr(self, "_transporter_aux_db_update_allowed", False))
        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()
        value = str(getattr(self, "transporter_aux_input", None).text() if getattr(self, "transporter_aux_input", None) else "" or "").strip()

        btn.setEnabled(bool(allowed and kundennr))
        if not kundennr:
            btn.setToolTip("Impossible de mettre à jour : transporteur non déterminé.")
        elif not allowed:
            btn.setToolTip("Mise à jour BDD non nécessaire : le compte auxiliaire BDD est déjà renseigné et commence par 0.")
        elif not value:
            btn.setToolTip("Saisis un compte auxiliaire commençant par 0 avant la mise à jour BDD.")
        elif not self._is_valid_transporter_aux_account(value):
            btn.setToolTip("Le compte saisi ne commence pas par 0 : il sera refusé à la validation.")
        else:
            btn.setToolTip("Mettre à jour XXAKun.KtoKreA avec ce compte et XXAKun.KtoKre avec le KundenNr.")

    def _set_transporter_aux_locked(self, locked: bool, value: str = "", allow_db_update: bool | None = None):
        self._transporter_aux_locked = bool(locked)
        self.transporter_aux_input.blockSignals(True)
        self.transporter_aux_input.setText(str(value or "").strip())
        self.transporter_aux_input.setReadOnly(bool(locked))
        self.transporter_aux_input.setFocusPolicy(Qt.NoFocus if locked else Qt.StrongFocus)
        self.transporter_aux_input.setClearButtonEnabled(not locked)
        self.transporter_aux_input.blockSignals(False)
        if allow_db_update is not None:
            self._transporter_aux_db_update_allowed = bool(allow_db_update)
        elif bool(locked):
            self._transporter_aux_db_update_allowed = False
        self._refresh_transporter_aux_style()
        self._update_transporter_aux_db_button_state()

    def _refresh_transporter_aux_style(self):
        locked = bool(getattr(self, "_transporter_aux_locked", True))
        match_ok = getattr(self, "_transporter_aux_match_ok", None)

        bg = "#f3f3f3" if locked else "#ffffff"
        extra = ""
        if match_ok is True:
            extra = "border: 2px solid #28a745;"
        elif match_ok is False:
            extra = "border: 2px solid #dc3545;"

        self.transporter_aux_input.setStyleSheet(f"background-color: {bg}; {extra}")

    def _resolve_kundennr_for_aux_refresh(self) -> tuple[str, str]:
        """Retourne le KundenNr transporteur courant pour actualiser le compte auxiliaire."""
        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()
        if kundennr:
            return kundennr, ""

        try:
            kundennr, source_tour_nr, err = self._resolve_transporter_from_first_folder()
            if err:
                return "", err
            if not kundennr:
                if source_tour_nr:
                    return "", f"Aucun KundenNr transporteur trouvé sur le dossier {source_tour_nr}."
                return "", "Aucun transporteur déterminé : renseigne d'abord un dossier."
            self.selected_kundennr = kundennr
            self.transporter_selected_mode = True
            return kundennr, ""
        except Exception as e:
            return "", str(e)

    def on_refresh_transporter_aux_clicked(self):
        """Relit XXAKun.KtoKreA en BDD pour le transporteur courant."""
        kundennr, err = self._resolve_kundennr_for_aux_refresh()
        if err or not kundennr:
            QMessageBox.information(
                self,
                "Actualisation compte auxiliaire",
                err or "Impossible de déterminer le transporteur courant."
            )
            return

        try:
            aux_row = self.transporter_repo.get_ktoKreA_by_kundennr(kundennr)
            db_aux = str((aux_row or {}).get("KtoKreA") or "").strip()

            # La BDD est la source de vérité pour cette actualisation manuelle.
            self._pending_saved_transporter_aux = ""
            self._pending_saved_transporter_aux_kundennr = ""

            if db_aux and self._is_valid_transporter_aux_account(db_aux):
                self._set_transporter_aux_locked(True, db_aux, allow_db_update=False)
                msg = f"Compte auxiliaire actualisé depuis la BDD pour le transporteur {kundennr} : {db_aux}."
            else:
                # Si la BDD est vide ou invalide, on laisse le champ modifiable
                # et le bouton 💾 permet de corriger XXAKun.
                self._set_transporter_aux_locked(False, db_aux, allow_db_update=True)
                if db_aux:
                    msg = f"Compte auxiliaire BDD invalide pour le transporteur {kundennr} : {db_aux}. Corrige puis clique sur 💾."
                else:
                    msg = f"Aucun compte auxiliaire renseigné en BDD pour le transporteur {kundennr}. Saisis le compte puis clique sur 💾."

            if hasattr(self, "statusBar"):
                self.statusBar().showMessage(msg, 5000)

        except Exception as e:
            QMessageBox.warning(
                self,
                "Actualisation compte auxiliaire",
                "Erreur pendant la relecture du compte auxiliaire en base :\n" + str(e)
            )

    def on_save_transporter_aux_to_db_clicked(self):
        """Met à jour XXAKun.KtoKreA/KtoKre pour le transporteur courant."""
        kundennr, err = self._resolve_kundennr_for_aux_refresh()
        if err or not kundennr:
            QMessageBox.information(
                self,
                "Mise à jour compte auxiliaire",
                err or "Impossible de déterminer le transporteur courant."
            )
            return

        aux_value = str(self.transporter_aux_input.text() or "").strip()
        if not aux_value:
            QMessageBox.warning(
                self,
                "Mise à jour compte auxiliaire",
                "Le compte auxiliaire est vide. Saisis un compte commençant par 0 avant de mettre à jour la base."
            )
            self.transporter_aux_input.setFocus()
            return

        if not self._is_valid_transporter_aux_account(aux_value):
            QMessageBox.warning(
                self,
                "Mise à jour compte auxiliaire",
                "Le compte auxiliaire doit commencer par 0 avant d'être enregistré en base."
            )
            self.transporter_aux_input.setFocus()
            return

        resp = QMessageBox.question(
            self,
            "Mise à jour compte auxiliaire",
            f"Mettre à jour le transporteur {kundennr} ?\n\n"
            f"KtoKreA = {aux_value}\n"
            f"KtoKre  = {kundennr}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        try:
            rowcount = self.transporter_repo.update_ktoKreA(kundennr, aux_value)
            if rowcount == 0:
                QMessageBox.warning(
                    self,
                    "Mise à jour compte auxiliaire",
                    f"Aucune ligne XXAKun mise à jour pour le transporteur {kundennr}."
                )
                return

            self._set_transporter_aux_locked(True, aux_value, allow_db_update=False)
            self._pending_saved_transporter_aux = ""
            self._pending_saved_transporter_aux_kundennr = ""
            if hasattr(self, "statusBar"):
                self.statusBar().showMessage(
                    f"Compte auxiliaire mis à jour en BDD pour le transporteur {kundennr}.",
                    5000
                )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Mise à jour compte auxiliaire",
                "Erreur pendant la mise à jour XXAKun :\n" + str(e)
            )

    def _normalize_iban_for_compare(self, value: str) -> str:
        return (
            str(value or "")
            .replace(" ", "")
            .replace("\u00A0", "")
            .replace("-", "")
            .upper()
            .strip()
        )

    def _normalize_bic_for_compare(self, value: str) -> str:
        return (
            str(value or "")
            .replace(" ", "")
            .replace("\u00A0", "")
            .replace("-", "")
            .upper()
            .strip()
        )

    def _same_bank_pair(self, iban_a: str, bic_a: str, iban_b: str, bic_b: str) -> bool:
        """Compare IBAN/BIC en tenant compte des BIC 8 ou 11 caractères."""
        ia = self._normalize_iban_for_compare(iban_a)
        ib = self._normalize_iban_for_compare(iban_b)
        if not ia or ia != ib:
            return False

        ba = self._normalize_bic_for_compare(bic_a)
        bb = self._normalize_bic_for_compare(bic_b)
        if not ba or not bb:
            return ba == bb

        return ba == bb or ba[:8] == bb[:8]

    def _transporter_has_bank_pair(self, iban: str, bic: str) -> bool:
        for db_iban, db_bic in getattr(self, "current_db_bank_pairs", []) or []:
            if self._same_bank_pair(iban, bic, db_iban, db_bic):
                return True
        return False

    def _transporter_has_iban(self, iban: str) -> bool:
        iban_norm = self._normalize_iban_for_compare(iban)
        if not iban_norm:
            return False

        for db_iban, _db_bic in getattr(self, "current_db_bank_pairs", []) or []:
            if iban_norm == self._normalize_iban_for_compare(db_iban):
                return True
        return False

    def _transporter_has_bic_for_iban(self, iban: str, bic: str) -> bool:
        iban_norm = self._normalize_iban_for_compare(iban)
        bic_norm = self._normalize_bic_for_compare(bic)
        if not iban_norm or not bic_norm:
            return False

        for db_iban, db_bic in getattr(self, "current_db_bank_pairs", []) or []:
            if iban_norm != self._normalize_iban_for_compare(db_iban):
                continue

            db_bic_norm = self._normalize_bic_for_compare(db_bic)
            if not db_bic_norm:
                continue

            if bic_norm == db_bic_norm or bic_norm[:8] == db_bic_norm[:8]:
                return True

        return False

    def _format_current_transporter_bank_values(self) -> str:
        pairs = []
        seen = set()
        for db_iban, db_bic in getattr(self, "current_db_bank_pairs", []) or []:
            db_iban = str(db_iban or "").strip()
            db_bic = str(db_bic or "").strip()
            if not db_iban and not db_bic:
                continue
            key = (db_iban, db_bic)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(f"{db_iban or '(IBAN vide)'} | {db_bic or '(BIC vide)'}")

        return "; ".join(pairs) if pairs else "aucun IBAN/BIC en base"

    def _apply_bank_field_styles(self):
        """Style IBAN/BIC selon la banque OCR et la banque du transporteur courant.

        Ancien comportement conservé : vert si l'IBAN/BIC existe quelque part en
        base, jaune sinon. Nouveau comportement prioritaire : rouge si l'IBAN
        OCR ne correspond pas aux coordonnées XXAKunBank du transporteur imposé
        par le premier dossier.
        """
        if not hasattr(self, "iban_input") or not hasattr(self, "bic_input"):
            return

        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()

        green = "background-color: #e6ffe6;"
        yellow = "background-color: #fff3cd;"
        red = "background-color: #f8d7da; border: 2px solid #dc3545;"

        # On ne force pas de couleur sur les champs vides : highlight_missing_fields
        # continue à gérer les champs obligatoires manquants.
        iban_style = ""
        bic_style = ""
        iban_tooltip = ""
        bic_tooltip = ""
        transporter_mismatch_fields = set()

        if iban or bic:
            if self.bank_valid is True:
                iban_style = green if iban else ""
                bic_style = green if bic else ""
            elif self.bank_valid is False:
                iban_style = yellow if iban else ""
                bic_style = yellow if bic else ""

        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()
        db_pairs = list(getattr(self, "current_db_bank_pairs", []) or [])

        # Contrôle fort uniquement quand un transporteur est effectivement
        # déterminé depuis le dossier et que l'IBAN OCR est valide.
        if kundennr and iban and validate_iban(iban):
            db_values = self._format_current_transporter_bank_values()

            if not db_pairs:
                transporter_mismatch_fields.add("iban")
                iban_style = red
                iban_tooltip = (
                    f"L'IBAN OCR ne peut pas être retrouvé dans XXAKunBank pour "
                    f"le transporteur {kundennr}. Valeur BD : {db_values}."
                )
            elif not self._transporter_has_iban(iban):
                transporter_mismatch_fields.add("iban")
                iban_style = red
                iban_tooltip = (
                    f"IBAN OCR différent de l'IBAN XXAKunBank du transporteur "
                    f"{kundennr}. Valeur BD : {db_values}."
                )
            elif bic and validate_bic(bic) and not self._transporter_has_bic_for_iban(iban, bic):
                transporter_mismatch_fields.add("bic")
                # L'IBAN est bon, mais le BIC OCR ne correspond pas à la même
                # ligne banque du transporteur. On laisse l'IBAN vert/jaune et
                # on signale uniquement le BIC.
                bic_style = red
                bic_tooltip = (
                    f"BIC OCR différent du SWIFT XXAKunBank pour cet IBAN "
                    f"sur le transporteur {kundennr}. Valeur BD : {db_values}."
                )

        self._bank_transporter_mismatch_fields = transporter_mismatch_fields
        self._bank_transporter_mismatch = bool(transporter_mismatch_fields)
        self.iban_input.setStyleSheet(iban_style)
        self.bic_input.setStyleSheet(bic_style)
        self.iban_input.setToolTip(iban_tooltip)
        self.bic_input.setToolTip(bic_tooltip)

    def _refresh_transporter_bank_transfer_button(self):
        """Active le bouton ➡ uniquement si l'IBAN/BIC OCR peut corriger la fiche transporteur.

        Le transporteur reste verrouillé et vient toujours du premier dossier.
        Ce bouton ne change que les coordonnées bancaires XXAKunBank du KundenNr
        déjà déterminé par la tournée.
        """
        if not hasattr(self, "btn_transporter_action") or self.btn_transporter_action is None:
            return

        enabled = False
        tooltip = ""
        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()
        iban = self.iban_input.text().strip() if hasattr(self, "iban_input") else ""
        bic = self.bic_input.text().strip() if hasattr(self, "bic_input") else ""

        if not kundennr:
            tooltip = "Aucun transporteur déterminé par le dossier."
        elif not iban or not bic:
            tooltip = "IBAN/BIC OCR incomplets : transfert impossible."
        elif not validate_iban(iban):
            tooltip = "IBAN OCR invalide : transfert impossible."
        elif not validate_bic(bic):
            tooltip = "BIC OCR invalide : transfert impossible."
        elif self._transporter_has_bank_pair(iban, bic):
            tooltip = "Cet IBAN/BIC est déjà présent sur la fiche transporteur."
        else:
            enabled = True
            old_iban = str(getattr(self, "current_db_iban", "") or "").strip()
            old_bic = str(getattr(self, "current_db_bic", "") or "").strip()
            if old_iban or old_bic:
                tooltip = f"Transférer l'IBAN/BIC OCR vers le transporteur {kundennr}. Valeur BD actuelle : {old_iban} | {old_bic}"
            else:
                tooltip = f"Créer l'IBAN/BIC OCR sur le transporteur {kundennr}."

        self.btn_transporter_action.setEnabled(enabled)
        self.btn_transporter_action.setToolTip(tooltip)

    def on_prev_page(self):
        self.pdf_viewer.previous_page()
        self.update_page_indicator()

    def on_next_page(self):
        self.pdf_viewer.next_page()
        self.update_page_indicator()

    def on_zoom_in(self):
        self.pdf_viewer.zoom_in()
        self.update_view_indicator()

    def on_zoom_out(self):
        self.pdf_viewer.zoom_out()
        self.update_view_indicator()

    def on_fit_width(self):
        self.pdf_viewer.fit_to_width()
        self.update_view_indicator()

    def on_rotate_left(self):
        self.pdf_viewer.rotate_left()
        self.update_view_indicator()

    def on_rotate_right(self):
        self.pdf_viewer.rotate_right()
        self.update_view_indicator()

    def update_view_indicator(self):
        if not hasattr(self, "lbl_view_info"):
            return

        try:
            zoom = int(self.pdf_viewer.get_zoom_percent())
        except Exception:
            zoom = 100

        try:
            rotation = int(self.pdf_viewer.rotation_degrees())
        except Exception:
            rotation = 0

        self.lbl_view_info.setText(f"{zoom}% · {rotation}°")

    def update_page_indicator(self):
        total = self.pdf_viewer.page_count()
        if total == 0:
            self.lbl_page_info.setText("0 / 0")
            if hasattr(self, "btn_prev_page"):
                self.btn_prev_page.setEnabled(False)
            if hasattr(self, "btn_next_page"):
                self.btn_next_page.setEnabled(False)
            self.update_view_indicator()
            return
        current = self.pdf_viewer.current_page_index() + 1
        self.lbl_page_info.setText(f"Page {current} / {total}")
        self.btn_prev_page.setEnabled(current > 1)
        self.btn_next_page.setEnabled(current < total)
        self.update_view_indicator()

    def load_related_pdfs(self):
        self.related_pdf_table.setRowCount(0)
        if not self.current_pdf_path:
            return

        current_dir = os.path.dirname(self.current_pdf_path)
        nom_pdf = os.path.basename(self.current_pdf_path)

        try:
            entry_id = self.logmail_repo.get_entry_id_for_file(nom_pdf)
            if not entry_id:
                return

            rows = self.logmail_repo.get_files_for_entry(entry_id)
            for row_idx, row in enumerate(rows):
                self.related_pdf_table.insertRow(row_idx)
                pdf_name = row["nom_pdf"]
                full_path = os.path.join(current_dir, pdf_name)
                item = QTableWidgetItem(pdf_name)
                item.setData(Qt.UserRole, full_path)
                self.related_pdf_table.setItem(row_idx, 0, item)

        except Exception as e:
            QMessageBox.warning(self, "BDD", f"Erreur lors du chargement des pièces jointes liées :\n{e}")

    def on_related_pdf_selected(self, row, column):
        item = self.related_pdf_table.item(row, 0)
        if not item:
            return

        path = item.data(Qt.UserRole)
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "PDF", "Fichier introuvable.")
            return

        # on affiche ce PDF, sans changer la facture cible
        self.view_pdf_path = path

        # si ce PDF est dans le groupe, on met à jour l’index
        if path in self.entry_pdf_paths:
            self.current_doc_index = self.entry_pdf_paths.index(path)

        self.display_pdf()
        self.update_page_indicator()
        self.update_doc_indicator()

    def check_bank_information(self):
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        self.bank_valid = None

        if iban and bic:
            record = self.bank_repo.find_by_iban_bic(iban, bic)
            self.bank_valid = bool(record)

        self._apply_bank_field_styles()

    def _set_transporter_input_locked(self, value: str = ""):
        """Affiche le transporteur en lecture seule.

        Le transporteur n'est plus une donnée OCR / banque modifiable : il est
        imposé par le KundenNr (FFNR) du premier dossier de la facture.
        """
        if not hasattr(self, "transporter_input") or self.transporter_input is None:
            return

        self.transporter_input.blockSignals(True)
        self.transporter_input.setText(str(value or "").strip())
        self.transporter_input.setReadOnly(True)
        self.transporter_input.setFocusPolicy(Qt.ClickFocus)
        self.transporter_input.setClearButtonEnabled(False)
        try:
            self.transporter_input.setCompleter(None)
        except Exception:
            pass
        self.transporter_input.setStyleSheet("background-color: #f3f3f3;")
        self.transporter_input.blockSignals(False)

    def _get_first_folder_number(self) -> str:
        try:
            for row in self.get_folder_rows() or []:
                tour_nr = str((row or {}).get("tour_nr") or "").strip()
                if tour_nr:
                    return tour_nr
        except Exception:
            pass
        return ""

    def _resolve_transporter_from_first_folder(self) -> tuple[str, str, str]:
        """Retourne (kundennr, tour_nr_source, erreur) depuis le premier dossier.

        On ne parcourt plus les dossiers pour chercher un transporteur par IBAN/BIC.
        Le premier TourNr renseigné est la source métier unique du transporteur.
        """
        tour_nr = self._get_first_folder_number()
        if not tour_nr:
            return "", "", ""

        try:
            if not re.fullmatch(self.DOSSIER_PATTERN, tour_nr):
                return "", tour_nr, f"Numéro de dossier invalide : {tour_nr}"
        except Exception:
            pass

        cache = getattr(self, "_supplier_kundennr_by_tour_cache", None)
        if cache is None:
            cache = {}
            self._supplier_kundennr_by_tour_cache = cache

        if tour_nr in cache:
            kundennr = str(cache.get(tour_nr) or "").strip()
        else:
            try:
                kundennr = str(self.tour_repo.get_ffnr_for_tour(tour_nr) or "").strip()
            except Exception as e:
                return "", tour_nr, str(e)
            cache[tour_nr] = kundennr

        return kundennr, tour_nr, ""

    def _refresh_transporter_from_first_folder(self):
        self.load_transporter_information(force_by_kundennr=False)
        # Le contrôle orange XXAV_InvC_PrintedShipments dépend du KundenNr du
        # transporteur. Quand le premier dossier change, on doit donc recalculer
        # toutes les lignes après résolution du transporteur.
        try:
            self._refresh_all_folder_row_statuses()
        except Exception:
            pass

    def load_transporter_information(self, force_by_kundennr: bool = False):
        """Charge le transporteur depuis le premier dossier uniquement.

        Ancienne logique supprimée côté UI : on ne recherche plus le transporteur
        depuis IBAN/BIC et on ne le laisse plus modifiable manuellement. Le
        KundenNr est lu dans xxatour.FFNR à partir du premier TourNr saisi.
        """
        try:
            if hasattr(self, "transporter_info") and self.transporter_info is not None:
                self.transporter_info.clear()

            kundennr, source_tour_nr, err = self._resolve_transporter_from_first_folder()

            if err:
                self.selected_kundennr = None
                self.transporter_selected_mode = False
                self._set_transporter_input_locked("")
                self._set_transporter_aux_locked(True, "")
                self.current_db_iban = ""
                self.current_db_bic = ""
                self.current_db_bank_pairs = []
                self.check_bank_information()
                self._refresh_transporter_bank_transfer_button()
                self.transporter_info.setPlainText(f"❌ Transporteur non déterminé depuis le dossier {source_tour_nr or ''} :\n{err}")
                return

            if not source_tour_nr:
                self.selected_kundennr = None
                self.transporter_selected_mode = False
                self._set_transporter_input_locked("")
                self._set_transporter_aux_locked(True, "")
                self.current_db_iban = ""
                self.current_db_bic = ""
                self.current_db_bank_pairs = []
                self.check_bank_information()
                self._refresh_transporter_bank_transfer_button()
                self.transporter_info.setPlainText("ℹ️ Aucun dossier renseigné : transporteur non déterminé.")
                return

            if not kundennr:
                self.selected_kundennr = None
                self.transporter_selected_mode = False
                self._set_transporter_input_locked("")
                self._set_transporter_aux_locked(True, "")
                self.current_db_iban = ""
                self.current_db_bic = ""
                self.current_db_bank_pairs = []
                self.check_bank_information()
                self._refresh_transporter_bank_transfer_button()
                self.transporter_info.setPlainText(
                    f"❌ Aucun KundenNr transporteur trouvé sur le dossier {source_tour_nr} (xxatour.FFNR vide)."
                )
                return

            # Le KundenNr vient du dossier : il devient la référence métier même
            # si la fiche transporteur détaillée n'est pas trouvée.
            self.selected_kundennr = kundennr
            self.transporter_selected_mode = True

            try:
                transporter = self.transporter_repo.find_transporter_by_kundennr(kundennr)
            except Exception as e:
                transporter = None
                self.transporter_info.setPlainText(f"Erreur chargement fiche transporteur {kundennr} :\n{e}")

            if not transporter:
                self._set_transporter_input_locked(kundennr)
                self._set_transporter_aux_locked(True, "")
                self.current_db_iban = ""
                self.current_db_bic = ""
                self.current_db_bank_pairs = []
                self.check_bank_information()
                self._refresh_transporter_bank_transfer_button()
                if not self.transporter_info.toPlainText().strip():
                    self.transporter_info.setPlainText(
                        f"Transporteur déterminé par le dossier {source_tour_nr}.\n"
                        "❌ Fiche transporteur introuvable."
                    )
                try:
                    self.update_transporter_vs_dossiers_status()
                except Exception:
                    pass
                try:
                    self._refresh_all_folder_row_statuses()
                except Exception:
                    pass
                return

            aux_row = self.transporter_repo.get_ktoKreA_by_kundennr(kundennr)
            db_aux = str((aux_row or {}).get("KtoKreA") or "").strip()
            pending_aux_kundennr = str(getattr(self, "_pending_saved_transporter_aux_kundennr", "") or "").strip()
            pending_aux_value = str(getattr(self, "_pending_saved_transporter_aux", "") or "").strip()

            if db_aux and self._is_valid_transporter_aux_account(db_aux):
                self._set_transporter_aux_locked(True, db_aux, allow_db_update=False)
            else:
                candidate_aux = db_aux or (pending_aux_value if pending_aux_kundennr == kundennr else "")
                # Valeur BDD vide ou ne commençant pas par 0 : on laisse modifiable
                # pour correction, et le bouton 💾 devient disponible.
                self._set_transporter_aux_locked(False, candidate_aux, allow_db_update=True)

            transporter_name = str(transporter.get("name1", "") or "").strip()
            ustid = str(transporter.get("UstId", "") or transporter.get("USTID", "") or transporter.get("USTIDNR", "") or "").strip()
            self._set_transporter_input_locked(transporter_name or kundennr)

            banks = self.bank_repo.get_all_bank_infos_by_kundennr(kundennr)

            lines = []
            lines.append(f"Transporteur déterminé par le dossier : {source_tour_nr}")
            lines.append(f"Transporteur : {transporter_name}")
            lines.append(f"N° TVA : {ustid or '(non renseigné)'}")

            address_line = [
                str(transporter.get("Strasse", "") or "").strip(),
                str(transporter.get("PLZ", "") or "").strip(),
                str(transporter.get("Ort", "") or "").strip(),
                str(transporter.get("LKZ", "") or "").strip(),
            ]
            address_line = [p for p in address_line if p]

            if address_line:
                lines.append("Adresse : " + ", ".join(address_line))

            if banks:
                lines.append("")
                lines.append("IBAN / SWIFT :")
                seen = set()

                for b in banks:
                    iban = str(b.get("iban", "") or "").strip()
                    bic = str(b.get("bic", "") or "").strip()

                    key = (iban, bic)
                    if key in seen:
                        continue
                    seen.add(key)

                    if iban or bic:
                        lines.append(f"  - {iban} | {bic}")
            else:
                lines.append("")
                lines.append("IBAN / SWIFT : aucun trouvé")

            self.transporter_info.setPlainText("\n".join(lines))

            self.current_db_bank_pairs = [
                (
                    str(b.get("iban", "") or "").strip(),
                    str(b.get("bic", "") or "").strip(),
                )
                for b in (banks or [])
                if str(b.get("iban", "") or "").strip() or str(b.get("bic", "") or "").strip()
            ]
            first_bank = banks[0] if banks else {}
            self.current_db_iban = str(first_bank.get("iban", "") or "").strip()
            self.current_db_bic = str(first_bank.get("bic", "") or "").strip()
            self.check_bank_information()
            self._refresh_transporter_bank_transfer_button()
            self._pending_saved_transporter_aux = ""
            self._pending_saved_transporter_aux_kundennr = ""

            try:
                self.update_transporter_vs_dossiers_status()
            except Exception:
                pass

            # Recalcule immédiatement le rouge/orange des dossiers une fois le
            # KundenNr transporteur connu. Sans ça, le orange n'apparaissait
            # qu'après modification d'un champ.
            try:
                self._refresh_all_folder_row_statuses()
            except Exception:
                pass

            try:
                self._maybe_prompt_duplicate_invoice()
            except Exception:
                pass

        except Exception as e:
            self.selected_kundennr = None
            self.transporter_selected_mode = False
            try:
                self._set_transporter_input_locked("")
                self._set_transporter_aux_locked(True, "")
                self.current_db_bank_pairs = []
                self.check_bank_information()
                self._refresh_transporter_bank_transfer_button()
            except Exception:
                pass
            self.transporter_info.setPlainText(f"Erreur chargement transporteur depuis le dossier :\n{e}")

    def on_bank_fields_changed(self):
        # IBAN/BIC restent contrôlés pour information, mais ne déterminent plus
        # le transporteur. Le bouton ➡ reste disponible pour corriger la banque
        # du transporteur déjà imposé par le premier dossier.
        self.check_bank_information()
        self._refresh_transporter_bank_transfer_button()

    def search_transporters(self, text: str):
        # Le transporteur est imposé par le premier dossier, il n'est plus
        # recherchable / modifiable manuellement.
        try:
            self.transporter_model.setStringList([])
        except Exception:
            pass

    def on_transporter_selected(self, text: str):
        # Sélection manuelle désactivée : on recharge depuis le dossier pour
        # annuler toute tentative venant d'un ancien completer encore présent.
        self.load_transporter_information(force_by_kundennr=False)

    def on_transporter_action(self):
        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()

        if not kundennr:
            QMessageBox.warning(self, "Transfert IBAN/BIC", "Aucun transporteur déterminé par le premier dossier.")
            self._refresh_transporter_bank_transfer_button()
            return

        if not iban or not bic or not validate_iban(iban) or not validate_bic(bic):
            QMessageBox.warning(self, "Transfert IBAN/BIC", "IBAN/BIC OCR incomplets ou invalides.")
            self._refresh_transporter_bank_transfer_button()
            return

        if self._transporter_has_bank_pair(iban, bic):
            QMessageBox.information(self, "Transfert IBAN/BIC", "Cet IBAN/BIC est déjà présent sur la fiche transporteur.")
            self._refresh_transporter_bank_transfer_button()
            return

        old_iban = str(getattr(self, "current_db_iban", "") or "").strip()
        old_bic = str(getattr(self, "current_db_bic", "") or "").strip()
        msg = (
            f"Mettre à jour les coordonnées bancaires du transporteur {kundennr} ?\n\n"
            f"Valeur BD actuelle : {old_iban or '(vide)'} | {old_bic or '(vide)'}\n"
            f"Valeur OCR à transférer : {iban} | {bic}"
        )
        resp = QMessageBox.question(
            self,
            "Transfert IBAN/BIC",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        try:
            result = self.transporter_repo.update_bank(kundennr, iban, bic)

            saved_iban = str((result or {}).get("iban") or iban).strip()
            saved_bic = str((result or {}).get("bic") or bic).strip()
            self.current_db_iban = saved_iban
            self.current_db_bic = saved_bic
            self.current_db_bank_pairs = [(saved_iban, saved_bic)]

            self.check_bank_information()
            self.load_transporter_information(force_by_kundennr=False)
            self._refresh_transporter_bank_transfer_button()

            action = str((result or {}).get("action") or "updated")
            if action == "already_exists":
                msg_ok = "Cet IBAN/BIC était déjà présent sur la fiche transporteur."
            elif action == "inserted":
                msg_ok = "IBAN/BIC créés sur la fiche transporteur et vérifiés en base."
            else:
                msg_ok = "IBAN/BIC mis à jour sur la fiche transporteur et vérifiés en base."
            QMessageBox.information(self, "Transfert IBAN/BIC", msg_ok)
        except Exception as e:
            QMessageBox.critical(self, "Transfert IBAN/BIC", f"Erreur pendant la mise à jour :\n{e}")
            self._refresh_transporter_bank_transfer_button()

    def enable_transporter_update(self):
        self._refresh_transporter_bank_transfer_button()

    def load_tour_information(self, tour_nr: str):
        self.last_loaded_tour_nr = (tour_nr or "").strip()
        self.tour_info.clear()
        tour_nr = (tour_nr or "").strip()

        if not tour_nr:
            self.tour_info.setPlainText("ℹ️ Aucun numéro de dossier.")
            return

        if not re.fullmatch(self.DOSSIER_PATTERN, tour_nr):
            self.tour_info.setPlainText(f"❌ Numéro de dossier invalide : {tour_nr}")
            return

        try:
            record = self.tour_repo.find_by_tournr(tour_nr)
            if not record:
                self.tour_info.setPlainText(f"❌ Tour non trouvée : {tour_nr}")
                return

            info = self.tour_repo.get_tour_extended_info(tour_nr) or {}

            invoice_tours = self._get_current_invoice_tours()
            cmr_tours = self._get_cmr_attached_tours_for_entry()

            missing = sorted(invoice_tours - cmr_tours) if invoice_tours else []
            all_ok = bool(invoice_tours) and not missing
            this_ok = tour_nr in cmr_tours

            global_icon = "✅" if all_ok else ("⚠️" if invoice_tours else "—")
            this_icon = "🧾✅" if this_ok else "🧾❌"

            header = f"🧾 Tour trouvée {global_icon}"
            if missing:
                header += f" | CMR manquantes: {', '.join(missing)}"

            txt = (
                f"{header}\n"
                f"TourNr : {info.get('TourNr', tour_nr)} {this_icon}\n"
                f"Départ : {info.get('Depart', '')}\n"
                f"Arrivée : {info.get('Arrivee', '')}\n"
                f"Date Tour : {info.get('DateTour', '')}\n"
                f"Date Livraison : {info.get('DateLivraison', '')}\n"
                f"Total Poids : {info.get('Total_Poids', '')}\n"
                f"Total MPL : {info.get('Total_MPL', '')}"
            )

            self.tour_info.setPlainText(txt)

        except Exception as e:
            self.tour_info.setPlainText(f"Erreur chargement tour :\n{e}")

    def on_related_pdf_context_menu(self, pos):

        invoice_row = self.pdf_table.currentRow()
        entry_id = None
        invoice_filename = None

        if invoice_row >= 0:
            it = self.pdf_table.item(invoice_row, 0)
            if it:
                invoice_filename = get_left_table_item_filename(it)
                entry_id = self.logmail_repo.get_entry_id_for_file(invoice_filename)

        action_associer.setEnabled(bool(entry_id))

        # (optionnel) garder en mémoire
        self.selected_invoice_filename = invoice_filename
        self.selected_invoice_entry_id = entry_id

        item = self.related_pdf_table.itemAt(pos)
        if not item:
            return

        linked_filename = item.text()

        menu = QMenu(self)

        action_associer = menu.addAction("Associer à la facture sélectionnée (liste du haut)")
        action_associer.setEnabled(bool(self.selected_invoice_entry_id))

        chosen = menu.exec(self.related_pdf_table.viewport().mapToGlobal(pos))
        if chosen != action_associer:
            return

        if not self.selected_invoice_entry_id or not self.selected_invoice_filename:
            QMessageBox.warning(self, "Association", "Aucune facture sélectionnée dans la liste du haut.")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Associer une pièce jointe")
        msg.setText(
            f"Associer le fichier :\n\n"
            f"  {linked_filename}\n\n"
            f"à la facture :\n\n"
            f"  {self.selected_invoice_filename}\n\n"
            f"(entry_id = {self.selected_invoice_entry_id})"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() == QMessageBox.Yes:
            try:
                self.logmail_repo.update_entry_for_file(linked_filename, self.selected_invoice_entry_id)
                QMessageBox.information(self, "Association", "Fichier associé à la facture.")
                self.load_related_pdfs()  # refresh
            except Exception as e:
                QMessageBox.critical(self, "Erreur association", str(e))

    def _format_amount_2(self, v: float) -> str:
        return f"{v:.2f}"

    def _best_ht_amount_for_tour(self, lines: list[str], tour_nr: str) -> float | None:
        dossier_re = re.compile(r"\b\d{8}\b")
        def contains_tour(ln: str) -> bool:
            if tour_nr in ln:
                return True
            # fallback si OCR a mis des espaces / tirets dans le numéro
            compact = re.sub(r"[ \u00A0-]", "", ln)
            return tour_nr in compact

        idx = next((i for i, ln in enumerate(lines) if contains_tour(ln)), None)
        if idx is None:
            return None

        # ✅ fenêtre centrée sur la ligne du dossier (les montants sont souvent juste AVANT)
        start = max(0, idx - 12)
        end = min(len(lines), idx + 25)

        # stop si autre dossier apparaît (avant)
        for j in range(idx - 1, start - 1, -1):
            ln = lines[j]
            for d in dossier_re.findall(ln):
                if d != tour_nr:
                    start = j + 1
                    break
            else:
                continue
            break

        # stop si autre dossier apparaît (après)
        for j in range(idx + 1, end):
            ln = lines[j]
            for d in dossier_re.findall(ln):
                if d != tour_nr:
                    end = j
                    break
            else:
                continue
            break

        best = None  # (score, position, value)
        found_2dec = False

        def prev_nonempty(k: int) -> str:
            for x in range(k - 1, start - 1, -1):
                t = lines[x].strip()
                if t:
                    return t
            return ""

        for j in range(start, end):
            raw = lines[j].strip()
            if not raw:
                continue

            up = raw.upper()

            # ignorer unités parasites
            if "CO2" in up or "CO2E" in up or "KG" in up:
                continue

            # si lettres (hors € / EUR), ignorer
            if HAS_LETTERS_RE.search(raw) and ("€" not in raw and "EUR" not in up):
                continue

            strict_line = bool(ONLY_AMOUNT_2DEC_RE.match(raw))

            for s_amt in AMOUNT_CANDIDATE_RE.findall(raw):
                v = self._parse_amount(s_amt)
                if v is None or v <= 0:
                    continue

                # on évite les taux/quantités
                if v < 50:
                    continue

                mdec = re.search(r"[.,](\d+)$", s_amt)
                dlen = len(mdec.group(1)) if mdec else 0
                if dlen == 2:
                    found_2dec = True

                score = 0

                # ✅ priorité à la proximité de la ligne dossier
                dist = abs(j - idx)
                score += max(0, 25 - dist * 2)

                # décimales
                if dlen == 2:
                    score += 30
                elif dlen == 3:
                    score += 10
                else:
                    score -= 40

                # bonus si montant seul
                if strict_line:
                    score += 80
                    # bonus si la ligne précédente ressemble à une quantité (rare, mais utile)
                    prev = prev_nonempty(j)
                    if re.fullmatch(r"\d{1,3}", prev.strip()):
                        score += 25

                cand = (score, j, round(v, 2))
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and cand[1] > best[1]):
                    best = cand

        if not best:
            return None

        # si on a trouvé des montants en 2 décimales, on refuse les autres
        if found_2dec:
            best2 = None
            for j in range(start, end):
                raw = lines[j].strip()
                if not raw:
                    continue
                up = raw.upper()
                if HAS_LETTERS_RE.search(raw) and ("€" not in raw and "EUR" not in up):
                    continue
                strict_line = bool(ONLY_AMOUNT_2DEC_RE.match(raw))
                for s_amt in AMOUNT_CANDIDATE_RE.findall(raw):
                    v = self._parse_amount(s_amt)
                    if v is None or v < 50:
                        continue
                    mdec = re.search(r"[.,](\d+)$", s_amt)
                    dlen = len(mdec.group(1)) if mdec else 0
                    if dlen != 2:
                        continue

                    dist = abs(j - idx)
                    score = max(0, 25 - dist * 2) + 30
                    if strict_line:
                        score += 80
                    cand = (score, j, round(v, 2))
                    if best2 is None or cand[0] > best2[0] or (cand[0] == best2[0] and cand[1] > best2[1]):
                        best2 = cand
            if best2:
                return best2[2]

        return best[2]

    def autofill_folder_amounts_from_ocr(self, ocr_text: str):
        txt = ocr_text or ""
        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        if not lines:
            return

        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
            if not dossier_le or not amount_le:
                continue

            tour_nr = (dossier_le.text() or "").strip()
            if not tour_nr:
                continue

            # ne pas écraser si déjà rempli
            if (amount_le.text() or "").strip():
                continue

            best = self._best_ht_amount_for_tour(lines, tour_nr)
            if best is not None:
                amount_le.setText(self._format_amount_2(best))

    def _parse_amount(self, s: str):
        if not s:
            return None
        s = s.strip().replace(" ", "").replace("\u00A0", "")
        s = s.replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None


    def _unique_tour_numbers(self, tour_numbers) -> list[str]:
        out = []
        seen = set()
        for t in (tour_numbers or []):
            t = str(t or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _prepare_folder_status_caches(self, tour_numbers) -> None:
        """Précharge en batch les infos nécessaires à l'affichage des dossiers.

        Sur les grosses factures, l'ancien flux lançait plusieurs requêtes SQL
        par ligne de dossier (kosten, TVA, AB, EUROPAL, LISINVOICE, CMR).
        Cette méthode remplit les caches en quelques requêtes groupées.
        Les fonctions unitaires restent en fallback pour l'édition manuelle.
        """
        tours = self._unique_tour_numbers(tour_numbers)
        if not tours:
            return

        if not hasattr(self, "_kosten_cache") or self._kosten_cache is None:
            self._kosten_cache = {}
        if not hasattr(self, "_vat_theo_cache") or self._vat_theo_cache is None:
            self._vat_theo_cache = {}
        if not hasattr(self, "_ab_cache") or self._ab_cache is None:
            self._ab_cache = {}
        if not hasattr(self, "_europal_cache") or self._europal_cache is None:
            self._europal_cache = {}
        if not hasattr(self, "_lisinvoice_tour_exists_cache") or self._lisinvoice_tour_exists_cache is None:
            self._lisinvoice_tour_exists_cache = {}
        if not hasattr(self, "_printed_shipment_tour_exists_cache") or self._printed_shipment_tour_exists_cache is None:
            self._printed_shipment_tour_exists_cache = {}

        missing_kosten = [t for t in tours if t not in self._kosten_cache]
        if missing_kosten:
            try:
                self._kosten_cache.update(self.tour_repo.get_kosten_by_tournrs(missing_kosten) or {})
                for t in missing_kosten:
                    self._kosten_cache.setdefault(t, None)
            except Exception:
                pass

        missing_vat = [t for t in tours if t not in self._vat_theo_cache]
        if missing_vat:
            try:
                if hasattr(self.tour_repo, "get_theoretical_vat_percent_by_tournrs"):
                    self._vat_theo_cache.update(self.tour_repo.get_theoretical_vat_percent_by_tournrs(missing_vat) or {})
                    for t in missing_vat:
                        self._vat_theo_cache.setdefault(t, None)
            except Exception:
                pass

        missing_ab = [t for t in tours if t not in self._ab_cache]
        if missing_ab:
            try:
                if hasattr(self.tour_repo, "has_infosymbol19_311_by_tournrs"):
                    self._ab_cache.update(self.tour_repo.has_infosymbol19_311_by_tournrs(missing_ab) or {})
                    for t in missing_ab:
                        self._ab_cache.setdefault(t, False)
            except Exception:
                pass

        missing_eu = [t for t in tours if t not in self._europal_cache]
        if missing_eu:
            try:
                if hasattr(self.tour_repo, "has_europal_by_tournrs"):
                    self._europal_cache.update(self.tour_repo.has_europal_by_tournrs(missing_eu) or {})
                    for t in missing_eu:
                        self._europal_cache.setdefault(t, False)
            except Exception:
                pass

        missing_lis = [t for t in tours if t not in self._lisinvoice_tour_exists_cache]
        if missing_lis:
            try:
                if hasattr(self.lisinvoice_repo, "get_existing_tournrs"):
                    existing = self.lisinvoice_repo.get_existing_tournrs(missing_lis) or set()
                    for t in missing_lis:
                        self._lisinvoice_tour_exists_cache[t] = t in existing
            except Exception:
                pass

        kunden_nr = str(getattr(self, "selected_kundennr", "") or "").strip()
        if kunden_nr:
            missing_printed = [
                t for t in tours
                if (t, kunden_nr) not in self._printed_shipment_tour_exists_cache
            ]
            if missing_printed:
                try:
                    if hasattr(self.lisinvoice_repo, "get_existing_printed_shipment_tournrs"):
                        existing_printed = self.lisinvoice_repo.get_existing_printed_shipment_tournrs(missing_printed, kunden_nr) or set()
                        for t in missing_printed:
                            self._printed_shipment_tour_exists_cache[(t, kunden_nr)] = t in existing_printed
                except Exception:
                    pass

        try:
            self._prepare_cmr_status_cache(tours)
        except Exception:
            pass

    def _prepare_cmr_status_cache(self, tour_numbers) -> None:
        """Prépare le statut CMR par dossier en une seule passe."""
        tours = self._unique_tour_numbers(tour_numbers)
        key = (tuple(sorted(tours)), str(getattr(self, "selected_invoice_entry_id", "") or ""))
        if getattr(self, "_cmr_status_cache_key", None) == key:
            return

        status = {}
        if not tours:
            self._cmr_status_cache_key = key
            self._cmr_status_by_tour = status
            return

        required = {}
        attached = {}
        legacy = {}
        attachment_counts = defaultdict(int)
        try:
            required = self._get_required_orders_by_tour(set(tours)) or {}
            attached = self._get_cmr_attached_orders_for_entry() or {}
            legacy = getattr(self, "_cmr_legacy_cache", {}) or {}
            if hasattr(self, "_collect_cmr_attachments_for_current_entry"):
                for att_row in (self._collect_cmr_attachments_for_current_entry() or []):
                    t = str((att_row or {}).get("tour_nr") or "").strip()
                    if t:
                        attachment_counts[t] += 1
        except Exception:
            required = {}
            attached = {}
            legacy = {}
            attachment_counts = defaultdict(int)

        for tour in tours:
            req = set(required.get(tour, set()))
            att = set(attached.get(tour, set()))
            if not att and legacy.get(tour, 0) > 0 and len(req) == 1:
                att = set(req)

            covered = set(att)

            attachment_count = int(attachment_counts.get(tour, 0) or 0)

            if not req:
                status[tour] = {"state": "unknown", "required": set(), "attached": att, "missing": [], "attachment_count": attachment_count}
            else:
                missing = sorted(req - covered)
                if not missing:
                    state = "ok"
                elif len(covered) > 0:
                    state = "partial"
                else:
                    state = "missing"
                status[tour] = {"state": state, "required": req, "attached": covered, "missing": missing, "attachment_count": attachment_count}

        self._cmr_status_cache_key = key
        self._cmr_status_by_tour = status

    def _invalidate_cmr_status_cache(self) -> None:
        self._cmr_status_cache_key = None
        self._cmr_status_by_tour = {}

    def _refresh_all_folder_row_statuses(self) -> None:
        rows = self.get_folder_rows() if hasattr(self, "get_folder_rows") else []
        tours = [r.get("tour_nr") for r in rows if r.get("tour_nr")]
        self._prepare_folder_status_caches(tours)

        old_updates = self.folder_table.updatesEnabled() if hasattr(self, "folder_table") else True
        try:
            self.folder_table.setUpdatesEnabled(False)
            for row in range(self.folder_table.rowCount()):
                self._update_folder_row_status(row)
        finally:
            self.folder_table.setUpdatesEnabled(old_updates)

    def update_folder_totals(self):
        rows = self.get_folder_rows()

        tour_nrs = [r["tour_nr"] for r in rows if r.get("tour_nr")]
        if tour_nrs:
            self._prepare_folder_status_caches(tour_nrs)
        kosten_map = {t: getattr(self, "_kosten_cache", {}).get(t) for t in tour_nrs}

        total_db = 0.0
        has_db = False
        for t in tour_nrs:
            v = kosten_map.get(t)
            if v is not None:
                total_db += float(v)
                has_db = True

        total_ocr = 0.0
        has_ocr = False
        for r in rows:
            a = self._parse_amount(r.get("amount_ht_ocr", ""))
            if a is not None:
                total_ocr += a
                has_ocr = True

        # Affichage : si au moins un dossier existe, on montre le total BDD même si introuvable
        if not rows:
            self.lbl_folder_totals.setText("")
            self.lbl_folder_totals.setStyleSheet("padding:4px;")
            return

        bdd_txt = f"{total_db:.2f}" if has_db else "N/A"
        ocr_txt = f"{total_ocr:.2f}" if has_ocr else "N/A"
        self.lbl_folder_totals.setText(f"Total OCR = {ocr_txt} | Total BDD = {bdd_txt}")

        if has_ocr and has_db and abs(total_ocr - total_db) <= 0.01:
            self.lbl_folder_totals.setStyleSheet("padding:4px; background-color:#e6ffe6;")
        else:
            self.lbl_folder_totals.setStyleSheet("padding:4px; background-color:#fff3cd;")

    def _make_folder_cell(self, placeholder: str):
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setClearButtonEnabled(True)
        return le

    def _get_row_widgets(self, row: int):
        dossier_le = self.folder_table.cellWidget(row, 0)
        amount_le = self.folder_table.cellWidget(row, 1)
        vat_theo_le = self.folder_table.cellWidget(row, 2)
        return dossier_le, amount_le, vat_theo_le 

    def _on_folder_cmr_icon_clicked(self, row: int):
        """Ouvre la liste des CMR rattachées au dossier de la ligne."""
        try:
            dossier_le, _amount_le, _vat_theo_le = self._get_row_widgets(row)
            tour_nr = (dossier_le.text() if dossier_le else "").strip()
        except Exception:
            tour_nr = ""

        if not tour_nr:
            return

        if hasattr(self, "_show_cmr_attachments_dialog_for_tour"):
            self._show_cmr_attachments_dialog_for_tour(tour_nr)

    def _add_folder_row(self, dossier: str = "", amount: str = "", vat_theo: str = ""):
        row = self.folder_table.rowCount()
        self.folder_table.insertRow(row)

        dossier_le = self._make_folder_cell("Numéro de dossier")
        dossier_le.setMinimumWidth(96)
        amount_le = self._make_folder_cell("Montant HT (OCR)")
        amount_le.setMinimumWidth(108)

        vat_theo_le = self._make_folder_cell("TVA théorique (%)")
        vat_theo_le.setMinimumWidth(92)
        vat_theo_le.setReadOnly(True)
        vat_theo_le.setFocusPolicy(Qt.NoFocus)
        vat_theo_le.setStyleSheet("background-color: #f3f3f3;")

        cmr_lbl = QPushButton("")
        cmr_lbl.setFlat(True)
        cmr_lbl.setStyleSheet("border: none; padding: 0px;")
        cmr_lbl.setCursor(Qt.PointingHandCursor)
        cmr_lbl.setToolTip("CMR OK ? Cliquez pour voir les CMR rattachées.")
        cmr_lbl.clicked.connect(lambda _checked=False, r=row: self._on_folder_cmr_icon_clicked(r))
        self.folder_table.setCellWidget(row, 3, cmr_lbl)
        
        ab_lbl = QLabel("")
        ab_lbl.setAlignment(Qt.AlignCenter)
        ab_lbl.setToolTip("Achat.Bloqué")
        self.folder_table.setCellWidget(row, 4, ab_lbl)

        reserved_lbl = QLabel("")
        reserved_lbl.setAlignment(Qt.AlignCenter)
        ab_lbl.setToolTip("EP")
        self.folder_table.setCellWidget(row, 5, reserved_lbl)

        dossier_le.setText("" if dossier is None else str(dossier))
        amount_le.setText("" if amount is None else str(amount))
        vat_theo_le.setText("" if vat_theo is None else str(vat_theo))

        self._bind_active_field_click(dossier_le)
        self._bind_active_field_click(amount_le)

        dossier_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        amount_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        dossier_le.editingFinished.connect(self.compact_folder_rows)
        amount_le.editingFinished.connect(self.compact_folder_rows)

        if getattr(self, "_invoice_validated_locked", False):
            try:
                dossier_le.setReadOnly(True)
                amount_le.setReadOnly(True)
                dossier_le.setToolTip("Facture déjà validée : champ non modifiable.")
                amount_le.setToolTip("Facture déjà validée : champ non modifiable.")
            except Exception:
                pass

        self.folder_table.setCellWidget(row, 0, dossier_le)
        self.folder_table.setCellWidget(row, 1, amount_le)
        self.folder_table.setCellWidget(row, 2, vat_theo_le)

        if not getattr(self, "_folder_bulk_loading", False):
            self._update_folder_row_status(row)

    def _ensure_empty_folder_row(self):
        # si aucune ligne -> en créer une vide
        if self.folder_table.rowCount() == 0:
            self._add_folder_row("", "")
            return

        last = self.folder_table.rowCount() - 1
        dossier_le, amount_le, vat_theo_le = self._get_row_widgets(last)
        dossier_txt = (dossier_le.text() if dossier_le else "").strip()
        amount_txt = (amount_le.text() if amount_le else "").strip()

        # si la dernière ligne n'est plus vide -> ajouter une nouvelle ligne vide
        if dossier_txt or amount_txt:
            self._add_folder_row("", "")

    def _on_folder_row_changed(self, row: int):
        self._update_folder_row_status(row)
        self.update_folder_totals()
        self._ensure_empty_folder_row()

        # Le transporteur dépend uniquement du premier dossier : dès que les
        # dossiers changent, on le recalcule depuis xxatour.FFNR.
        try:
            first_tour = self._get_first_folder_number()
            if first_tour != getattr(self, "_last_transporter_source_tour_nr", None):
                self._last_transporter_source_tour_nr = first_tour
                self._refresh_transporter_from_first_folder()
        except Exception:
            pass

        # si le champ actif est le dossier de cette ligne, refresh le volet tour
        dossier_le, _, vat_theo_le = self._get_row_widgets(row)
        if self.active_field == dossier_le:
            self.load_tour_information(dossier_le.text())

    def get_folder_rows(self):
        rows = []
        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
            dossier = (dossier_le.text() if dossier_le else "").strip()
            amount = (amount_le.text() if amount_le else "").strip()
            # ignorer la ligne totalement vide (celle du bas)
            if dossier or amount:
                rows.append({"tour_nr": dossier, "amount_ht_ocr": amount})
        return rows

    def _update_folder_row_status(self, row: int):
        dossier_le, amount_le, vat_theo_le = self._get_row_widgets(row)
        if not dossier_le or not amount_le:
            return

        tour_nr = dossier_le.text().strip()

        cmr_lbl = self._get_row_cmr_widget(row)

        ab_lbl = self._get_row_ab_widget(row)
        if ab_lbl is not None:
            if not tour_nr:
                ab_lbl.setText("")
                ab_lbl.setStyleSheet("")
                ab_lbl.setToolTip("")
            else:
                try:
                    if tour_nr in getattr(self, "_ab_cache", {}):
                        has_ab = self._ab_cache[tour_nr]
                    else:
                        has_ab = bool(self.tour_repo.has_infosymbol19_311_for_tournr(tour_nr))
                        self._ab_cache[tour_nr] = has_ab

                    if has_ab:
                        ab_lbl.setText("❌")
                        ab_lbl.setStyleSheet("color:#dc3545; font-weight:bold;")
                        ab_lbl.setToolTip("AB détecté : au moins une commande avec InfoSymbol19=311")
                    else:
                        ab_lbl.setText("")
                        ab_lbl.setStyleSheet("")
                        ab_lbl.setToolTip("AB non détecté (InfoSymbol19=311 absent)")
                except Exception as e:
                    ab_lbl.setText("❓")
                    ab_lbl.setStyleSheet("color:#b58900; font-weight:bold;")
                    ab_lbl.setToolTip(f"Erreur contrôle AB: {e}")


        eu_lbl = self._get_row_europal_widget(row)
        if eu_lbl is not None:
            if not tour_nr:
                eu_lbl.setText("")
                eu_lbl.setStyleSheet("")
                eu_lbl.setToolTip("")
            else:
                try:
                    if tour_nr in getattr(self, "_europal_cache", {}):
                        has_eu = self._europal_cache[tour_nr]
                    else:
                        has_eu = bool(self.tour_repo.has_europal_for_tournr(tour_nr))
                        self._europal_cache[tour_nr] = has_eu

                    if has_eu:
                        eu_lbl.setText("✅")  # symbole V
                        eu_lbl.setStyleSheet("color:#28a745; font-weight:bold;")
                        eu_lbl.setToolTip("EUROPAL trouvé (xxav_LIS_SUMTOUR_228794)")
                    else:
                        eu_lbl.setText("❌")  # symbole X
                        eu_lbl.setStyleSheet("color:#dc3545; font-weight:bold;")
                        eu_lbl.setToolTip("EUROPAL absent (xxav_LIS_SUMTOUR_228794)")
                except Exception as e:
                    eu_lbl.setText("❓")
                    eu_lbl.setStyleSheet("color:#b58900; font-weight:bold;")
                    eu_lbl.setToolTip(f"Erreur contrôle EUROPAL: {e}")

        # CMR icon
        if cmr_lbl is not None:
            if not tour_nr:
                cmr_lbl.setText("")
                cmr_lbl.setToolTip("")
            else:
                try:
                    self._prepare_cmr_status_cache(self.get_folder_numbers())
                    cmr_status = (getattr(self, "_cmr_status_by_tour", {}) or {}).get(tour_nr, {})
                except Exception:
                    cmr_status = {}

                state = cmr_status.get("state")
                req = set(cmr_status.get("required") or set())
                attached = set(cmr_status.get("attached") or set())
                missing = list(cmr_status.get("missing") or [])
                attachment_count = int(cmr_status.get("attachment_count") or 0)
                suffix_cmr = f" {attachment_count} CMR rattachée(s)." if attachment_count else ""

                if state == "unknown" or not req:
                    cmr_lbl.setText("🧾❓")
                    cmr_lbl.setToolTip("Aucune commande (AufNr) trouvée en BDD pour ce dossier." + suffix_cmr + " Cliquez pour voir les CMR rattachées.")
                elif state == "ok":
                    cmr_lbl.setText("🧾✅")
                    cmr_lbl.setToolTip(f"Toutes les commandes ont une CMR ({len(req)}/{len(req)})." + suffix_cmr + " Cliquez pour voir/supprimer les CMR rattachées.")
                elif state == "partial":
                    cmr_lbl.setText("🧾⚠️")
                    cmr_lbl.setToolTip(f"CMR partielle: {len(attached)}/{len(req)}. Manque: {', '.join(missing[:10])}" + ("..." if len(missing) > 10 else "") + "." + suffix_cmr + " Cliquez pour voir/supprimer les CMR rattachées.")
                else:
                    cmr_lbl.setText("🧾❌")
                    cmr_lbl.setToolTip(f"Aucune CMR sur les commandes. Attendu: {len(req)} commande(s)." + suffix_cmr + " Cliquez pour voir les éventuels rattachements.")



        amount_ocr = self._parse_amount(amount_le.text())

        dossier_le.setStyleSheet("")
        dossier_le.setToolTip("")
        amount_le.setStyleSheet("")
        amount_le.setToolTip("")

        # ligne vide => neutre
        if not tour_nr:
            vat_theo_le.setText("")
            vat_theo_le.setToolTip("")
            return

        already_invoiced = self._apply_tour_invoicing_style(dossier_le, tour_nr)
        
        # TVA théorique (BDD)
        try:
            if tour_nr in self._vat_theo_cache:
                vat_val = self._vat_theo_cache.get(tour_nr)
            else:
                vat_val = self.tour_repo.get_theoretical_vat_percent_by_tournr(tour_nr)
                self._vat_theo_cache[tour_nr] = vat_val

            if vat_val is not None:
                vat_theo_le.setText(self._format_percent(vat_val))
                vat_theo_le.setToolTip(f"TVA théorique BDD = {vat_val}")
            else:
                vat_theo_le.setText("")
                vat_theo_le.setToolTip("TVA théorique introuvable en BDD.")
        except Exception as e:
            vat_theo_le.setText("")
            vat_theo_le.setToolTip(f"Erreur BDD TVA: {e}")


        try:
            if not hasattr(self, "_kosten_cache") or self._kosten_cache is None:
                self._kosten_cache = {}
            if tour_nr in self._kosten_cache:
                db_kosten = self._kosten_cache.get(tour_nr)
            else:
                db_kosten = self.tour_repo.get_kosten_by_tournr(tour_nr)
                self._kosten_cache[tour_nr] = db_kosten
        except Exception as e:
            amount_le.setStyleSheet("background-color: #ffe6e6;")
            amount_le.setToolTip(f"Erreur BDD: {e}")
            return

        if db_kosten is None:
            if already_invoiced:
                dossier_le.setStyleSheet("background-color:#ffe6e6; color:#dc3545;")
                dossier_le.setToolTip("Le dossier est déjà en facturation (LISINVOICE_EDTRANS), mais la tournée est introuvable dans xxatour.")
            else:
                dossier_le.setStyleSheet("background-color: #ffe6e6;")
                dossier_le.setToolTip("Tour non trouvée en base (xxatour).")
            amount_le.setStyleSheet("background-color: #ffe6e6;")
            amount_le.setToolTip("Tour non trouvée en base (xxatour).")
            return

        try:
            db_val = float(db_kosten)
        except Exception:
            db_val = None

        amount_le.setToolTip(f"Montant BDD (kosten) = {db_val}")

        if amount_ocr is None or db_val is None:
            amount_le.setStyleSheet("background-color: #fff3cd;")
            return

        if abs(amount_ocr - db_val) <= 0.01:
            amount_le.setStyleSheet("background-color: #e6ffe6;")
        else:
            amount_le.setStyleSheet("background-color: #fff3cd;")

    def get_folder_numbers(self) -> list[str]:
        return [r["tour_nr"] for r in self.get_folder_rows() if r.get("tour_nr")]

    def _make_vat_cell(self, placeholder: str):
        le = QLineEdit()
        le.setPlaceholderText(placeholder)
        le.setClearButtonEnabled(True)
        return le

    def _get_vat_row_widgets(self, row: int):
        rate_le = self.vat_table.cellWidget(row, 0)
        base_le = self.vat_table.cellWidget(row, 1)
        vat_le  = self.vat_table.cellWidget(row, 2)
        return rate_le, base_le, vat_le

    def _add_vat_row(self, rate: str = "", base: str = "", vat: str = ""):
        row = self.vat_table.rowCount()
        self.vat_table.insertRow(row)

        rate_le = self._make_vat_cell("ex: 20")
        base_le = self._make_vat_cell("Base HT")
        vat_le  = self._make_vat_cell("Montant TVA")

        rate_le.setText("" if rate is None else str(rate))
        base_le.setText("" if base is None else str(base))
        vat_le.setText("" if vat is None else str(vat))

        # champ actif
        self._bind_active_field_click(rate_le)
        self._bind_active_field_click(base_le)
        self._bind_active_field_click(vat_le)

        # changements => total + ligne vide
        rate_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))
        base_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))
        vat_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))

        if getattr(self, "_invoice_validated_locked", False):
            try:
                for _le in (rate_le, base_le, vat_le):
                    _le.setReadOnly(True)
                    _le.setToolTip("Facture déjà validée : champ non modifiable.")
            except Exception:
                pass

        self.vat_table.setCellWidget(row, 0, rate_le)
        self.vat_table.setCellWidget(row, 1, base_le)
        self.vat_table.setCellWidget(row, 2, vat_le)

    def _ensure_empty_vat_row(self):
        if self.vat_table.rowCount() == 0:
            self._add_vat_row("", "", "")
            return

        last = self.vat_table.rowCount() - 1
        rate_le, base_le, vat_le = self._get_vat_row_widgets(last)
        rate_txt = (rate_le.text() if rate_le else "").strip()
        base_txt = (base_le.text() if base_le else "").strip()
        vat_txt  = (vat_le.text() if vat_le else "").strip()

        if rate_txt or base_txt or vat_txt:
            self._add_vat_row("", "", "")

    def _on_vat_row_changed(self, row: int):
        self.update_vat_total()
        self._ensure_empty_vat_row()

    def get_vat_rows(self):
        rows = []
        for r in range(self.vat_table.rowCount()):
            rate_le, base_le, vat_le = self._get_vat_row_widgets(r)
            rate = (rate_le.text() if rate_le else "").strip()
            base = (base_le.text() if base_le else "").strip()
            vat  = (vat_le.text() if vat_le else "").strip()
            if rate or base or vat:
                rows.append({"rate": rate, "base": base, "vat": vat})
        return rows

    def update_vat_total(self):
        base_total = 0.0
        vat_total = 0.0
        has_any = False

        # ✅ dédoublonnage des lignes (rate, base, vat) pour éviter double comptage
        seen = set()  # (rate, base, vat) arrondis

        for r in range(self.vat_table.rowCount()):
            rate_le, base_le, vat_le = self._get_vat_row_widgets(r)

            rate_txt = (rate_le.text() if rate_le else "").strip()
            base_txt = (base_le.text() if base_le else "").strip()
            vat_txt  = (vat_le.text() if vat_le else "").strip()

            # ligne vide -> ignore
            if not rate_txt and not base_txt and not vat_txt:
                continue

            b = self._parse_amount(base_txt)
            v = self._parse_amount(vat_txt)
            rt = self._parse_amount(rate_txt)

            # si on n'a pas base+vat, on n'additionne pas (évite les lignes incomplètes)
            if b is None and v is None:
                continue

            # clé de déduplication si on a tout
            if rt is not None and b is not None and v is not None:
                key = (round(rt, 2), round(b, 2), round(v, 2))
                if key in seen:
                    continue
                seen.add(key)

            if b is not None:
                base_total += b
                has_any = True
            if v is not None:
                vat_total += v
                has_any = True

        if not has_any:
            self.lbl_vat_total.setText("")
            self.lbl_vat_total.setStyleSheet("padding:4px;")
            return

        ttc_total = base_total + vat_total

        self.lbl_vat_total.setText(
            f"Base HT = {base_total:.2f} | Total TVA = {vat_total:.2f} | Total TTC = {ttc_total:.2f}"
        )

        # vert (info) : total calculé
        self.lbl_vat_total.setStyleSheet("padding:4px; background-color:#e6ffe6;")
        
    def _get_row_ab_widget(self, row: int):
        return self.folder_table.cellWidget(row, 4)
    
    def _get_row_europal_widget(self, row: int):
        return self.folder_table.cellWidget(row, 5)

