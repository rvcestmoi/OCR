from __future__ import annotations
import fitz
import pytesseract
from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled
from ocr.invoice_parser import normalize_date_format
from ocr.supplier_model import validate_iban, validate_bic


class MainWindowCoreMixin:

    def _bind_active_field_click(self, field):
        """Rend un champ actif au clic sans casser le comportement natif Qt.

        Avant, on remplaçait directement mousePressEvent par set_active_field(...).
        Le clic ne passait donc plus par QLineEdit.mousePressEvent : Qt ne pouvait
        plus positionner le curseur à l'endroit exact cliqué, et le curseur finissait
        souvent en fin de texte.
        """
        if field is None or getattr(field, "_ocr_active_click_bound", False):
            return

        original_mouse_press = field.mousePressEvent
        field._ocr_original_mouse_press_event = original_mouse_press
        field._ocr_active_click_bound = True

        def _mouse_press_event(event, _field=field, _original=original_mouse_press):
            try:
                _original(event)
            finally:
                try:
                    self.set_active_field(_field)
                except Exception:
                    pass

        field.mousePressEvent = _mouse_press_event

    def set_active_field(self, field):
        self.active_field = field

        self.pdf_viewer.active_field = field
        self.pdf_viewer.field_colors = self.FIELD_COLORS

        field.setStyleSheet("background-color: #fff3cd;")
        try:
            self._highlight_pdf_field_for_widget(field)
        except Exception:
            pass

        # ✅ Volet info selon champ actif
        # ✅ Volet info selon champ actif
        if field in (self.iban_input, self.bic_input):
            # IBAN/BIC : contrôle banque uniquement. Le transporteur reste celui du premier dossier.
            self.check_bank_information()
            self.load_transporter_information(force_by_kundennr=False)
            return

        if field == self.transporter_input:
            # Transporteur verrouillé : il est déterminé par le premier dossier.
            self.load_transporter_information(force_by_kundennr=False)
            return

        for r in range(self.folder_table.rowCount()):
            dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
            if field == dossier_le or field == amount_le or field == vat_theo_le:
                self.load_tour_information(dossier_le.text())
                return

    def fill_active_field(self, text: str):
        if not self.active_field:
            return

        value = text.strip()

        if self.active_field == self.invoice_number_input:
            value = "".join(c for c in value if c.isdigit())
        elif self.active_field in self.get_folder_line_edits():
            # extraction dossier via pattern
            m = re.search(self.DOSSIER_PATTERN, value)
            value = m.group(0) if m else ""
        elif self.active_field == self.iban_input:
            value = value.replace(" ", "").upper()
        elif self.active_field == self.date_input:
            value = normalize_date_format(value)

        self.active_field.setText(value)
        self.active_field.setText(value)
        self.active_field.setStyleSheet("background-color: #e6ffe6;")

        if self.active_field in (self.iban_input, self.bic_input):
            QTimer.singleShot(0, self._refresh_transporter_after_bank_autofill)

        self.active_field.setStyleSheet("background-color: #e6ffe6;")

    def get_folder_line_edits(self) -> list[QLineEdit]:
        out = []
        for r in range(self.folder_table.rowCount()):
            dossier_le, _ , vat_theo_le= self._get_row_widgets(r)
            if dossier_le:
                out.append(dossier_le)
        return out

    def clear_folder_fields(self, *args, **kwargs):
        self.folder_table.setRowCount(0)
        self._ensure_empty_folder_row()
        self.update_folder_totals()

    def on_folder_changed(self, line_edit: QLineEdit):
        # Si on est en train d’éditer ce champ dossier, on refresh le volet info tour
        if self.active_field == line_edit:
            self.load_tour_information(line_edit.text())

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Sélectionner un dossier",
            self.DEFAULT_PDF_FOLDER
        )
        if not folder:
            return

        self.load_folder(folder)

    
    def on_pdf_selected(self, row, column):
        item = self.pdf_table.item(row, 0)
        if not item:
            return

        self.current_pdf_path = item.data(Qt.UserRole)
        if not self.current_pdf_path:
            return

        self.selected_invoice_filename = os.path.basename(self.current_pdf_path)

        entry_id = item.data(Qt.UserRole + 4)
        new_entry_id = str(entry_id or "").strip()
        
        if not new_entry_id or new_entry_id.startswith("__NO_ENTRY__"):
            new_entry_id = None

        self.selected_invoice_entry_id = new_entry_id

        if new_entry_id:
            self._claim_selected_entry(new_entry_id)
        else:
            self._release_claimed_entry()

        group_paths = item.data(Qt.UserRole + 5)
        if isinstance(group_paths, list) and group_paths:
            rep = self.current_pdf_path
            paths = [rep] + [p for p in group_paths if p != rep]
            self.entry_pdf_paths = paths
            self.current_doc_index = 0
            self.update_doc_indicator()
            self.show_doc_by_index(0)
        else:
            self.build_entry_pdf_group()
            self.show_doc_by_index(0)

        self.view_pdf_path = self.current_pdf_path
        self.refresh_invoice_data()

        new_path = item.data(Qt.UserRole)
        if getattr(self, "_last_main_selected_path", None) == new_path:
            return
        self._last_main_selected_path = new_path
        self.current_pdf_path = new_path

    def display_pdf(self):
        doc_path = self.view_pdf_path or self.current_pdf_path
        if not doc_path or not os.path.exists(doc_path):
            return

        try:
            if is_image_document(doc_path):
                pix = QPixmap(doc_path)
                if pix.isNull():
                    raise RuntimeError(f"Impossible de charger l'image : {doc_path}")

                self.pdf_viewer.set_pages([pix])
                self.lbl_page_info.setText("Image")
                self.btn_prev_page.setEnabled(False)
                self.btn_next_page.setEnabled(False)
                return

            # Optimisation gros PDF : le viewer rend maintenant les pages à la demande.
            # Avant, toutes les pages étaient converties en images dès l'ouverture,
            # ce qui pouvait bloquer plusieurs secondes/minutes sur des PDF volumineux.
            if hasattr(self.pdf_viewer, "set_pdf_file"):
                self.pdf_viewer.set_pdf_file(doc_path)
            else:
                doc = fitz.open(doc_path)
                pixmaps = []
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                    pixmaps.append(QPixmap.fromImage(img))
                self.pdf_viewer.set_pages(pixmaps)
                doc.close()

            self.update_page_indicator()

        except Exception as e:
            QMessageBox.critical(self, "Erreur document", str(e))



    def refresh_invoice_data(self):
        """Recharge les données pour le PDF sélectionné.
        - Si un JSON existe : on recharge.
        - Sinon : on OCR automatiquement.
        """
        if not self.current_pdf_path:
            return

        # En changeant de facture, on enlève d'abord un éventuel verrou hérité
        # de la facture précédente. Il sera réappliqué à la fin si la nouvelle
        # facture est déjà validée.
        try:
            self._apply_invoice_validated_lock(False)
        except Exception:
            pass

        # reset UI
        self.bank_valid = None
        self.selected_kundennr = None
        self.current_db_iban = None
        self.current_db_bic = None
        self.current_db_bank_pairs = []
        self.transporter_selected_mode = False
        self._pending_saved_transporter_aux = ""
        self._pending_saved_transporter_aux_kundennr = ""
        self._transporter_aux_match_ok = None
        self._transporter_aux_locked = True
        self.pallet_details = {}
        self.block_options = {}
        self._supplier_kundennr_by_tour_cache = {}
        self._last_transporter_source_tour_nr = None
        self._bank_transporter_mismatch = False
        self._bank_transporter_mismatch_fields = set()
        self.field_positions = {}
        try:
            self.pdf_viewer.clear_highlights()
        except Exception:
            pass

        # champs facture
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.blockSignals(True)
            field.clear()
            field.setStyleSheet("")
            field.blockSignals(False)

        # transporteur
        if hasattr(self, "_set_transporter_input_locked"):
            self._set_transporter_input_locked("")
        else:
            self.transporter_input.blockSignals(True)
            self.transporter_input.clear()
            self.transporter_input.setReadOnly(True)
            self.transporter_input.setFocusPolicy(Qt.ClickFocus)
            self.transporter_input.setClearButtonEnabled(False)
            self.transporter_input.setStyleSheet("background-color: #f3f3f3;")
            self.transporter_input.blockSignals(False)
        if hasattr(self, "_set_transporter_aux_locked"):
            self._set_transporter_aux_locked(True, "")
        else:
            self.transporter_aux_input.clear()
            self.transporter_aux_input.setReadOnly(True)
            self.transporter_aux_input.setFocusPolicy(Qt.NoFocus)
            self.transporter_aux_input.setStyleSheet("background-color: #f3f3f3;")
        self.btn_transporter_action.setEnabled(False)
        self.transporter_info.clear()
        if hasattr(self, "tour_info") and self.tour_info is not None:
            self.tour_info.clear()

        # expéditeur
        self.sender_input.clear()

        # commentaire libre facture
        if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
            self.invoice_comment_input.setPlainText("")

        # dossiers + TVA
        self.clear_folder_fields()
        self.vat_table.setRowCount(0)
        self._ensure_empty_vat_row()
        self.update_vat_total()

        # OCR texte + recherche
        self.ocr_text_view.setPlainText("")
        self.search_selections = []
        self.current_match_index = -1
        self.search_counter_label.setText("0 / 0")

        # ✅ Pré-remplir depuis la BDD (liste de gauche) quand dispo
        # Objectif : les champs "légers" (date/iban/bic) viennent prioritairement de SQL,
        # et le JSON reste pour les champs lourds (OCR, dossiers, TVA, etc.).
        try:
            row = self.pdf_table.currentRow() if hasattr(self, "pdf_table") else -1
            if row is not None and row >= 0 and getattr(self, "pdf_table", None) is not None:
                it_date = self.pdf_table.item(row, 1)  # Date (col 1)
                it_iban = self.pdf_table.item(row, 2)  # IBAN (col 2)
                it_bic  = self.pdf_table.item(row, 3)  # BIC  (col 3)

                db_date = (it_date.text() if it_date else "").strip()
                db_iban = (it_iban.text() if it_iban else "").strip()
                db_bic  = (it_bic.text() if it_bic else "").strip()

                if db_date:
                    self.date_input.setText(normalize_date_format(db_date))
                if db_iban:
                    self.iban_input.setText(db_iban)
                if db_bic:
                    self.bic_input.setText(db_bic)
        except Exception:
            pass

        # ✅ Charger les données sauvegardées depuis le JSON si elles existent
        try:
            saved_data = self._read_saved_invoice_json(self.current_pdf_path) or {}
            self.pallet_details = saved_data.get("pallet_details", {}) or {}
            self.block_options = saved_data.get("block_options", {}) or {}
            self.field_positions = self._normalize_field_positions(saved_data.get("field_positions") or {})
        except Exception:
            saved_data = {}
            self.field_positions = {}

        # ✅ Le fichier JSON de sauvegarde est la source la plus récente.
        # Important en multi-utilisateur : la liste de gauche peut contenir des
        # valeurs SQL déjà chargées en mémoire avant la sauvegarde d'un collègue.
        # On doit donc appliquer le JSON même si les champs sont déjà remplis,
        # et même si une valeur a été volontairement vidée puis sauvegardée.
        if "iban" in saved_data:
            self.iban_input.setText(str(saved_data.get("iban") or "").strip())
        if "bic" in saved_data:
            self.bic_input.setText(str(saved_data.get("bic") or "").strip())
        if "invoice_date" in saved_data:
            saved_invoice_date = str(saved_data.get("invoice_date") or "").strip()
            self.date_input.setText(normalize_date_format(saved_invoice_date) if saved_invoice_date else "")
        if "invoice_number" in saved_data:
            self.invoice_number_input.setText(str(saved_data.get("invoice_number") or "").strip())

        # Commentaire libre facture : champ optionnel, compatible anciens JSON.
        # Important : refresh_invoice_data() est le flux utilisé à l'ouverture
        # d'une facture depuis le volet gauche. Sans ce bloc, le commentaire
        # pouvait bien être écrit dans le JSON mais ne jamais être réaffiché,
        # donnant l'impression qu'il n'était pas sauvegardé.
        if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
            invoice_comment = str(
                saved_data.get("invoice_comment")
                or saved_data.get("free_invoice_comment")
                or saved_data.get("commentaire_libre")
                or ""
            )
            self.invoice_comment_input.setPlainText(invoice_comment)

        # Transporteur : le texte sauvegardé n'est plus restauré directement.
        # Il est recalculé depuis le premier dossier via xxatour.FFNR.
        if "transporter_aux_account" in saved_data:
            saved_aux_account = str(saved_data.get("transporter_aux_account") or "").strip()
            self.transporter_aux_input.setText(saved_aux_account)
            self._pending_saved_transporter_aux = saved_aux_account
        else:
            saved_aux_account = ""

        # Ancienne valeur JSON conservée uniquement pour faire le lien avec le
        # compte auxiliaire si elle correspond encore au KundenNr du dossier.
        if "transporter_kundennr" in saved_data or "selected_kundennr" in saved_data:
            self._pending_saved_transporter_aux_kundennr = str(
                saved_data.get("transporter_kundennr")
                or saved_data.get("selected_kundennr")
                or ""
            ).strip()

        # Dossiers
        folders = saved_data.get("folders") or []
        if folders:
            folder_rows_to_load = []
            for folder in folders:
                if isinstance(folder, dict):
                    folder_rows_to_load.append((
                        folder.get("tour_nr", ""),
                        folder.get("amount_ht_ocr", ""),
                        "",  # vat_theo n'est pas sauvegardé, il est recalculé depuis la BDD
                    ))

            # Optimisation gros PDF : précharger les statuts BDD en batch et
            # construire le tableau sans recalcul ligne par ligne.
            try:
                self._prepare_folder_status_caches([r[0] for r in folder_rows_to_load])
            except Exception:
                pass

            old_updates = self.folder_table.updatesEnabled()
            self._folder_bulk_loading = True
            try:
                self.folder_table.setUpdatesEnabled(False)
                self.folder_table.setRowCount(0)
                for tour_nr, amount, vat_theo in folder_rows_to_load:
                    self._add_folder_row(tour_nr, amount, vat_theo)
                self._ensure_empty_folder_row()
            finally:
                self._folder_bulk_loading = False
                self.folder_table.setUpdatesEnabled(old_updates)

            try:
                self._refresh_all_folder_row_statuses()
            except Exception:
                pass

        # TVA
        vat_lines = saved_data.get("vat_lines") or []
        if vat_lines:
            old_updates = self.vat_table.updatesEnabled()
            try:
                self.vat_table.setUpdatesEnabled(False)
                # Supprimer la ligne vide ajoutée par _ensure_empty_vat_row
                self.vat_table.setRowCount(0)
                for vat_line in vat_lines:
                    if isinstance(vat_line, dict):
                        rate = vat_line.get("rate", "")
                        base = vat_line.get("base", "")
                        vat = vat_line.get("vat", "")
                        self._add_vat_row(rate, base, vat)
            finally:
                self.vat_table.setUpdatesEnabled(old_updates)

        # OCR text
        if saved_data.get("ocr_text"):
            self.ocr_text_view.setPlainText(saved_data["ocr_text"])

        try:
            self.pdf_viewer.set_highlights(getattr(self, "field_positions", {}) or {})
        except Exception:
            pass

        # Mettre à jour les totaux
        self.update_folder_totals()
        self.update_vat_total()
        self._ensure_empty_vat_row()

        # Vérifier les informations bancaires si IBAN/BIC remplis, mais le
        # transporteur vient uniquement du premier dossier.
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        if iban and bic:
            self.check_bank_information()
        self.load_transporter_information(force_by_kundennr=False)

        # Charger l'expéditeur depuis logmail
        entry_id = getattr(self, "selected_invoice_entry_id", None)
        if entry_id:
            try:
                sender = self.logmail_repo.get_sender_for_entry_id(entry_id)
                if sender:
                    self.sender_input.setText(sender)
                else:
                    self.sender_input.clear()
            except Exception:
                self.sender_input.clear()
        else:
            self.sender_input.clear()

        # Verrouiller l'édition si la facture est déjà validée. La vérification
        # relit la BDD en priorité pour éviter qu'un JSON local ancien autorise
        # une modification sur une facture déjà validée par un autre utilisateur.
        try:
            self._apply_invoice_validated_lock(self._is_invoice_already_validated(pdf_path=self.current_pdf_path))
        except Exception:
            pass

    def _confirm_relaunch_ocr_data(self, title: str) -> bool:
        reply = QMessageBox.question(
            self,
            title,
            "Voulez-vous relancer les données ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return reply == QMessageBox.Yes

    def _resolve_supplier_kundennr_from_folders(self) -> tuple[str, str]:
        """Retourne (KundenNr, TourNr source) depuis le premier dossier.

        Le transporteur et les modèles supplier sont désormais rattachés au
        premier TourNr de la facture. On ne cherche plus un transporteur en
        parcourant IBAN/BIC ou une sélection manuelle.
        """
        try:
            if hasattr(self, "_resolve_transporter_from_first_folder"):
                kundennr, source_tour_nr, _err = self._resolve_transporter_from_first_folder()
                return str(kundennr or "").strip(), str(source_tour_nr or "").strip()
        except Exception:
            pass

        try:
            folders = self.get_folder_numbers() if hasattr(self, "get_folder_numbers") else []
        except Exception:
            folders = []

        source_tour_nr = str((folders or [""])[0] or "").strip()
        if not source_tour_nr:
            return "", ""

        cache = getattr(self, "_supplier_kundennr_by_tour_cache", None)
        if cache is None:
            cache = {}
            self._supplier_kundennr_by_tour_cache = cache

        if source_tour_nr in cache:
            kundennr = str(cache.get(source_tour_nr) or "").strip()
        else:
            try:
                kundennr = str(self.tour_repo.get_ffnr_for_tour(source_tour_nr) or "").strip()
            except Exception:
                kundennr = ""
            cache[source_tour_nr] = kundennr

        return kundennr, source_tour_nr

    def _extract_kundennr_from_transporter_input(self) -> str:
        try:
            m = re.search(r"\((\d+)\)\s*$", self.transporter_input.text() or "")
            if m:
                return m.group(1).strip()
        except Exception:
            pass
        return ""

    def _get_supplier_model_context(self, iban: str = "", bic: str = "") -> dict:
        iban = str(iban or self.iban_input.text() or "").strip()
        bic = str(bic or self.bic_input.text() or "").strip()

        kundennr, source_tour_nr = self._resolve_supplier_kundennr_from_folders()
        source = "tour" if kundennr else ""

        primary_key = build_supplier_key_by_kundennr(kundennr) if kundennr else None
        legacy_key = build_supplier_key(iban, bic)

        return {
            "kundennr": kundennr,
            "kundennr_source": source,
            "source_tour_nr": source_tour_nr,
            "primary_key": primary_key,
            "legacy_key": legacy_key,
            "iban": iban,
            "bic": bic,
        }

    def _load_supplier_model_for_current_context(self, *, allow_legacy: bool = True) -> tuple[dict | None, dict]:
        ctx = self._get_supplier_model_context()
        candidates = []

        if ctx.get("primary_key"):
            candidates.append((ctx["primary_key"], "kundennr"))

        legacy_key = ctx.get("legacy_key")
        # Rétrocompatibilité uniquement si le KundenNr du premier dossier existe :
        # on peut migrer un ancien modèle IBAN/BIC vers KUNDENNR_xxx, mais on ne
        # charge plus un transporteur/modèle uniquement depuis IBAN/BIC.
        if ctx.get("primary_key") and allow_legacy and legacy_key and legacy_key != ctx.get("primary_key"):
            candidates.append((legacy_key, "bank"))

        for key, key_type in candidates:
            try:
                model = load_supplier_model(key)
            except Exception:
                model = None
            if model:
                ctx["loaded_key"] = key
                ctx["loaded_key_type"] = key_type
                return model, ctx

        return None, ctx

    def _merge_vat_lines_from_supplier_model(self, model: dict) -> None:
        if not model or not model.get("patterns"):
            return

        ocr_text = self.ocr_text_view.toPlainText() or ""
        if not ocr_text.strip():
            return

        try:
            from ocr.invoice_parser import parse_vat_lines
            rows = parse_vat_lines(ocr_text, model=model) or []
        except Exception:
            return

        existing_vat = {
            (
                str(row.get("rate") or "").strip(),
                str(row.get("base") or "").strip(),
                str(row.get("vat") or "").strip(),
            )
            for row in (self.get_vat_rows() if hasattr(self, "get_vat_rows") else [])
        }

        changed = False
        for row in rows:
            candidate = (
                str(row.get("rate") or "").strip(),
                str(row.get("base") or "").strip(),
                str(row.get("vat") or "").strip(),
            )
            if not any(candidate) or candidate in existing_vat:
                continue
            if not self._fill_first_empty_vat_row(*candidate):
                self._add_vat_row(*candidate)
            existing_vat.add(candidate)
            changed = True

        if changed:
            self._ensure_empty_vat_row()
            self.update_vat_total()

    def _apply_supplier_model_for_current_context(self) -> dict | None:
        model, ctx = self._load_supplier_model_for_current_context(allow_legacy=True)
        if not model:
            return None

        try:
            self.apply_supplier_model(model)
            self._merge_vat_lines_from_supplier_model(model)
        except Exception:
            pass

        # Si le modèle a été trouvé grâce au dossier, on mémorise aussi le KundenNr
        # côté écran pour les sauvegardes JSON et les contrôles suivants.
        try:
            if ctx.get("kundennr"):
                self.selected_kundennr = str(ctx.get("kundennr") or "").strip() or None
                self.transporter_selected_mode = bool(self.selected_kundennr)
        except Exception:
            pass

        return model

    def on_analyze_pdf_clicked(self, checked: bool = False):
        if self._warn_if_invoice_validated_locked("relancer l'OCR"):
            return
        if self._confirm_relaunch_ocr_data("Analyser le PDF (OCR)"):
            self.analyze_pdf(checked=checked)

    def on_analyze_pdf_deep_clicked(self, checked: bool = False):
        if self._warn_if_invoice_validated_locked("relancer l'OCR profond"):
            return
        if self._confirm_relaunch_ocr_data("OCR profond"):
            self.analyze_pdf_deep(checked=checked)

    def _get_active_document_path(self, preferred_path: str | None = None) -> str | None:
        for candidate in (preferred_path, getattr(self, "view_pdf_path", None), getattr(self, "current_pdf_path", None)):
            candidate = str(candidate or "").strip()
            if candidate:
                return candidate
        return None

    def _field_position_norm_value(self, value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())

    def _field_key_for_widget(self, field) -> str:
        try:
            if field == self.iban_input:
                return "iban"
            if field == self.bic_input:
                return "bic"
            if field == self.date_input:
                return "invoice_date"
            if field == self.invoice_number_input:
                return "invoice_number"
        except Exception:
            pass
        return ""

    def _get_current_field_values_for_positions(self) -> dict[str, str]:
        return {
            "iban": (self.iban_input.text() or "").strip(),
            "bic": (self.bic_input.text() or "").strip(),
            "invoice_date": (self.date_input.text() or "").strip(),
            "invoice_number": (self.invoice_number_input.text() or "").strip(),
        }

    def _normalize_field_positions(self, positions: dict | None) -> dict:
        """Normalise le bloc JSON optionnel field_positions.

        Rétrocompatibilité : les anciens JSON n'ont pas cette clé, ou peuvent
        contenir des valeurs incomplètes. Dans ce cas on ignore simplement la
        position au lieu de bloquer l'ouverture de la facture.
        """
        if not isinstance(positions, dict):
            return {}

        out = {}
        for key, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            try:
                x = float(pos.get("x", pos.get("left", pos.get("x0", 0))) or 0)
                y = float(pos.get("y", pos.get("top", pos.get("y0", 0))) or 0)
                w = float(pos.get("w", pos.get("width", 0)) or 0)
                h = float(pos.get("h", pos.get("height", 0)) or 0)

                if (w <= 0 or h <= 0) and {"x0", "y0", "x1", "y1"}.issubset(pos.keys()):
                    x0 = float(pos.get("x0") or 0)
                    y0 = float(pos.get("y0") or 0)
                    x1 = float(pos.get("x1") or 0)
                    y1 = float(pos.get("y1") or 0)
                    x, y, w, h = x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)

                if w <= 0 or h <= 0:
                    continue

                page = pos.get("page", pos.get("page_index", 0))
                try:
                    page = int(page or 0)
                except Exception:
                    page = 0
                if page >= 1 and bool(pos.get("page_number")):
                    page -= 1

                norm = dict(pos)
                norm.update({
                    "page": max(0, page),
                    "x": max(0.0, min(1.0, x)),
                    "y": max(0.0, min(1.0, y)),
                    "w": max(0.0, min(1.0, w)),
                    "h": max(0.0, min(1.0, h)),
                })
                out[str(key)] = norm
            except Exception:
                continue
        return out

    def _update_field_positions_from_document(self, document_path: str | None = None, *, allow_tesseract_fallback: bool = False) -> dict:
        """Calcule les rectangles des champs principaux et les garde en mémoire.

        Le résultat est sauvegardé dans le JSON sous `field_positions`. Les
        anciens JSON restent compatibles : si rien n'est trouvé, le champ est
        simplement absent et l'interface n'affiche pas de rectangle.
        """
        doc_path = str(document_path or self._get_active_document_path() or "").strip()
        if not doc_path:
            return getattr(self, "field_positions", {}) or {}

        values = self._get_current_field_values_for_positions()
        try:
            found = find_field_positions(
                doc_path,
                values,
                allow_tesseract_fallback=allow_tesseract_fallback,
            ) or {}
        except Exception:
            found = {}

        current = self._normalize_field_positions(getattr(self, "field_positions", {}) or {})
        for key, pos in self._normalize_field_positions(found).items():
            pos = dict(pos)
            pos["document"] = os.path.basename(doc_path)
            current[key] = pos

        self.field_positions = current
        try:
            self.pdf_viewer.set_highlights(current)
        except Exception:
            pass
        return current

    def _highlight_pdf_field_for_widget(self, field) -> None:
        key = self._field_key_for_widget(field)
        if not key:
            try:
                self.pdf_viewer.highlight_field(None, None)
            except Exception:
                pass
            return

        positions = self._normalize_field_positions(getattr(self, "field_positions", {}) or {})
        pos = positions.get(key)
        if not pos:
            try:
                self.pdf_viewer.highlight_field(None, None)
            except Exception:
                pass
            return

        # Si la valeur affichée a changé depuis la dernière OCRisation, on évite
        # d'encadrer une ancienne occurrence qui ne correspond plus au champ.
        current_value = (self._get_current_field_values_for_positions().get(key) or "").strip()
        saved_value = str(pos.get("value") or "").strip()
        if saved_value and current_value and self._field_position_norm_value(saved_value) != self._field_position_norm_value(current_value):
            try:
                self.pdf_viewer.highlight_field(None, None)
            except Exception:
                pass
            return

        # Si la position a été calculée sur une autre pièce du même entry_id,
        # on bascule d'abord l'affichage sur ce document.
        doc_name = str(pos.get("document") or "").strip()
        if doc_name and hasattr(self, "entry_pdf_paths"):
            try:
                current_name = os.path.basename(str(getattr(self, "view_pdf_path", "") or ""))
                if current_name != doc_name:
                    for i, path in enumerate(self.entry_pdf_paths or []):
                        if os.path.basename(str(path or "")) == doc_name:
                            self.show_doc_by_index(i)
                            break
            except Exception:
                pass

        try:
            self.pdf_viewer.highlight_field(key, pos)
            source = str(pos.get("source") or "").strip()
            page = int(pos.get("page", 0) or 0) + 1
            self.statusBar().showMessage(f"Champ localisé sur le document (page {page}{', ' + source if source else ''}).", 2500)
        except Exception:
            pass

    def analyze_pdf(self, checked: bool = False, show_message: bool = False, document_path: str | None = None, auto_save: bool = True):

        active_doc_path = self._get_active_document_path(document_path)
        if not active_doc_path:
            QMessageBox.warning(self, "Analyse OCR", "Aucun document sélectionné.")
            return

        if self._warn_if_invoice_validated_locked("relancer l'OCR", pdf_path=active_doc_path):
            return

        if not is_ocr_allowed_document(active_doc_path):
            QMessageBox.information(
                self,
                "Analyse OCR",
                "Ce document est une image. Il peut être affiché dans l'application, mais il n'est pas OCRisé."
            )
            return
        try:
            # 1) prélecture rapide : première page uniquement
            preview_text = extract_text_from_pdf(active_doc_path, max_pages=1)
            doc_type = classify_document_text(preview_text)

            # 2) si CMR / document logistique -> on stoppe avant OCR complet
            if doc_type == "cmr":
                self.ocr_text_view.setPlainText(preview_text)
                return

            # 3) sinon OCR complet normal
            text = extract_text_from_pdf(active_doc_path)
            self.ocr_text_view.setPlainText(text)

            data = parse_invoice(text)

            self.fill_fields(data)

            detected_fields = detect_fields_multilingual(text)
            if detected_fields:
                self._merge_detected_fields_without_overwrite(detected_fields)

            self.autofill_folder_amounts_from_ocr(text)


            self.update_folder_totals()
            self.check_bank_information()
            self.load_transporter_information(force_by_kundennr=False)
            self._apply_supplier_model_for_current_context()

            self.highlight_missing_fields()
            ocr_text = self.ocr_text_view.toPlainText() or ""
            # Ici on laisse l'OCR choisir réellement l'IBAN/BIC du document.
            # Ne pas booster la valeur déjà présente : elle peut venir d'un ancien
            # modèle transporteur KundenNr et empêcherait la bonne valeur OCR de remonter.
            best = extract_best_bank_ids(ocr_text)

            current_iban = self.iban_input.text().strip()
            current_bic = self.bic_input.text().strip()
            bic_scores = dict(best.get("bic_candidates") or [])
            best_bic_score = int(bic_scores.get(best.get("bic") or "", 0) or 0)
            current_bic_score = int(bic_scores.get(current_bic.replace(" ", "").upper(), 0) or 0)
            if best["iban"] and best["iban"] != current_iban.replace(" ", "").replace("-", "").upper():
                self.iban_input.setText(best["iban"])
            if best["bic"] and (
                not current_bic
                or not validate_bic(current_bic)
                or (best["bic"] != current_bic.replace(" ", "").upper() and best_bic_score >= current_bic_score)
            ):
                self.bic_input.setText(best["bic"])

            self.check_bank_information()
            self.enable_transporter_update()
            self._apply_supplier_model_for_current_context()
            self._update_field_positions_from_document(active_doc_path, allow_tesseract_fallback=False)

            # ✅ Si aucun IBAN ou BIC valide n'est trouvé, lancer OCR profond automatiquement
            final_iban = self.iban_input.text().strip()
            final_bic = self.bic_input.text().strip()
            if not final_iban or not validate_iban(final_iban) or not final_bic or not validate_bic(final_bic):
                self.statusBar().showMessage("OCR normal terminé, lancement OCR profond...", 2000)
                QApplication.processEvents()
                self.analyze_pdf_deep(document_path=document_path, auto_save=False)

            if auto_save:
                self._auto_save_after_ocr(pdf_path=active_doc_path)
            else:
                self.statusBar().showMessage("OCR terminé.", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Erreur OCR", str(e))
        if show_message:
            QMessageBox.information(...)

    def _get_existing_status_for_current_pdf(self, default: str = "pending", pdf_path: str | None = None) -> str:
        status = str(default or "pending").strip().lower()

        try:
            pdf_path = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()
            if pdf_path:
                data = self._read_saved_invoice_json(pdf_path) or {}
                json_status = str(data.get("status") or "").strip().lower()
                if json_status in {"pending", "validated", "error", "ecart"}:
                    return json_status
        except Exception:
            pass

        try:
            entry_id = str(self._resolve_current_entry_id() or "").strip()
            if entry_id:
                sql_status = str(self.logmail_repo.get_processing_status_for_entry(entry_id) or "").strip().lower()
                if sql_status in {"pending", "validated", "error", "ecart"}:
                    return sql_status
        except Exception:
            pass

        return status if status in {"pending", "validated", "error", "ecart"} else "pending"

    def _parse_invoice_date_strict(self, value: str):
        """Retourne (date_obj, date_normalisee) si la date facture est réellement valide.

        Contrairement à normalize_date_format(), cette méthode refuse les dates
        impossibles comme 31/02/2026 ou 4705.53.77. Elle accepte les formats
        courants utilisés par les OCR : jj/mm/aaaa, jj.mm.aaaa, jj-mm-aaaa,
        aaaa-mm-jj et les années sur 2 chiffres.
        """
        raw = str(value or "").strip()
        if not raw:
            return None, ""

        m = re.search(r"\b(\d{1,4})[./\-](\d{1,2})[./\-](\d{1,4})\b", raw)
        if not m:
            return None, ""

        try:
            p1, p2, p3 = [int(x) for x in m.groups()]
        except Exception:
            return None, ""

        # aaaa-mm-jj / aaaa.mm.jj
        if len(m.group(1)) == 4 or p1 > 31:
            year, month, day = p1, p2, p3
        else:
            day, month, year = p1, p2, p3

        if year < 100:
            # Pivot classique : 00-69 => 2000-2069, 70-99 => 1970-1999.
            year = 2000 + year if year <= 69 else 1900 + year

        if not (1900 <= year <= 2100):
            return None, ""

        try:
            dt = datetime(year, month, day)
        except Exception:
            return None, ""

        return dt.date(), f"{dt.day:02d}/{dt.month:02d}/{dt.year:04d}"

    def _validate_invoice_date_for_validation(self) -> bool:
        raw = str(self.date_input.text() or "").strip()
        _date_obj, normalized = self._parse_invoice_date_strict(raw)
        if not normalized:
            QMessageBox.warning(
                self,
                "Validation impossible",
                "Le champ 'Date facture' doit contenir une date valide pour valider la facture.\n\n"
                "Formats acceptés : JJ/MM/AAAA, JJ.MM.AAAA, JJ-MM-AAAA ou AAAA-MM-JJ."
            )
            try:
                self.date_input.setStyleSheet("background-color: #ffe6e6;")
                self.date_input.setFocus()
                self.date_input.selectAll()
            except Exception:
                pass
            return False

        if raw != normalized:
            try:
                self.date_input.setText(normalized)
            except Exception:
                pass
        try:
            if not getattr(self, "_invoice_validated_locked", False):
                self.date_input.setStyleSheet("")
        except Exception:
            pass
        return True

    def _get_effective_invoice_status(self, pdf_path: str | None = None, entry_id: str | None = None, default: str = "pending") -> str:
        """Statut réel d'une facture, avec priorité à la BDD.

        Pour verrouiller une facture validée, la BDD doit primer sur un JSON
        éventuellement ancien ou localement modifié.
        """
        allowed = {"pending", "validated", "error", "ecart", "eccarts", "draft"}
        status = str(default or "pending").strip().lower()

        try:
            entry_id = str(entry_id or "").strip()
            if not entry_id:
                target_pdf = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()
                if target_pdf:
                    entry_id = str(self._resolve_current_entry_id(target_pdf) or "").strip()
                else:
                    entry_id = str(self._resolve_current_entry_id() or "").strip()
            if entry_id:
                sql_status = str(self.logmail_repo.get_processing_status_for_entry(entry_id) or "").strip().lower()
                if sql_status == "eccarts":
                    sql_status = "ecart"
                if sql_status in allowed:
                    return sql_status
        except Exception:
            pass

        try:
            target_pdf = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()
            if target_pdf:
                data = self._read_saved_invoice_json(target_pdf) or {}
                json_status = str(data.get("status") or "").strip().lower()
                if json_status == "eccarts":
                    json_status = "ecart"
                if json_status in allowed:
                    return json_status
        except Exception:
            pass

        if status == "eccarts":
            status = "ecart"
        return status if status in allowed else "pending"

    def _is_invoice_already_validated(self, pdf_path: str | None = None, entry_id: str | None = None) -> bool:
        return self._get_effective_invoice_status(pdf_path=pdf_path, entry_id=entry_id, default="pending") == "validated"

    def _warn_if_invoice_validated_locked(self, action: str = "modifier", pdf_path: str | None = None) -> bool:
        if not self._is_invoice_already_validated(pdf_path=pdf_path):
            return False

        QMessageBox.warning(
            self,
            "Facture déjà validée",
            "Cette facture est déjà validée et ne peut plus être modifiée.\n\n"
            f"Action refusée : {action}."
        )
        try:
            self.statusBar().showMessage("Facture déjà validée : modification refusée.", 5000)
        except Exception:
            pass
        return True

    def _set_line_edit_locked_style(self, field, locked: bool):
        if field is None:
            return
        try:
            field.setReadOnly(bool(locked))
            if locked:
                field.setStyleSheet("background-color: #f3f3f3;")
                field.setToolTip("Facture déjà validée : champ non modifiable.")
            else:
                field.setToolTip("")
        except Exception:
            pass

    def _apply_invoice_validated_lock(self, locked: bool):
        """Verrouille/déverrouille l'édition des champs d'une facture validée.

        Le verrou visuel complète le blocage de sauvegarde : même si un ancien
        flux tente quand même de sauver, save_current_data refuse la modification.
        """
        self._invoice_validated_locked = bool(locked)

        for field_name in ("iban_input", "bic_input", "date_input", "invoice_number_input"):
            self._set_line_edit_locked_style(getattr(self, field_name, None), locked)

        try:
            if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
                self.invoice_comment_input.setReadOnly(bool(locked))
                self.invoice_comment_input.setToolTip("Facture déjà validée : commentaire non modifiable." if locked else "")
        except Exception:
            pass

        try:
            if hasattr(self, "folder_table") and self.folder_table is not None:
                for r in range(self.folder_table.rowCount()):
                    dossier_le, amount_le, vat_theo_le = self._get_row_widgets(r)
                    self._set_line_edit_locked_style(dossier_le, locked)
                    self._set_line_edit_locked_style(amount_le, locked)
                    # La colonne TVA théorique reste toujours en lecture seule.
                    if vat_theo_le is not None:
                        vat_theo_le.setReadOnly(True)
        except Exception:
            pass

        try:
            if hasattr(self, "vat_table") and self.vat_table is not None:
                for r in range(self.vat_table.rowCount()):
                    for w in self._get_vat_row_widgets(r):
                        self._set_line_edit_locked_style(w, locked)
        except Exception:
            pass

        try:
            if hasattr(self, "btn_analyze_pdf"):
                self.btn_analyze_pdf.setToolTip("Facture déjà validée : OCR non autorisé." if locked else "")
            if hasattr(self, "btn_deep_ocr"):
                self.btn_deep_ocr.setToolTip("Facture déjà validée : OCR profond non autorisé." if locked else "")
        except Exception:
            pass

    def _auto_save_after_ocr(self, pdf_path: str | None = None) -> bool:
        target_pdf_path = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()
        if not target_pdf_path:
            return False

        status_to_keep = self._get_existing_status_for_current_pdf(default="pending", pdf_path=target_pdf_path)
        ok = self.save_current_data(status=status_to_keep, show_message=False, pdf_path=target_pdf_path)

        if ok:
            try:
                if hasattr(self, "_set_invoice_source_document"):
                    self._set_invoice_source_document(target_pdf_path)
            except Exception:
                pass
            try:
                self._set_left_row_status(target_pdf_path, status_to_keep)
            except Exception:
                pass
            self.statusBar().showMessage("OCR terminé et sauvegardé.", 3000)
        else:
            self.statusBar().showMessage("OCR terminé, mais la sauvegarde a échoué.", 5000)

        return ok

    def _build_progress_dialog(self, title: str, label: str, maximum: int) -> QProgressDialog:
        dlg = QProgressDialog(label, "Annuler", 0, max(0, int(maximum)), self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        return dlg
    def analyze_pdf_deep(self, checked: bool = False, document_path: str | None = None, auto_save: bool = True):
        active_doc_path = self._get_active_document_path(document_path)
        if not active_doc_path:
            QMessageBox.warning(self, "OCR profond", "Aucun document sélectionné.")
            return

        if self._warn_if_invoice_validated_locked("relancer l'OCR profond", pdf_path=active_doc_path):
            return

        progress = None
        try:
            progress = self._build_progress_dialog("OCR profond", "Préparation OCR profond…", 1)
            progress.show()
            QApplication.processEvents()

            def _on_progress(current: int, total: int, label: str):
                if progress is None:
                    return
                total = max(1, int(total or 1))
                current = max(0, min(int(current or 0), total))
                if progress.maximum() != total:
                    progress.setMaximum(total)
                progress.setLabelText(label)
                progress.setValue(current)
                QApplication.processEvents()
                if progress.wasCanceled():
                    raise _DownloadCanceled()

            deep_text = (extract_text_with_tesseract(active_doc_path, progress_callback=_on_progress) or "").strip()
            if progress is not None:
                progress.setValue(progress.maximum())
                QApplication.processEvents()

            if not deep_text:
                QMessageBox.information(self, "OCR profond", "Aucun texte exploitable n'a été trouvé par Tesseract.")
                return

            current_text = (self.ocr_text_view.toPlainText() or "").strip()
            if current_text:
                norm_current = re.sub(r"\s+", " ", current_text).strip()
                norm_deep = re.sub(r"\s+", " ", deep_text).strip()
                if norm_deep and norm_deep not in norm_current:
                    merged_text = current_text + "\n\n===== OCR PROFOND (TESSERACT) =====\n" + deep_text
                else:
                    merged_text = current_text
            else:
                merged_text = deep_text

            self.ocr_text_view.setPlainText(merged_text)

            data = parse_invoice(merged_text)
            self._merge_invoice_data_without_overwrite(data)

            detected_fields = detect_fields_multilingual(merged_text)
            if detected_fields:
                self._merge_detected_fields_without_overwrite(detected_fields)

            self.autofill_folder_amounts_from_ocr(merged_text)
            self.update_folder_totals()
            self.check_bank_information()
            self.load_transporter_information(force_by_kundennr=False)

            # OCR profond : même règle, la valeur trouvée dans le document prime
            # sur une ancienne valeur déjà présente à l'écran.
            best = extract_best_bank_ids(merged_text)
            current_iban = self.iban_input.text().strip()
            current_bic = self.bic_input.text().strip()
            bic_scores = dict(best.get("bic_candidates") or [])
            best_bic_score = int(bic_scores.get(best.get("bic") or "", 0) or 0)
            current_bic_score = int(bic_scores.get(current_bic.replace(" ", "").upper(), 0) or 0)
            if best["iban"] and best["iban"] != current_iban.replace(" ", "").replace("-", "").upper():
                self.iban_input.setText(best["iban"])
            if best["bic"] and (
                not current_bic
                or not validate_bic(current_bic)
                or (best["bic"] != current_bic.replace(" ", "").upper() and best_bic_score >= current_bic_score)
            ):
                self.bic_input.setText(best["bic"])

            self.check_bank_information()
            self.enable_transporter_update()
            self._apply_supplier_model_for_current_context()
            self._update_field_positions_from_document(active_doc_path, allow_tesseract_fallback=True)

            self.highlight_missing_fields()
            if auto_save:
                self._auto_save_after_ocr(pdf_path=active_doc_path)
            else:
                self.statusBar().showMessage("OCR profond terminé.", 4000)
        except _DownloadCanceled:
            self.statusBar().showMessage("OCR profond annulé.", 4000)
        except pytesseract.TesseractNotFoundError:
            QMessageBox.critical(
                self,
                "OCR profond",
                "Tesseract est introuvable. Vérifie le chemin 'tesseract_path' dans settings/app_settings.json.",
            )
        except Exception as e:
            QMessageBox.critical(self, "OCR profond", str(e))
        finally:
            if progress is not None:
                progress.close()

    def _merge_invoice_data_without_overwrite(self, data):
        current_iban = self.iban_input.text().strip()
        current_bic = self.bic_input.text().strip()

        if getattr(data, "iban", "") and (not current_iban or not validate_iban(current_iban)):
            if validate_iban(str(data.iban or "")):
                self.iban_input.setText(data.iban)

        if getattr(data, "bic", "") and (not current_bic or not validate_bic(current_bic)):
            if validate_bic(str(data.bic or "")):
                self.bic_input.setText(data.bic)

        if getattr(data, "invoice_date", "") and not self.date_input.text().strip():
            self.date_input.setText(normalize_date_format(data.invoice_date))

        current_invoice_number = (self.invoice_number_input.text() or "").strip()
        if getattr(data, "invoice_number", "") and not current_invoice_number:
            self.invoice_number_input.setText(data.invoice_number)

        existing_folders = {
            str(row.get("tour_nr") or "").strip()
            for row in (self.get_folder_rows() if hasattr(self, "get_folder_rows") else [])
            if str(row.get("tour_nr") or "").strip()
        }
        for folder_number in list(getattr(data, "folder_numbers", None) or []):
            folder_number = str(folder_number or "").strip()
            if not folder_number or folder_number in existing_folders:
                continue
            if not self._fill_first_empty_folder_row(folder_number):
                self._add_folder_row(folder_number, "")
            existing_folders.add(folder_number)
        self._ensure_empty_folder_row()

        existing_vat = {
            (
                str(row.get("rate") or "").strip(),
                str(row.get("base") or "").strip(),
                str(row.get("vat") or "").strip(),
            )
            for row in (self.get_vat_rows() if hasattr(self, "get_vat_rows") else [])
        }
        for vat_row in list(getattr(data, "vat_lines", None) or []):
            candidate = (
                str(vat_row.get("rate") or "").strip(),
                str(vat_row.get("base") or "").strip(),
                str(vat_row.get("vat") or "").strip(),
            )
            if candidate in existing_vat or not any(candidate):
                continue
            if not self._fill_first_empty_vat_row(*candidate):
                self._add_vat_row(*candidate)
            existing_vat.add(candidate)
        self._ensure_empty_vat_row()
        self.update_vat_total()

    def _fill_first_empty_folder_row(self, dossier: str) -> bool:
        for row in range(self.folder_table.rowCount()):
            dossier_le, amount_le, _ = self._get_row_widgets(row)
            dossier_txt = (dossier_le.text() if dossier_le else "").strip()
            amount_txt = (amount_le.text() if amount_le else "").strip()
            if dossier_le and not dossier_txt and not amount_txt:
                dossier_le.setText(dossier)
                return True
        return False

    def _fill_first_empty_vat_row(self, rate: str, base: str, vat: str) -> bool:
        for row in range(self.vat_table.rowCount()):
            rate_le, base_le, vat_le = self._get_vat_row_widgets(row)
            rate_txt = (rate_le.text() if rate_le else "").strip()
            base_txt = (base_le.text() if base_le else "").strip()
            vat_txt = (vat_le.text() if vat_le else "").strip()
            if rate_le and base_le and vat_le and not rate_txt and not base_txt and not vat_txt:
                rate_le.setText(rate)
                base_le.setText(base)
                vat_le.setText(vat)
                return True
        return False

    def _merge_detected_fields_without_overwrite(self, detected: dict):
        if not isinstance(detected, dict) or not detected:
            return

        iban = str(detected.get("iban") or "").replace(" ", "").upper().strip()
        current_iban = self.iban_input.text().strip()
        if iban and validate_iban(iban) and (not current_iban or not validate_iban(current_iban)):
            self.iban_input.setText(iban)

        bic = str(detected.get("bic") or "").replace(" ", "").upper().strip()
        current_bic = self.bic_input.text().strip()
        if bic and validate_bic(bic) and (not current_bic or not validate_bic(current_bic)):
            self.bic_input.setText(bic)

        date_value = str(detected.get("date") or "").strip()
        if date_value and not self.date_input.text().strip():
            self.date_input.setText(normalize_date_format(date_value))

        invoice_number = str(detected.get("invoice_number") or "").strip()
        if invoice_number and not self.invoice_number_input.text().strip():
            self.invoice_number_input.setText(invoice_number)

        folder_number = str(detected.get("folder_number") or "").strip()
        if folder_number:
            existing_folders = {
                str(row.get("tour_nr") or "").strip()
                for row in (self.get_folder_rows() if hasattr(self, "get_folder_rows") else [])
                if str(row.get("tour_nr") or "").strip()
            }
            if folder_number not in existing_folders:
                if not self._fill_first_empty_folder_row(folder_number):
                    self._add_folder_row(folder_number, "")
                self._ensure_empty_folder_row()


    def fill_fields(self, data):
        # Champs simples
        self.iban_input.setText(data.iban or "")
        self.bic_input.setText(data.bic or "")
        self.date_input.setText(normalize_date_format(data.invoice_date or ""))
        self.invoice_number_input.setText(data.invoice_number or "")

        # dossiers
        self.folder_table.setRowCount(0)

        folder_numbers = getattr(data, "folder_numbers", None)
        if folder_numbers:
            for n in folder_numbers:
                if n:
                    self._add_folder_row(str(n), "")
        else:
            if getattr(data, "folder_number", None):
                self._add_folder_row(str(data.folder_number), "")
        # ligne vide permanente
        self._ensure_empty_folder_row()
        self.update_folder_totals()
        # --- TVA ---
        self.vat_table.setRowCount(0)

        vat_lines = getattr(data, "vat_lines", None) or []
        for r in vat_lines:
            self._add_vat_row(r.get("rate", ""), r.get("base", ""), r.get("vat", ""))

        self._ensure_empty_vat_row()
        self.update_vat_total()

        # Totaux / couleurs (si tu as ces fonctions)
        if hasattr(self, "update_folder_totals"):
            self.update_folder_totals()

    def highlight_missing_fields(self):
        fields = [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]
        for field in fields:
            if field in (self.iban_input, self.bic_input):
                mismatch_fields = getattr(self, "_bank_transporter_mismatch_fields", set()) or set()
                if self.bank_valid is not None:
                    continue
                if field == self.iban_input and "iban" in mismatch_fields:
                    continue
                if field == self.bic_input and "bic" in mismatch_fields:
                    continue
            field.setStyleSheet("background-color: #ffe6e6;" if not field.text().strip() else "background-color: #e6ffe6;")

        rows = self.get_folder_rows()
        has_any = any(r.get("tour_nr") for r in rows)

        for r in range(self.folder_table.rowCount()):
            dossier_le, _, vat_theo_le = self._get_row_widgets(r)
            if not dossier_le:
                continue
            if has_any:
                # vert seulement si rempli
                dossier_le.setStyleSheet("background-color: #e6ffe6;" if dossier_le.text().strip() else "")
            else:
                # si aucun dossier saisi, on met la première ligne en rouge
                dossier_le.setStyleSheet("background-color: #ffe6e6;" if r == 0 else "")

    def clear_fields(self):
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.clear()
            field.setStyleSheet("")
        self.clear_folder_fields()
        if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
            self.invoice_comment_input.setPlainText("")
        self.selected_kundennr = None
        self.transporter_selected_mode = False
        self._last_transporter_source_tour_nr = None
        if hasattr(self, "_set_transporter_input_locked"):
            self._set_transporter_input_locked("")
        else:
            self.transporter_input.clear()
        if hasattr(self, "_set_transporter_aux_locked"):
            self._set_transporter_aux_locked(True, "")

    def append_ocr_text(self, text: str):
        if not text.strip():
            return
        current = self.ocr_text_view.toPlainText()
        self.ocr_text_view.setPlainText(current + "\n\n--- OCR sélection ---\n" + text)

    def assign_text_to_field(self, text: str, field_key: str):
        text = text.strip()

        if field_key == "invoice_number":
            cleaned = re.sub(r"[^A-Z0-9\-_/\. ]", "", text.upper()).strip()
            self.invoice_number_input.setText(cleaned)
            self.invoice_number_input.setStyleSheet("background-color: #e6ffe6;")
            return

        if field_key == "folder_number":
            m = re.search(self.DOSSIER_PATTERN, text)
            dossier = m.group(0) if m else ""

            # remplir la première ligne dont la colonne dossier est vide (en évitant la ligne vide du bas si elle existe)
            for r in range(self.folder_table.rowCount()):
                dossier_le, _ , vat_theo_le= self._get_row_widgets(r)
                if dossier_le and not dossier_le.text().strip():
                    dossier_le.setText(dossier)
                    dossier_le.setStyleSheet("background-color: #e6ffe6;")
                    self._ensure_empty_folder_row()
                    return

            # sinon on force une nouvelle ligne (avant/avec la ligne vide)
            self._add_folder_row(dossier, "")
            self._ensure_empty_folder_row()
            return

        if field_key == "iban":
            self.iban_input.setText(text.replace(" ", "").upper())
            self.iban_input.setStyleSheet("background-color: #e6ffe6;")
            QTimer.singleShot(0, self._refresh_transporter_after_bank_autofill)
            return

        if field_key == "bic":
            self.bic_input.setText(text.replace(" ", "").upper())
            self.bic_input.setStyleSheet("background-color: #e6ffe6;")
            QTimer.singleShot(0, self._refresh_transporter_after_bank_autofill)
            return

        if field_key == "date":
            normalized_date = normalize_date_format(text)
            self.date_input.setText(normalized_date)
            self.date_input.setStyleSheet("background-color: #e6ffe6;")
            return

    def search_in_ocr_text(self, query: str):
        editor = self.ocr_text_view
        self.search_selections = []
        self.current_match_index = -1
        editor.setExtraSelections([])

        if not query.strip():
            self.search_counter_label.setText("0 / 0")
            return

        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.Start)

        while True:
            cursor = editor.document().find(query, cursor)
            if cursor.isNull():
                break

            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor

            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#fff59d"))
            sel.format = fmt
            self.search_selections.append(sel)

        if not self.search_selections:
            self.search_counter_label.setText("0 / 0")
            return

        self.current_match_index = 0
        self._update_active_match()
        self.search_counter_label.setText(f"1 / {len(self.search_selections)}")

    def goto_next_match(self):
        if not self.search_selections:
            return
        self.current_match_index = (self.current_match_index + 1) % len(self.search_selections)
        self._update_active_match()

    def goto_previous_match(self):
        if not self.search_selections:
            return
        self.current_match_index = (self.current_match_index - 1) % len(self.search_selections)
        self._update_active_match()

    def _update_active_match(self):
        editor = self.ocr_text_view
        updated = []

        for i, sel in enumerate(self.search_selections):
            fmt = QTextCharFormat()
            if i == self.current_match_index:
                fmt.setBackground(QColor("#ffcc80"))
                editor.setTextCursor(sel.cursor)
            else:
                fmt.setBackground(QColor("#fff59d"))

            sel.format = fmt
            updated.append(sel)

        editor.setExtraSelections(updated)
        self.search_counter_label.setText(f"{self.current_match_index + 1} / {len(self.search_selections)}")



    def _score_search_index_json_data(self, data: dict) -> int:
        """Score simple pour choisir le JSON le plus riche lors d'une reconstruction d'index."""
        if not isinstance(data, dict):
            return 0
        score = 0
        for key in (
            "invoice_number",
            "invoice_date",
            "iban",
            "bic",
            "transporter_kundennr",
            "selected_kundennr",
            "transporter_name",
            "transporter_text",
            "ocr_text",
        ):
            if str(data.get(key) or "").strip():
                score += 1
        try:
            score += min(5, len(self._extract_tournrs_from_saved(data)))
        except Exception:
            pass
        try:
            score += min(3, len(data.get("folders") or []))
        except Exception:
            pass
        return score

    def _build_saved_json_lookup_for_search_index(self) -> dict:
        """Prépare un index mémoire des JSON de sauvegarde existants.

        Compatibilité : gère les anciens noms `<entry_id>__fichier.json`, les
        nouveaux noms `<fichier>___suffixe.json`, et les JSON qui portent eux-mêmes
        `entry_id` dans leur contenu.
        """
        lookup = {"by_entry": {}, "by_stem": {}}
        model_dir = str(MODELS_DIR or "").strip()
        if not model_dir or not os.path.isdir(model_dir):
            return lookup

        try:
            filenames = [f for f in os.listdir(model_dir) if str(f).lower().endswith(".json")]
        except Exception:
            return lookup

        for filename in filenames:
            path = os.path.join(model_dir, filename)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            stem = os.path.splitext(filename)[0]
            entry_id = str(data.get("entry_id") or "").strip()
            if not entry_id and ENTRY_FILE_SEPARATOR in stem:
                candidate = stem.split(ENTRY_FILE_SEPARATOR, 1)[0]
                # Un entry_id Outlook est long ; on évite de prendre un simple
                # nom de fichier contenant "__" par hasard.
                if len(candidate) >= 20:
                    entry_id = candidate

            item = {"path": path, "stem": stem, "entry_id": entry_id, "data": data, "score": self._score_search_index_json_data(data)}
            lookup["by_stem"].setdefault(stem.upper(), []).append(item)
            if ENTRY_FILE_SEPARATOR in stem:
                lookup["by_stem"].setdefault(stem.split(ENTRY_FILE_SEPARATOR, 1)[1].upper(), []).append(item)

            if entry_id:
                lookup["by_entry"].setdefault(entry_id, []).append(item)

        return lookup

    def _find_saved_json_for_search_index(self, entry_id: str, nom_pdf: str, lookup: dict) -> dict:
        """Retourne le meilleur JSON disponible pour un entry_id/nom_pdf."""
        entry_id = str(entry_id or "").strip()
        nom_pdf = os.path.basename(str(nom_pdf or "").strip())
        stem = os.path.splitext(nom_pdf)[0]
        candidates = []

        if stem:
            candidates.append(stem.upper())
        if entry_id and stem:
            candidates.append(f"{entry_id}{ENTRY_FILE_SEPARATOR}{stem}".upper())
        if ENTRY_FILE_SEPARATOR in stem:
            candidates.append(stem.split(ENTRY_FILE_SEPARATOR, 1)[1].upper())

        by_stem = (lookup or {}).get("by_stem") or {}
        for key in candidates:
            items = by_stem.get(key) or []
            if not items:
                continue
            exact_items = [
                item for item in items
                if str(item.get("entry_id") or "").strip() == entry_id
                or str(item.get("stem") or "").upper().startswith(f"{entry_id}{ENTRY_FILE_SEPARATOR}".upper())
            ]
            pool = exact_items or items
            best = max(pool, key=lambda x: int(x.get("score") or 0))
            return best.get("data") or {}

        by_entry = (lookup or {}).get("by_entry") or {}
        entry_items = by_entry.get(entry_id) or []
        if entry_items:
            best = max(entry_items, key=lambda x: int(x.get("score") or 0))
            return best.get("data") or {}

        return {}

    def _transporter_name_for_search_index(self, kundennr: str, fallback: str = "", cache: dict | None = None) -> str:
        """Nom transporteur pour l'index, avec cache pour éviter trop d'appels SQL."""
        kundennr = str(kundennr or "").strip()
        fallback = str(fallback or "").strip()
        if fallback and kundennr and fallback.endswith(f"({kundennr})"):
            fallback = fallback[: -len(f"({kundennr})")].strip()
        if fallback:
            return fallback
        if not kundennr:
            return ""
        if cache is not None and kundennr in cache:
            return cache[kundennr]
        name = ""
        try:
            transporter = self.transporter_repo.find_transporter_by_kundennr(kundennr) or {}
            name = str(transporter.get("name1") or "").strip()
        except Exception:
            name = ""
        if cache is not None:
            cache[kundennr] = name
        return name

    def on_rebuild_search_index_clicked(self):
        """Bouton admin : reconstruit tout XXA_OCR_SEARCH_INDEX depuis le début."""
        reply = QMessageBox.question(
            self,
            "Recréer l'index recherche",
            "Cette opération va vider puis reconstruire entièrement la table XXA_OCR_SEARCH_INDEX\n"
            "à partir de XXA_LOGMAIL_228794 et des JSON sauvegardés.\n\n"
            "Continuer ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        progress = None
        rebuilt = 0
        skipped = 0
        errors: list[str] = []
        canceled = False

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            rows = self.logmail_repo.get_all_search_index_source_rows() or []
            self.logmail_repo.clear_search_index()
            lookup = self._build_saved_json_lookup_for_search_index()
            transporter_name_cache: dict[str, str] = {}

            progress = QProgressDialog(
                "Reconstruction de l'index de recherche…",
                "Annuler",
                0,
                max(1, len(rows)),
                self,
            )
            progress.setWindowTitle("Index recherche")
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)

            for i, row in enumerate(rows, start=1):
                if progress.wasCanceled():
                    canceled = True
                    break

                entry_id = str((row or {}).get("entry_id") or "").strip()
                nom_pdf = str((row or {}).get("nom_pdf") or "").strip()
                if not entry_id:
                    skipped += 1
                    continue

                progress.setValue(i - 1)
                progress.setLabelText(f"Reconstruction de l'index…\n{i}/{len(rows)}\n{nom_pdf}")
                QApplication.processEvents()

                try:
                    data = self._find_saved_json_for_search_index(entry_id, nom_pdf, lookup)
                    if not isinstance(data, dict):
                        data = {}

                    try:
                        tour_numbers = self._extract_tournrs_from_saved(data) if data else []
                    except Exception:
                        tour_numbers = []

                    status = str((row or {}).get("processing_status") or data.get("status") or "pending").strip()
                    invoice_number = str(data.get("invoice_number") or "").strip()
                    invoice_date = str(data.get("invoice_date") or (row or {}).get("invoice_date") or "").strip()
                    iban = str(data.get("iban") or (row or {}).get("iban") or "").strip()
                    bic = str(data.get("bic") or (row or {}).get("bic") or "").strip()
                    kundennr = str(data.get("transporter_kundennr") or data.get("selected_kundennr") or "").strip()
                    transporter_name = self._transporter_name_for_search_index(
                        kundennr,
                        fallback=str(data.get("transporter_name") or data.get("transporter_text") or "").strip(),
                        cache=transporter_name_cache,
                    )

                    self.logmail_repo.upsert_search_index(
                        entry_id=entry_id,
                        nom_pdf=nom_pdf,
                        status=status,
                        invoice_number=invoice_number,
                        invoice_date=invoice_date,
                        iban=iban,
                        bic=bic,
                        tour_numbers=tour_numbers,
                        transporter_kundennr=kundennr,
                        transporter_name=transporter_name,
                        date_mail=(row or {}).get("date_mail"),
                        expediteur=str((row or {}).get("expediteur") or "").strip(),
                        verbose=False,
                    )
                    rebuilt += 1
                except Exception as e:
                    skipped += 1
                    if len(errors) < 10:
                        errors.append(f"{entry_id} / {nom_pdf} : {e}")

            if progress:
                progress.setValue(progress.maximum())

        except Exception as e:
            errors.append(str(e))
        finally:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            try:
                if progress:
                    progress.close()
            except Exception:
                pass

        # Force la prochaine recherche à repartir de l'index frais.
        try:
            cache = getattr(self, "_left_search_folder_index_cache", None)
            if isinstance(cache, dict):
                cache.clear()
        except Exception:
            pass

        try:
            folder = str(getattr(self, "current_folder_path", "") or "").strip()
            if folder and hasattr(self, "load_folder"):
                self.load_folder(folder)
        except Exception:
            pass

        if errors:
            QMessageBox.warning(
                self,
                "Index recherche",
                f"Index reconstruit partiellement.\n\n"
                f"Lignes indexées : {rebuilt}\n"
                f"Lignes ignorées/en erreur : {skipped}\n"
                f"Annulé : {'oui' if canceled else 'non'}\n\n"
                "Premières erreurs :\n" + "\n".join(errors[:10]),
            )
        else:
            QMessageBox.information(
                self,
                "Index recherche",
                f"Index reconstruit avec succès.\n\nLignes indexées : {rebuilt}\nLignes ignorées : {skipped}",
            )

    def save_current_data(self, status: str = "draft", show_message: bool = True, pdf_path: str | None = None):
        target_pdf_path = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()
        if not target_pdf_path:
            if show_message:
                QMessageBox.warning(self, "Sauvegarde", "Aucun document sélectionné.")
            return False

        pdf_path = target_pdf_path
        if not pdf_path:
            if show_message:
                QMessageBox.warning(self, "Sauvegarde", "Chemin de document invalide.")
            return False

        requested_status = str(status or "draft").strip().lower()
        if requested_status == "eccarts":
            requested_status = "ecart"

        if self._warn_if_invoice_validated_locked("sauvegarder/modifier", pdf_path=pdf_path):
            return False

        json_path = self._get_saved_json_path(pdf_path)

        # retrouve l'entry_id même si selected_invoice_entry_id n'est pas rempli
        current_entry_id = self._resolve_current_entry_id(pdf_path)

        # relit l'existant pour ne rien perdre
        try:
            data = self._read_saved_invoice_json(pdf_path) or {}
        except Exception:
            data = {}

        # préserve les tags existants
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if str(t).strip()]

        # conserve éventuellement les options de blocage déjà présentes
        doc_name = os.path.basename(pdf_path)
        block_info = self.block_options.get(doc_name, {}) if hasattr(self, "block_options") else {}
        blocked = bool((block_info or {}).get("blocked", False))
        block_comment = str((block_info or {}).get("comment", "") or "").strip()

        # met à jour uniquement les champs utiles
        data["entry_id"] = current_entry_id
        normalized_status = requested_status
        data["status"] = normalized_status
        data["iban"] = self.iban_input.text().strip()
        data["bic"] = self.bic_input.text().strip()
        data["invoice_date"] = self.date_input.text().strip()
        data["invoice_number"] = self.invoice_number_input.text().strip()
        # Champ libre utilisateur, optionnel : les anciens JSON sans cette clé restent valides.
        if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
            data["invoice_comment"] = (self.invoice_comment_input.toPlainText() or "").strip()
        else:
            data["invoice_comment"] = str(data.get("invoice_comment") or "").strip()
        data["transporter_text"] = self.transporter_input.text().strip()
        data["transporter_aux_account"] = (self.transporter_aux_input.text() or "").strip()

        # --- Transporteur : clé canonique + compat ---
        # Le transporteur est imposé par le premier dossier (xxatour.FFNR).
        # On ne retombe plus sur IBAN/BIC ou une sélection manuelle pour le déterminer.
        kundennr, _source_tour_nr = self._resolve_supplier_kundennr_from_folders()

        # clé canonique (utilisée par load_saved_data)
        data["transporter_kundennr"] = kundennr
        # compat (anciens JSON)
        data["selected_kundennr"] = kundennr

        transporter_name = str(data.get("transporter_text") or "").strip()
        try:
            if kundennr and transporter_name.endswith(f"({kundennr})"):
                transporter_name = transporter_name[: -len(f"({kundennr})")].strip()
            if not transporter_name and kundennr:
                transporter = self.transporter_repo.find_transporter_by_kundennr(kundennr) or {}
                transporter_name = str(transporter.get("name1", "") or "").strip()
        except Exception:
            transporter_name = str(data.get("transporter_text") or "").strip()
        data["transporter_name"] = transporter_name


        data["folders"] = self.get_folder_rows() if hasattr(self, "get_folder_rows") else []
        data["vat_lines"] = self.get_vat_rows() if hasattr(self, "get_vat_rows") else []
        data["tags"] = sorted(set(tags))
        data["blocked"] = blocked
        data["block_comment"] = block_comment
        data["saved_by"] = str(getattr(self, "current_username", "") or "").strip()
        data["saved_at"] = datetime.now().isoformat(timespec="seconds")

        # si tu stockes déjà les CMR agrégées sur la facture, on conserve la fonctionnalité
        try:
            if hasattr(self, "_collect_cmr_attachments_for_current_entry"):
                data["cmr_attachments"] = self._collect_cmr_attachments_for_current_entry()
        except Exception:
            pass

        # Positions visuelles optionnelles des champs principaux (compat : absent dans les anciens JSON).
        try:
            positions = self._normalize_field_positions(getattr(self, "field_positions", {}) or data.get("field_positions") or {})
            if positions:
                data["field_positions"] = positions
            elif "field_positions" in data:
                data.pop("field_positions", None)
        except Exception:
            pass

        # si tu as du texte OCR déjà chargé, on le garde
        try:
            if hasattr(self, "ocr_text_view") and self.ocr_text_view is not None:
                data["ocr_text"] = self.ocr_text_view.toPlainText()
        except Exception:
            pass


        # ✅ 1) mettre à jour EN BDD par nom de fichier (crée entry_id si absent)
        sql_error = None
        final_entry_id = str(current_entry_id or "").strip()
        try:
            pdf_filename = os.path.basename(pdf_path)
            iban_to_save = self.iban_input.text().strip()
            bic_to_save = self.bic_input.text().strip()
            print(f"DEBUG: Saving to DB for {pdf_filename} - IBAN: {iban_to_save}, BIC: {bic_to_save}")
            returned_entry_id = self.logmail_repo.update_document_by_filename(
                pdf_filename,
                entry_id=current_entry_id,  # Peut être vide, la méthode va chercher en BDD
                invoice_date=self.date_input.text().strip(),
                iban=iban_to_save,
                bic=bic_to_save,
                status=str(status or "").strip().lower() if status else None,
            )
            final_entry_id = str(returned_entry_id or final_entry_id or "").strip()
            print(f"✅ Métadonnées mises à jour en BDD pour {pdf_filename}")
        except Exception as e:
            # on garde l'erreur mais on continue (les champs lourds restent en fichier)
            sql_error = e
            print(f"⚠️ Erreur mise à jour BDD: {e}")

        # ✅ 2) écriture disque (champs lourds)
        try:
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                cache = getattr(self, "_saved_json_cache", None)
                if isinstance(cache, dict):
                    cache.pop(json_path, None)
            except Exception:
                pass
        except Exception as e:
            # si le SQL est passé mais pas le fichier, on le dit clairement
            if show_message:
                extra = "" if not sql_error else f"\n\nNote: la mise à jour SQL a été tentée et a échoué: {sql_error}"
                QMessageBox.warning(self, "Sauvegarde", f"Impossible d'écrire le JSON :\n{e}{extra}")
            return False

        # si la synchro SQL a échoué, on prévient sans bloquer
        if sql_error is not None and show_message:
            QMessageBox.warning(
                self,
                "Sauvegarde",
                f"Le fichier a été sauvegardé, mais la mise à jour SQL a échoué :\n{sql_error}"
            )

        # si on a retrouvé un entry_id, on le garde en mémoire pour la suite
        if final_entry_id:
            self.selected_invoice_entry_id = final_entry_id
            data["entry_id"] = final_entry_id

        # ✅ 3) Alimentation de l'index SQL de recherche.
        # Important : ce bloc est volontairement placé APRÈS la résolution de
        # final_entry_id. Avant, l'index pouvait ne jamais être hydraté si
        # current_entry_id était vide au début de la sauvegarde.
        search_index_error = None
        try:
            if final_entry_id:
                pdf_filename = os.path.basename(pdf_path)
                try:
                    tour_numbers = self._extract_tournrs_from_saved(data) if hasattr(self, "_extract_tournrs_from_saved") else []
                except Exception:
                    tour_numbers = []
                if not tour_numbers:
                    try:
                        tour_numbers = [str((r or {}).get("tour_nr") or "").strip() for r in (data.get("folders") or []) if str((r or {}).get("tour_nr") or "").strip()]
                    except Exception:
                        tour_numbers = []

                index_status = normalized_status
                result_index = self.logmail_repo.upsert_search_index(
                    entry_id=final_entry_id,
                    nom_pdf=pdf_filename,
                    status=index_status,
                    invoice_number=str(data.get("invoice_number") or "").strip(),
                    invoice_date=str(data.get("invoice_date") or "").strip(),
                    iban=str(data.get("iban") or "").strip(),
                    bic=str(data.get("bic") or "").strip(),
                    tour_numbers=tour_numbers,
                    transporter_kundennr=str(data.get("transporter_kundennr") or data.get("selected_kundennr") or "").strip(),
                    transporter_name=str(data.get("transporter_name") or data.get("transporter_text") or "").strip(),
                )
                print(f"DEBUG SEARCH INDEX: save_current_data result={result_index} entry_id={final_entry_id}")
        except Exception as e:
            search_index_error = e
            print(f"⚠️ Erreur alimentation XXA_OCR_SEARCH_INDEX: {e}")
            if show_message:
                QMessageBox.warning(
                    self,
                    "Index recherche",
                    "La sauvegarde est faite, mais l'index de recherche n'a pas été alimenté :\n" + str(e)
                )

        # ✅ 3 bis) Alimentation de la table de reporting des modifications OCR.
        # Cette table sert au suivi utilisateur/facture/dossier. Elle doit être
        # alimentée à chaque sauvegarde, indépendamment de l'index de recherche.
        reporting_error = None
        try:
            if hasattr(self, "reporting_repo") and self.reporting_repo is not None:
                utilisateur = str(getattr(self, "current_username", "") or "").strip()
                rech_nr = str(data.get("invoice_number") or "").strip()

                try:
                    reporting_tour_numbers = (
                        self._extract_tournrs_from_saved(data)
                        if hasattr(self, "_extract_tournrs_from_saved")
                        else []
                    )
                except Exception:
                    reporting_tour_numbers = []

                if not reporting_tour_numbers:
                    try:
                        reporting_tour_numbers = [
                            str((r or {}).get("tour_nr") or "").strip()
                            for r in (data.get("folders") or [])
                            if str((r or {}).get("tour_nr") or "").strip()
                        ]
                    except Exception:
                        reporting_tour_numbers = []

                # Le reporting est volontairement non bloquant : une erreur ici
                # ne doit pas empêcher la sauvegarde JSON/SQL principale.
                if utilisateur and rech_nr and reporting_tour_numbers:
                    reporting_errors = self.reporting_repo.upsert_modifications_for_invoice(
                        utilisateur=utilisateur,
                        rech_nr=rech_nr,
                        tour_nrs=reporting_tour_numbers,
                        is_bloque=bool(data.get("blocked", False)),
                    )
                    if reporting_errors:
                        reporting_error = " | ".join(reporting_errors)
                        print("⚠️ Erreurs alimentation XXA_OCR_REPORTING_MODIFS: " + reporting_error)
                else:
                    missing_parts = []
                    if not utilisateur:
                        missing_parts.append("utilisateur")
                    if not rech_nr:
                        missing_parts.append("numéro de facture")
                    if not reporting_tour_numbers:
                        missing_parts.append("numéro de dossier")
                    print(
                        "⚠️ Reporting OCR non alimenté : données incomplètes ("
                        + ", ".join(missing_parts)
                        + ")"
                    )
        except Exception as e:
            reporting_error = str(e)
            print(f"⚠️ Erreur alimentation XXA_OCR_REPORTING_MODIFS: {e}")
            if show_message:
                QMessageBox.warning(
                    self,
                    "Reporting OCR",
                    "La sauvegarde est faite, mais XXA_OCR_REPORTING_MODIFS n'a pas été alimentée :\n" + str(e)
                )

        # Invalide l'ancien cache JSON de recherche : il ne doit plus imposer un
        # redémarrage pour voir les nouveaux dossiers/factures.
        try:
            cache = getattr(self, "_left_search_folder_index_cache", None)
            if isinstance(cache, dict):
                cache.clear()
        except Exception:
            pass

        # refresh léger de la ligne dans le tableau gauche, sans recharger tout le dossier
        try:
            country = ""
            if hasattr(self, "_get_country_for_document"):
                country = self._get_country_for_document(
                    pdf_path,
                    self.iban_input.text().strip(),
                    self.bic_input.text().strip(),
                )

            if final_entry_id and hasattr(self, "_update_left_row_for_entry"):
                self._update_left_row_for_entry(
                    final_entry_id,
                    self.date_input.text().strip(),
                    self.iban_input.text().strip(),
                    self.bic_input.text().strip(),
                    country,
                )
            elif hasattr(self, "_update_left_table_date_iban_bic"):
                self._update_left_table_date_iban_bic(
                    pdf_path,
                    self.date_input.text().strip(),
                    self.iban_input.text().strip(),
                    self.bic_input.text().strip(),
                )

            if hasattr(self, "pdf_table") and self.pdf_table is not None and hasattr(self, "refresh_left_row_processing_state"):
                for row in range(self.pdf_table.rowCount()):
                    it0 = self.pdf_table.item(row, 0)
                    if not it0:
                        continue
                    row_pdf = it0.data(Qt.UserRole)
                    row_entry_id = str(it0.data(Qt.UserRole + 4) or "").strip()
                    if row_pdf == pdf_path or (final_entry_id and row_entry_id == final_entry_id):
                        self.refresh_left_row_processing_state(row)
                        break

            if hasattr(self, "apply_left_table_search_filter"):
                self.apply_left_table_search_filter()
        except Exception:
            pass

        # ✅ met aussi à jour le modèle transporteur après une sauvegarde réussie
        supplier_model_error = None
        try:
            self.save_supplier_model(show_message=False)
        except Exception as e:
            supplier_model_error = e

        if show_message:
            if supplier_model_error is None:
                self.statusBar().showMessage("Données sauvegardées.", 2500)
            else:
                self.statusBar().showMessage(
                    f"Données sauvegardées, mais modèle transporteur non mis à jour : {supplier_model_error}",
                    5000
                )

        # ✅ Synchroniser le JSON si l'entry_id final a été déterminé pendant la sauvegarde
        try:
            if final_entry_id and final_entry_id != current_entry_id:
                self.selected_invoice_entry_id = final_entry_id
                data["entry_id"] = final_entry_id
                json_path = self._get_saved_json_path(pdf_path)
                os.makedirs(os.path.dirname(json_path), exist_ok=True)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                try:
                    cache = getattr(self, "_saved_json_cache", None)
                    if isinstance(cache, dict):
                        cache.pop(json_path, None)
                except Exception:
                    pass
        except Exception:
            pass

        return True


    def load_saved_data(self) -> bool:
        """Recharge les données JSON du PDF courant.

        Important : on restaure d'abord les champs facture (IBAN/BIC/Date/N°),
        puis le transporteur. Avant, on rechargeait le transporteur alors que
        l'IBAN/BIC n'étaient pas encore remis, ce qui donnait l'impression que
        la sauvegarde n'avait pas fonctionné.
        """
        if not self.current_pdf_path:
            return False

        json_path = self._get_saved_json_path(self.current_pdf_path)
        if not os.path.exists(json_path):
            return False

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}

            # --- Mémoire annexes ---
            self.pallet_details = data.get("pallet_details", {}) or {}
            self.block_options = data.get("block_options", {}) or {}
            self.field_positions = self._normalize_field_positions(data.get("field_positions") or {})
            try:
                self.pdf_viewer.set_highlights(self.field_positions)
            except Exception:
                pass

            # --- Champs facture ---
            invoice_date = str(data.get("invoice_date", "") or "").strip()
            iban = str(data.get("iban", "") or "").strip()
            bic = str(data.get("bic", "") or "").strip()
            invoice_number = str(data.get("invoice_number", "") or "").strip()

            # ✅ Le JSON est prioritaire quand il existe : il peut être plus récent
            # que les valeurs SQL déjà chargées dans l'interface d'un autre utilisateur.
            # On applique aussi les valeurs vides pour propager une suppression.
            if "iban" in data:
                self.iban_input.setText(iban)
            if "bic" in data:
                self.bic_input.setText(bic)
            if "invoice_date" in data:
                self.date_input.setText(normalize_date_format(invoice_date) if invoice_date else "")
            if "invoice_number" in data:
                self.invoice_number_input.setText(invoice_number)

            # Commentaire libre facture : nouveau champ optionnel.
            # Compatibilité : ancien JSON sans clé => champ vide.
            if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
                invoice_comment = str(
                    data.get("invoice_comment")
                    or data.get("free_invoice_comment")
                    or data.get("commentaire_libre")
                    or ""
                )
                self.invoice_comment_input.setPlainText(invoice_comment)


            self._update_left_table_date_iban_bic(
                self.current_pdf_path,
                (invoice_date or self.date_input.text().strip()),
                (iban or self.iban_input.text().strip()),
                (bic or self.bic_input.text().strip()),
            )


            # --- Transporteur ---
            # Le texte transporteur sauvegardé n'est plus restauré directement :
            # il est recalculé après reconstruction des dossiers, depuis le
            # premier TourNr via xxatour.FFNR.
            saved_aux = str(data.get("transporter_aux_account") or "").strip()
            if "transporter_aux_account" in data:
                self.transporter_aux_input.setText(saved_aux)
                self._pending_saved_transporter_aux = saved_aux

            self._pending_saved_transporter_aux_kundennr = str(
                data.get("transporter_kundennr")
                or data.get("selected_kundennr")
                or ""
            ).strip()

            # --- OCR texte ---
            ocr_text = data.get("ocr_text", "")
            self.ocr_text_view.setPlainText(ocr_text if isinstance(ocr_text, str) else "")

            # ✅ dossiers + TVA (ta fonction rebuild gère déjà tout proprement)
            self.rebuild_folder_fields_from_json(data)

            # Transporteur recalculé depuis le premier dossier seulement.
            self.load_transporter_information(force_by_kundennr=False)
            return True

        except Exception as e:
            QMessageBox.warning(self, "Erreur chargement", str(e))
            return False



    def rebuild_folder_fields_from_json(self, data: dict):
        # reset table
        self.vat_table.setRowCount(0)
        vat_lines = data.get("vat_lines", [])
        if isinstance(vat_lines, list):
            for r in vat_lines:
                self._add_vat_row(r.get("rate", ""), r.get("base", ""), r.get("vat", ""))

        self._ensure_empty_vat_row()
        self.update_vat_total()

        self.folder_table.setRowCount(0)

        folders = data.get("folders")

        if isinstance(folders, list) and folders:
            for row in folders:
                tour_nr = "" if row is None else str(row.get("tour_nr", "") or "")
                amt = "" if row is None else str(row.get("amount_ht_ocr", "") or "")
                if tour_nr or amt:
                    self._add_folder_row(tour_nr, amt)
        else:
            # compat ancienne version
            one = str(data.get("folder_number", "") or "")
            if one:
                self._add_folder_row(one, "")

        self._ensure_empty_folder_row()
        self.update_folder_totals()

    def ocr_all_pdfs(self):
        # sécurité table
        if not hasattr(self, "pdf_table") or self.pdf_table is None or self.pdf_table.rowCount() == 0:
            QMessageBox.information(self, "OCR", "Aucun PDF à traiter.")
            return

        total_rows = self.pdf_table.rowCount()

        # sauvegarde l'état courant
        previous_pdf = self.current_pdf_path

        processed = 0
        skipped = 0
        errors = 0
        canceled = False
        progress = self._build_progress_dialog("OCR de masse", f"OCR 0/{total_rows}", total_rows)
        progress.show()
        QApplication.processEvents()

        try:
            for row in range(total_rows):
                if progress.wasCanceled():
                    canceled = True
                    break

                it0 = self.pdf_table.item(row, 0)
                progress.setValue(row)
                progress.setLabelText(f"OCR {row + 1}/{total_rows}\n{it0.text() if it0 else ''}")
                QApplication.processEvents()

                if not it0:
                    continue

                pdf_path = it0.data(Qt.UserRole)
                if not is_ocr_allowed_document(pdf_path):
                    skipped += 1
                    continue

                # ✅ On OCRise uniquement les non-sauvegardés (pas de JSON)
                if self._has_saved_json_for_pdf(pdf_path):
                    skipped += 1
                    continue

                try:
                    self.current_pdf_path = pdf_path

                    # Clear fields before processing each PDF to avoid carrying over data from previous PDFs
                    self.clear_fields()

                    # OCR (sans popup)
                    self.analyze_pdf(show_message=False, document_path=pdf_path, auto_save=False)

                    # Debug: show extracted IBAN/BIC
                    pdf_filename = os.path.basename(pdf_path)
                    iban_after_ocr = self.iban_input.text().strip()
                    bic_after_ocr = self.bic_input.text().strip()
                    print(f"DEBUG: After OCR for {pdf_filename} - IBAN: {iban_after_ocr}, BIC: {bic_after_ocr}")

                    # sauvegarde OCR, mais on reste en "pending" tant que ce n'est pas validé
                    self.save_current_data(status="pending", show_message=False)

                    # statut en table (pour les filtres)
                    self._set_left_row_status(pdf_path, "pending")

                    processed += 1

                except Exception as e:
                    errors += 1
                    # (optionnel) marquer en erreur pour ton futur onglet “Erreurs”
                    self._set_left_row_status(pdf_path, "error")
                    # on continue sur les autres
                    print(f"OCR error on {pdf_path}: {e}")

            progress.setValue(total_rows)
            QApplication.processEvents()
        finally:
            # restore
            self.current_pdf_path = previous_pdf
            progress.close()

        self.refresh_left_table_processing_states()
        self.apply_left_filter_to_table()

        # ré-applique tes filtres si tu les as
        if hasattr(self, "apply_left_filter_to_table"):
            self.apply_left_filter_to_table()

        title = "OCR annulé" if canceled else "OCR terminé"
        suffix = "\nTraitement interrompu par l'utilisateur." if canceled else ""
        QMessageBox.information(
            self,
            title,
            f"Traités : {processed}\nDéjà sauvegardés (skip) : {skipped}\nErreurs : {errors}{suffix}"
        )

    def _save_data_for_pdf(self, pdf_path, data):
        if self._warn_if_invoice_validated_locked("sauvegarder/modifier", pdf_path=pdf_path):
            return False

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = MODELS_DIR
        os.makedirs(model_dir, exist_ok=True)
        json_path = os.path.join(model_dir, f"{base_name}.json")

        folder_numbers = []
        if getattr(data, "folder_numbers", None):
            folder_numbers = data.folder_numbers or []
        elif getattr(data, "folder_number", None):
            folder_numbers = [data.folder_number] if data.folder_number else []

        existing_payload = {}
        try:
            if os.path.exists(json_path):
                with open(json_path, "r", encoding="utf-8") as f:
                    existing_payload = json.load(f) or {}
        except Exception:
            existing_payload = {}

        payload = dict(existing_payload) if isinstance(existing_payload, dict) else {}
        payload.update({
            "iban": data.iban or "",
            "bic": data.bic or "",
            "invoice_date": data.invoice_date or "",
            "invoice_number": data.invoice_number or "",
            "folder_numbers": folder_numbers,
        })

        # Ne jamais perdre le commentaire libre lors d'une sauvegarde OCR
        # ancienne/annexe qui passe par _save_data_for_pdf au lieu de save_current_data.
        try:
            if hasattr(self, "invoice_comment_input") and self.invoice_comment_input is not None:
                current_comment = (self.invoice_comment_input.toPlainText() or "").strip()
                if current_comment or "invoice_comment" in payload:
                    payload["invoice_comment"] = current_comment
        except Exception:
            pass

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return True

    def _model_exists_for_pdf(self, pdf_path):
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = MODELS_DIR
        json_path = os.path.join(model_dir, f"{base_name}.json")
        return os.path.exists(json_path)

    def save_supplier_model(self, checked: bool = False, show_message: bool = True) -> bool:
        ocr_text = self.ocr_text_view.toPlainText() or ""

        # 1) récupérer IBAN/BIC robustes depuis l’OCR si possible.
        # Ils restent stockés dans le modèle pour compatibilité et transition,
        # mais la clé principale devient le KundenNr trouvé via un dossier.
        best = extract_best_bank_ids(
            ocr_text,
            prefer_iban=self.iban_input.text().strip(),
            prefer_bic=self.bic_input.text().strip(),
        )

        iban = best.get("iban") or self.iban_input.text().strip()
        bic  = best.get("bic")  or self.bic_input.text().strip()

        # En mode "silencieux" (validation), on ne modifie pas les champs UI
        if show_message:
            if iban:
                self.iban_input.setText(iban)
            if bic:
                self.bic_input.setText(bic)

        ctx = self._get_supplier_model_context(iban=iban, bic=bic)
        supplier_key = ctx.get("primary_key")
        key_type = "kundennr"

        if not supplier_key:
            msg = (
                "Impossible de sauvegarder le modèle : aucun KundenNr trouvé via le premier dossier.\n"
                "Renseigne au moins un dossier valide, présent dans xxatour avec FFNR, puis réessaie."
            )
            if show_message:
                QMessageBox.warning(self, "Modèle transporteur", msg)
            else:
                self.statusBar().showMessage("Modèle transporteur non mis à jour (KundenNr du premier dossier introuvable).", 4000)
            return False

        # 2) extraire les données TVA pour apprentissage
        # On privilégie les lignes TVA déjà visibles / corrigées dans l'écran.
        vat_lines = []
        if hasattr(self, "get_vat_rows"):
            for row in (self.get_vat_rows() or []):
                rate = str((row or {}).get("rate") or "").strip()
                base = str((row or {}).get("base") or "").strip()
                vat = str((row or {}).get("vat") or "").strip()
                if rate or base or vat:
                    vat_lines.append({"rate": rate, "base": base, "vat": vat})
        if not vat_lines:
            from ocr.invoice_parser import parse_vat_lines
            vat_lines = parse_vat_lines(ocr_text)

        # 3) charger l’existant.
        # Rétrocompatibilité : si le nouveau modèle KundenNr n'existe pas encore,
        # on repart de l'ancien modèle IBAN_BIC lorsqu'il existe, puis on sauvegarde
        # sous la nouvelle clé KUNDENNR_xxx.
        existing = load_supplier_model(supplier_key) or {}
        legacy_key = ctx.get("legacy_key")
        if not existing and ctx.get("primary_key") and legacy_key:
            existing = load_supplier_model(legacy_key) or {}

        # Supprimer les anciens champs d'exemple pour éviter la confusion
        for old_field in ["invoice_number_example", "date_example", "folder_number_example"]:
            existing.pop(old_field, None)

        # 4) apprendre / merger les patterns
        new_patterns = learn_supplier_patterns(
            ocr_text,
            iban=iban,
            bic=bic,
            invoice_number=self.invoice_number_input.text().strip(),
            invoice_date=self.date_input.text().strip(),
            vat_lines=vat_lines,
        )
        merged = merge_patterns(existing.get("patterns") or {}, new_patterns)

        # 5) construire data
        data = dict(existing)
        data.update({
            "supplier_key": supplier_key,
            "supplier_key_type": key_type,
            "supplier_kundennr": ctx.get("kundennr") or existing.get("supplier_kundennr", ""),
            "supplier_kundennr_source": ctx.get("kundennr_source") or existing.get("supplier_kundennr_source", ""),
            "source_tour_nr": ctx.get("source_tour_nr") or existing.get("source_tour_nr", ""),
            "legacy_supplier_key": legacy_key or existing.get("legacy_supplier_key", ""),
            "iban": iban or existing.get("iban", ""),
            "bic": bic or existing.get("bic", ""),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "patterns": merged,
            "model_version": 3,
        })

        # 6) sauver le fichier
        try:
            save_supplier_model(supplier_key, data)
            if show_message:
                detail = "KundenNr" if key_type == "kundennr" else "IBAN/BIC"
                QMessageBox.information(self, "Modèle transporteur", f"Modèle transporteur sauvegardé / mis à jour ({detail}).")
            else:
                self.statusBar().showMessage("Modèle transporteur mis à jour.", 3000)
            return True
        except Exception as e:
            if show_message:
                QMessageBox.critical(self, "Erreur modèle transporteur", str(e))
            else:
                self.statusBar().showMessage("Erreur MAJ modèle transporteur.", 4000)
            return False

    def apply_supplier_model(self, model: dict):
        if not model:
            return

        ocr_text = self.ocr_text_view.toPlainText() or ""
        found = extract_fields_with_model(ocr_text, model)

        # IBAN/BIC : valeur trouvée via patterns, sinon valeur stockée modèle.
        # Important avec les modèles KundenNr : ne pas vider un IBAN/BIC déjà OCRisé
        # si le nouveau modèle n'en contient pas encore.
        model_iban = (found.get("iban") or model.get("iban", "") or "").strip()
        current_iban = self.iban_input.text().strip()
        # Ne pas écraser un IBAN déjà OCRisé / saisi valide avec l'IBAN stocké
        # dans le modèle KundenNr. Le modèle sert de secours, l'OCR du document prime.
        if model_iban and (not current_iban or not validate_iban(current_iban)):
            self.iban_input.setText(model_iban)

        model_bic = (found.get("bic") or model.get("bic", "") or "").strip()
        current_bic = self.bic_input.text().strip()
        if model_bic and (not current_bic or not validate_bic(current_bic)):
            self.bic_input.setText(model_bic)

        cur = (self.invoice_number_input.text() or "").strip()
        is_date_like = bool(re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", cur)) or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", cur))
        is_ok = bool(cur) and any(c.isdigit() for c in cur) and cur.upper() not in {"DESCRIPTION", "DATE", "FACTURE", "INVOICE"} and not is_date_like

        # si le modèle a trouvé un numéro de facture, on lui donne priorité
        invoice_number_from_model = (found.get("invoice_number") or "").strip()
        if invoice_number_from_model:
            self.invoice_number_input.setText(invoice_number_from_model)
        elif not is_ok:
            self.invoice_number_input.setText(invoice_number_from_model or "")

        model_invoice_date = normalize_date_format(found.get("invoice_date") or "")
        current_invoice_date = normalize_date_format(self.date_input.text().strip())
        if model_invoice_date and (not current_invoice_date or model_invoice_date != current_invoice_date):
            self.date_input.setText(model_invoice_date)

        if not self.get_folder_numbers():
            folder_from_model = found.get("folder_number") or ""
            if folder_from_model and self.DOSSIER_PATTERN.fullmatch(folder_from_model):
                dossier_le, _, vat_theo_le = self._get_row_widgets(0)
                if dossier_le:
                    dossier_le.setText(folder_from_model)
                    self._ensure_empty_folder_row()

    def _update_left_table_date_iban_bic(self, pdf_path: str, invoice_date: str, iban: str, bic: str):
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
            
    def _claim_selected_entry(self, entry_id: str | None):
        entry_id = str(entry_id or "").strip()
        username = str(getattr(self, "current_username", "") or "").strip()

        if not entry_id or entry_id.startswith("__NO_ENTRY__"):
            return

        if not username:
            return

        try:
            claimed = bool(self.logmail_repo.claim_entry_for_user(entry_id, username))
        except Exception as e:
            claimed = False

        if claimed:
            self._claimed_entry_id = entry_id
        else:
            try:
                processing_user = str(self.logmail_repo.get_processing_user_for_entry(entry_id) or "").strip()
            except Exception:
                processing_user = ""

            if processing_user and processing_user != username:
                self.statusBar().showMessage(
                    f"Document déjà en cours de traitement par {processing_user}.",
                    5000,
                )
            elif not processing_user:
                self.statusBar().showMessage(
                    f"Impossible de réserver la ligne SQL pour entry_id={entry_id}.",
                    5000,
                )

            self._claimed_entry_id = None

        self.refresh_left_table_processing_claims()


    def _release_claimed_entry(self):
        username = str(getattr(self, "current_username", "") or "").strip()
        if not username:
            self._claimed_entry_id = None
            return

        try:
            self.logmail_repo.release_all_entries_for_user(username)
        except Exception:
            pass

        self._claimed_entry_id = None
        self.refresh_left_table_processing_claims()


    def _resolve_current_entry_id(self, pdf_path: str | None = None) -> str:
        """
        Retrouve l'entry_id courant de façon robuste.
        Priorité :
        1) lookup SQL via le nom du PDF courant
        2) entry_id déjà présent dans le JSON du document courant
        3) selected_invoice_entry_id (fallback UI)
        """
        pdf_path = str(pdf_path or getattr(self, "current_pdf_path", "") or "").strip()

        if pdf_path:
            try:
                entry_id = str(
                    self.logmail_repo.get_entry_id_for_file(os.path.basename(pdf_path)) or ""
                ).strip()
                if entry_id:
                    return entry_id
            except Exception:
                pass

            try:
                data = self._read_saved_invoice_json(pdf_path) or {}
                entry_id = str(data.get("entry_id") or "").strip()
                if entry_id:
                    return entry_id
            except Exception:
                pass

        entry_id = str(getattr(self, "selected_invoice_entry_id", "") or "").strip()
        if entry_id:
            return entry_id

        return ""
