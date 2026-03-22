from __future__ import annotations
import fitz
import pytesseract
from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled
from ocr.invoice_parser import normalize_date_format
from ocr.supplier_model import validate_iban, validate_bic


class MainWindowCoreMixin:

    def set_active_field(self, field):
        self.active_field = field

        self.pdf_viewer.active_field = field
        self.pdf_viewer.field_colors = self.FIELD_COLORS

        field.setStyleSheet("background-color: #fff3cd;")

        # ✅ Volet info selon champ actif
        # ✅ Volet info selon champ actif
        if field in (self.iban_input, self.bic_input):
            # IBAN/BIC -> toujours par banque
            self.transporter_selected_mode = False
            self.load_transporter_information(force_by_kundennr=False)
            return

        if field == self.transporter_input:
            # Transporteur -> si on a sélectionné un transporteur avant, on recharge par kundennr
            self.load_transporter_information(force_by_kundennr=self.transporter_selected_mode)
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

            doc = fitz.open(doc_path)
            pixmaps = []
            for page in doc:
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
                pixmaps.append(QPixmap.fromImage(img))
            self.pdf_viewer.set_pages(pixmaps)
            doc.close()

        except Exception as e:
            QMessageBox.critical(self, "Erreur document", str(e))



    def refresh_invoice_data(self):
        """Recharge les données pour le PDF sélectionné.
        - Si un JSON existe : on recharge.
        - Sinon : on OCR automatiquement.
        """
        if not self.current_pdf_path:
            return

        # reset UI
        self.bank_valid = None
        self.selected_kundennr = None
        self.current_db_iban = None
        self.current_db_bic = None
        self.transporter_selected_mode = False

        # champs facture
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.blockSignals(True)
            field.clear()
            field.setStyleSheet("")
            field.blockSignals(False)

        # transporteur
        self.transporter_input.blockSignals(True)
        self.transporter_input.clear()
        self.transporter_input.blockSignals(False)
        self.btn_transporter_action.setEnabled(False)
        self.transporter_info.clear()
        if hasattr(self, "tour_info") and self.tour_info is not None:
            self.tour_info.clear()

        # expéditeur
        self.sender_input.clear()

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
        except Exception:
            saved_data = {}

        # Remplir les champs facture si pas déjà remplis depuis BDD
        if not self.iban_input.text().strip() and saved_data.get("iban"):
            self.iban_input.setText(saved_data["iban"])
        if not self.bic_input.text().strip() and saved_data.get("bic"):
            self.bic_input.setText(saved_data["bic"])
        if not self.date_input.text().strip() and saved_data.get("invoice_date"):
            self.date_input.setText(normalize_date_format(saved_data["invoice_date"]))
        if not self.invoice_number_input.text().strip() and saved_data.get("invoice_number"):
            self.invoice_number_input.setText(saved_data["invoice_number"])

        # Transporteur
        if saved_data.get("transporter_text"):
            self.transporter_input.setText(saved_data["transporter_text"])
        if saved_data.get("transporter_aux_account"):
            self.transporter_aux_input.setText(saved_data["transporter_aux_account"])
        if saved_data.get("selected_kundennr"):
            self.selected_kundennr = saved_data["selected_kundennr"]

        # Dossiers
        folders = saved_data.get("folders") or []
        if folders:
            # Supprimer la ligne vide ajoutée par clear_folder_fields
            self.folder_table.setRowCount(0)
            for folder in folders:
                if isinstance(folder, dict):
                    tour_nr = folder.get("tour_nr", "")
                    amount = folder.get("amount_ht_ocr", "")
                    vat_theo = ""  # vat_theo n'est pas sauvegardé, laissé vide
                    self._add_folder_row(tour_nr, amount, vat_theo)
            # Ajouter une ligne vide à la fin si nécessaire
            self._ensure_empty_folder_row()

        # TVA
        vat_lines = saved_data.get("vat_lines") or []
        if vat_lines:
            # Supprimer la ligne vide ajoutée par _ensure_empty_vat_row
            self.vat_table.setRowCount(0)
            for vat_line in vat_lines:
                if isinstance(vat_line, dict):
                    rate = vat_line.get("rate", "")
                    base = vat_line.get("base", "")
                    vat = vat_line.get("vat", "")
                    self._add_vat_row(rate, base, vat)

        # OCR text
        if saved_data.get("ocr_text"):
            self.ocr_text_view.setPlainText(saved_data["ocr_text"])

        # Mettre à jour les totaux
        self.update_folder_totals()
        self.update_vat_total()
        self._ensure_empty_vat_row()

        # Vérifier les informations bancaires si IBAN/BIC remplis
        iban = self.iban_input.text().strip()
        bic = self.bic_input.text().strip()
        if iban and bic:
            self.check_bank_information()
        if self.transporter_input.text().strip():
            self.load_transporter_information()

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

    def _get_active_document_path(self, preferred_path: str | None = None) -> str | None:
        for candidate in (preferred_path, getattr(self, "view_pdf_path", None), getattr(self, "current_pdf_path", None)):
            candidate = str(candidate or "").strip()
            if candidate:
                return candidate
        return None

    def analyze_pdf(self, checked: bool = False, show_message: bool = False, document_path: str | None = None):

        active_doc_path = self._get_active_document_path(document_path)
        if not active_doc_path:
            QMessageBox.warning(self, "Analyse OCR", "Aucun document sélectionné.")
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
            self.autofill_folder_amounts_from_ocr(text)
            self.update_folder_totals()
            self.check_bank_information()
            self.load_transporter_information()
            iban = self.iban_input.text().strip()
            bic = self.bic_input.text().strip()
            supplier_key = build_supplier_key(iban, bic)
            model = load_supplier_model(supplier_key)

            if model:
                self.apply_supplier_model(model)

            self.highlight_missing_fields()
            ocr_text = self.ocr_text_view.toPlainText() or ""
            best = extract_best_bank_ids(
                ocr_text,
                prefer_iban=self.iban_input.text().strip(),
                prefer_bic=self.bic_input.text().strip(),
            )

            current_iban = self.iban_input.text().strip()
            current_bic = self.bic_input.text().strip()
            bic_scores = dict(best.get("bic_candidates") or [])
            best_bic_score = int(bic_scores.get(best.get("bic") or "", 0) or 0)
            current_bic_score = int(bic_scores.get(current_bic.replace(" ", "").upper(), 0) or 0)
            if best["iban"] and (not current_iban or not validate_iban(current_iban)):
                self.iban_input.setText(best["iban"])
            if best["bic"] and (
                not current_bic
                or not validate_bic(current_bic)
                or (best["bic"] != current_bic.replace(" ", "").upper() and best_bic_score > current_bic_score)
            ):
                self.bic_input.setText(best["bic"])

            supplier_key = build_supplier_key(
                self.iban_input.text().strip(),
                self.bic_input.text().strip(),
            )
            if supplier_key:
                model = load_supplier_model(supplier_key)
                if model:
                    self.apply_supplier_model(model)

            # ✅ Si aucun IBAN ou BIC valide n'est trouvé, lancer OCR profond automatiquement
            final_iban = self.iban_input.text().strip()
            final_bic = self.bic_input.text().strip()
            if not final_iban or not validate_iban(final_iban) or not final_bic or not validate_bic(final_bic):
                self.statusBar().showMessage("OCR normal terminé, lancement OCR profond...", 2000)
                QApplication.processEvents()
                self.analyze_pdf_deep(document_path=document_path)

            self.statusBar().showMessage("OCR terminé.", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Erreur OCR", str(e))
        if show_message:
            QMessageBox.information(...)

    def _build_progress_dialog(self, title: str, label: str, maximum: int) -> QProgressDialog:
        dlg = QProgressDialog(label, "Annuler", 0, max(0, int(maximum)), self)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        return dlg
    def analyze_pdf_deep(self, checked: bool = False, document_path: str | None = None):
        active_doc_path = self._get_active_document_path(document_path)
        if not active_doc_path:
            QMessageBox.warning(self, "OCR profond", "Aucun document sélectionné.")
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
            self.load_transporter_information()

            best = extract_best_bank_ids(
                merged_text,
                prefer_iban=self.iban_input.text().strip(),
                prefer_bic=self.bic_input.text().strip(),
            )
            current_iban = self.iban_input.text().strip()
            current_bic = self.bic_input.text().strip()
            bic_scores = dict(best.get("bic_candidates") or [])
            best_bic_score = int(bic_scores.get(best.get("bic") or "", 0) or 0)
            current_bic_score = int(bic_scores.get(current_bic.replace(" ", "").upper(), 0) or 0)
            if best["iban"] and (not current_iban or not validate_iban(current_iban)):
                self.iban_input.setText(best["iban"])
            if best["bic"] and (
                not current_bic
                or not validate_bic(current_bic)
                or (best["bic"] != current_bic.replace(" ", "").upper() and best_bic_score > current_bic_score)
            ):
                self.bic_input.setText(best["bic"])

            supplier_key = build_supplier_key(
                self.iban_input.text().strip(),
                self.bic_input.text().strip(),
            )
            if supplier_key:
                model = load_supplier_model(supplier_key)
                if model:
                    self.apply_supplier_model(model)

            self.highlight_missing_fields()
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
            if field in (self.iban_input, self.bic_input) and self.bank_valid is not None:
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



    def save_current_data(self, status: str = "draft", show_message: bool = True):
        if not self.current_pdf_path:
            if show_message:
                QMessageBox.warning(self, "Sauvegarde", "Aucun document sélectionné.")
            return False

        pdf_path = str(self.current_pdf_path).strip()
        if not pdf_path:
            if show_message:
                QMessageBox.warning(self, "Sauvegarde", "Chemin de document invalide.")
            return False

        json_path = self._get_saved_json_path(pdf_path)

        # retrouve l'entry_id même si selected_invoice_entry_id n'est pas rempli
        current_entry_id = self._resolve_current_entry_id()

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
        data["status"] = str(status or "draft").strip().lower()
        data["iban"] = self.iban_input.text().strip()
        data["bic"] = self.bic_input.text().strip()
        data["invoice_date"] = self.date_input.text().strip()
        data["invoice_number"] = self.invoice_number_input.text().strip()
        data["transporter_text"] = self.transporter_input.text().strip()
        data["transporter_aux_account"] = (self.transporter_aux_input.text() or "").strip()

        # --- Transporteur : clé canonique + compat ---
        kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()

        # fallback si le champ contient "Nom (12345)"
        if not kundennr:
            try:
                m = re.search(r"\((\d+)\)\s*$", self.transporter_input.text() or "")
                if m:
                    kundennr = m.group(1)
            except Exception:
                pass

        # clé canonique (utilisée par load_saved_data)
        data["transporter_kundennr"] = kundennr
        # compat (anciens JSON)
        data["selected_kundennr"] = kundennr


        data["folders"] = self.get_folder_rows() if hasattr(self, "get_folder_rows") else []
        data["vat_lines"] = self.get_vat_rows() if hasattr(self, "get_vat_rows") else []
        data["tags"] = sorted(set(tags))
        data["blocked"] = blocked
        data["block_comment"] = block_comment

        # si tu stockes déjà les CMR agrégées sur la facture, on conserve la fonctionnalité
        try:
            if hasattr(self, "_collect_cmr_attachments_for_current_entry"):
                data["cmr_attachments"] = self._collect_cmr_attachments_for_current_entry()
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
        try:
            pdf_filename = os.path.basename(pdf_path)
            iban_to_save = self.iban_input.text().strip()
            bic_to_save = self.bic_input.text().strip()
            print(f"DEBUG: Saving to DB for {pdf_filename} - IBAN: {iban_to_save}, BIC: {bic_to_save}")
            self.logmail_repo.update_document_by_filename(
                pdf_filename,
                entry_id=current_entry_id,  # Peut être vide, la méthode va chercher en BDD
                invoice_date=self.date_input.text().strip(),
                iban=iban_to_save,
                bic=bic_to_save,
                status=str(status or "").strip().lower() if status else None,
            )
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
        if current_entry_id:
            self.selected_invoice_entry_id = current_entry_id

        # refresh léger de la ligne dans le tableau gauche
        try:
            if hasattr(self, "_update_left_table_date_iban_bic"):
                self._update_left_table_date_iban_bic(
                    pdf_path,
                    self.date_input.text().strip(),
                    self.iban_input.text().strip(),
                    self.bic_input.text().strip(),
                )
        except Exception:
            pass

        # si un dossier courant est chargé, on peut recharger la liste pour garder les onglets cohérents
        try:
            current_folder = str(getattr(self, "current_folder_path", "") or "").strip()
            if current_folder and os.path.isdir(current_folder):
                self.load_folder(current_folder)
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

        # ✅ Recharger l'entry_id depuis BDD si on vient de l'assigner
        try:
            pdf_filename = os.path.basename(pdf_path)
            db_entry_id = str(self.logmail_repo.get_entry_id_for_file(pdf_filename) or "").strip()
            if db_entry_id and db_entry_id != current_entry_id:
                self.selected_invoice_entry_id = db_entry_id
                # Synchroniser aussi le JSON
                data["entry_id"] = db_entry_id
                json_path = self._get_saved_json_path(pdf_path)
                os.makedirs(os.path.dirname(json_path), exist_ok=True)
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
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

            # --- Champs facture ---
            invoice_date = str(data.get("invoice_date", "") or "").strip()
            iban = str(data.get("iban", "") or "").strip()
            bic = str(data.get("bic", "") or "").strip()
            invoice_number = str(data.get("invoice_number", "") or "").strip()

            # ✅ Ne pas écraser les champs SQL (pré-remplis) par du vide JSON
            if iban:
                self.iban_input.setText(iban)
            if bic:
                self.bic_input.setText(bic)
            if invoice_date:
                self.date_input.setText(normalize_date_format(invoice_date))


            self.invoice_number_input.setText(invoice_number)


            self._update_left_table_date_iban_bic(
                self.current_pdf_path,
                (invoice_date or self.date_input.text().strip()),
                (iban or self.iban_input.text().strip()),
                (bic or self.bic_input.text().strip()),
            )


            # --- Transporteur ---
            saved_text = str(data.get("transporter_text") or "").strip()


            saved_aux = str(data.get("transporter_aux_account") or "").strip()
            if saved_aux and not self.transporter_aux_input.text().strip():
                self.transporter_aux_input.setText(saved_aux)


            kundennr = str(
                data.get("transporter_kundennr")
                or data.get("selected_kundennr")
                or ""
            ).strip()

            self.selected_kundennr = kundennr or None
            self.transporter_selected_mode = bool(self.selected_kundennr)

            # restaurer l'affichage tel quel (même si la BDD ne répond pas)
            self.transporter_input.blockSignals(True)
            self.transporter_input.setText(saved_text)
            self.transporter_input.blockSignals(False)
            #self.transporter_vat_input.setText(saved_vat)

            # puis, si possible, rafraîchir depuis la BDD
            if self.selected_kundennr:
                self.load_transporter_information(force_by_kundennr=True)
            elif iban and bic:
                self.load_transporter_information(force_by_kundennr=False)

            # --- OCR texte ---
            ocr_text = data.get("ocr_text", "")
            self.ocr_text_view.setPlainText(ocr_text if isinstance(ocr_text, str) else "")

            # ✅ dossiers + TVA (ta fonction rebuild gère déjà tout proprement)
            self.rebuild_folder_fields_from_json(data)
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
                    self.analyze_pdf(show_message=False, document_path=pdf_path)

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
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = MODELS_DIR
        os.makedirs(model_dir, exist_ok=True)
        json_path = os.path.join(model_dir, f"{base_name}.json")

        folder_numbers = []
        if getattr(data, "folder_numbers", None):
            folder_numbers = data.folder_numbers or []
        elif getattr(data, "folder_number", None):
            folder_numbers = [data.folder_number] if data.folder_number else []

        payload = {
            "iban": data.iban or "",
            "bic": data.bic or "",
            "invoice_date": data.invoice_date or "",
            "invoice_number": data.invoice_number or "",
            "folder_numbers": folder_numbers,
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _model_exists_for_pdf(self, pdf_path):
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        model_dir = MODELS_DIR
        json_path = os.path.join(model_dir, f"{base_name}.json")
        return os.path.exists(json_path)

    def save_supplier_model(self, checked: bool = False, show_message: bool = True) -> bool:
        ocr_text = self.ocr_text_view.toPlainText() or ""

        # 1) récupérer IBAN/BIC robustes depuis l’OCR (validation + scoring)
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

        supplier_key = build_supplier_key(iban, bic)
        if not supplier_key:
            msg = (
                "Impossible de sauvegarder le modèle : IBAN/BIC non fiables.\n"
                "Corrige IBAN/BIC puis réessaie."
            )
            if show_message:
                QMessageBox.warning(self, "Modèle transporteur", msg)
            else:
                self.statusBar().showMessage("Modèle transporteur non mis à jour (IBAN/BIC non fiables).", 4000)
            return False

        # 2) extraire les données TVA pour apprentissage
        from ocr.invoice_parser import parse_vat_lines
        vat_lines = parse_vat_lines(ocr_text)

        # 3) charger l’existant
        existing = load_supplier_model(supplier_key) or {}

        # Supprimer les anciens champs d'exemple pour éviter la confusion
        for old_field in ["invoice_number_example", "date_example", "folder_number_example"]:
            existing.pop(old_field, None)

        # 3) apprendre / merger les patterns
        new_patterns = learn_supplier_patterns(
            ocr_text,
            iban=iban,
            bic=bic,
            invoice_number=self.invoice_number_input.text().strip(),
            invoice_date=self.date_input.text().strip(),
            vat_lines=vat_lines,
        )
        merged = merge_patterns(existing.get("patterns") or {}, new_patterns)

        folders = self.get_folder_numbers()

        # 4) construire data
        data = dict(existing)
        data.update({
            "supplier_key": supplier_key,
            "iban": iban,
            "bic": bic,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "patterns": merged,
            "model_version": 2,
        })

        # 5) sauver le fichier
        try:
            save_supplier_model(supplier_key, data)
            if show_message:
                QMessageBox.information(self, "Modèle transporteur", "Modèle transporteur sauvegardé / mis à jour.")
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

        # IBAN/BIC : valeur trouvée via patterns, sinon valeur stockée modèle
        self.iban_input.setText(found.get("iban") or model.get("iban", ""))

        if not self.bic_input.text().strip():
            self.bic_input.setText(found.get("bic") or model.get("bic", ""))

        cur = (self.invoice_number_input.text() or "").strip()
        is_date_like = bool(re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", cur)) or bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", cur))
        is_ok = bool(cur) and any(c.isdigit() for c in cur) and cur.upper() not in {"DESCRIPTION", "DATE", "FACTURE", "INVOICE"} and not is_date_like

        # si le modèle a trouvé un numéro de facture, on lui donne priorité
        invoice_number_from_model = (found.get("invoice_number") or "").strip()
        if invoice_number_from_model:
            self.invoice_number_input.setText(invoice_number_from_model)
        elif not is_ok:
            self.invoice_number_input.setText(invoice_number_from_model or "")

        if not self.date_input.text().strip():
            self.date_input.setText(found.get("invoice_date") or "")

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


    def _resolve_current_entry_id(self) -> str:
        """
        Retrouve l'entry_id courant de façon robuste.
        Priorité :
        1) selected_invoice_entry_id
        2) data déjà présent dans le JSON du document
        3) lookup SQL via nom du fichier
        """
        entry_id = str(getattr(self, "selected_invoice_entry_id", "") or "").strip()
        if entry_id:
            return entry_id

        pdf_path = str(getattr(self, "current_pdf_path", "") or "").strip()
        if not pdf_path:
            return ""

        # 1) JSON existant
        try:
            data = self._read_saved_invoice_json(pdf_path) or {}
            entry_id = str(data.get("entry_id") or "").strip()
            if entry_id:
                return entry_id
        except Exception:
            pass

        # 2) BDD via nom du fichier physique
        try:
            entry_id = str(
                self.logmail_repo.get_entry_id_for_file(os.path.basename(pdf_path)) or ""
            ).strip()
            if entry_id:
                return entry_id
        except Exception:
            pass

        return ""