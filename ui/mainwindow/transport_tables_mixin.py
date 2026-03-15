from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled


class MainWindowTransportTablesMixin:

    def on_prev_page(self):
        self.pdf_viewer.previous_page()
        self.update_page_indicator()

    def on_next_page(self):
        self.pdf_viewer.next_page()
        self.update_page_indicator()

    def update_page_indicator(self):
        total = self.pdf_viewer.page_count()
        if total == 0:
            self.lbl_page_info.setText("0 / 0")
            return
        current = self.pdf_viewer.current_page_index() + 1
        self.lbl_page_info.setText(f"Page {current} / {total}")
        self.btn_prev_page.setEnabled(current > 1)
        self.btn_next_page.setEnabled(current < total)

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

        if not iban or not bic:
            return

        record = self.bank_repo.find_by_iban_bic(iban, bic)
        if record:
            self.bank_valid = True
            self.iban_input.setStyleSheet("background-color: #e6ffe6;")
            self.bic_input.setStyleSheet("background-color: #e6ffe6;")
        else:
            self.bank_valid = False
            self.iban_input.setStyleSheet("background-color: #fff3cd;")
            self.bic_input.setStyleSheet("background-color: #fff3cd;")


    def load_transporter_information(self, force_by_kundennr: bool = False):
        try:
            self.transporter_info.clear()

            kundennr = ""
            transporter = None

            if force_by_kundennr:
                kundennr = str(getattr(self, "selected_kundennr", "") or "").strip()
                if not kundennr:
                    self.transporter_info.setPlainText("ℹ️ Aucun transporteur sélectionné.")
                    return

                transporter = self.transporter_repo.find_transporter_by_kundennr(kundennr)

            else:
                iban = self.iban_input.text().strip()
                bic = self.bic_input.text().strip()

                if not iban or not bic:
                    self.transporter_info.setPlainText("ℹ️ Aucun IBAN/BIC renseigné.")
                    return

                transporter = self.transporter_repo.find_transporter_by_bank(iban, bic)
                if transporter:
                    kundennr = str(transporter.get("KundenNr") or "").strip()
                    self.selected_kundennr = kundennr
                else:
                    self.selected_kundennr = None

            if not transporter:
                if force_by_kundennr and kundennr:
                    self.transporter_info.setPlainText(f"❌ Transporteur introuvable : {kundennr}")
                else:
                    self.transporter_info.setPlainText("❌ Aucun transporteur trouvé pour cet IBAN / SWIFT.")
                return

            if not kundennr:
                kundennr = str(transporter.get("KundenNr") or "").strip()

            self.selected_kundennr = kundennr

            transporter_name = str(transporter.get("name1", "") or "").strip()
            vat_no = str(transporter.get("USTIDNR", "") or "").strip()

            if transporter_name and kundennr:
                self.transporter_input.setText(f"{transporter_name} ({kundennr})")
            elif transporter_name:
                self.transporter_input.setText(transporter_name)
            elif kundennr:
                self.transporter_input.setText(kundennr)

            #self.transporter_vat_input.setText(vat_no)

            banks = self.bank_repo.get_all_bank_infos_by_kundennr(kundennr)

            lines = []
            lines.append(f"Transporteur : {str(transporter.get('name1', '') or '').strip()}")

            address_line = [
                str(transporter.get("Strasse", "") or "").strip(),
                str(transporter.get("PLZ", "") or "").strip(),
                str(transporter.get("Ort", "") or "").strip(),
                str(transporter.get("LKZ", "") or "").strip(),
            ]
            address_line = [p for p in address_line if p]

            ustid = str(transporter.get("UstId", "") or "").strip()

            if address_line:
                lines.append("Adresse : " + ", ".join(address_line))

            if ustid:
                lines.append(f"N°TVA : {ustid}")

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

            # mémorise la banque actuellement affichée pour le bouton de mise à jour
            if force_by_kundennr:
                first_bank = banks[0] if banks else {}
                self.current_db_iban = str(first_bank.get("iban", "") or "").strip()
                self.current_db_bic = str(first_bank.get("bic", "") or "").strip()
            else:
                self.current_db_iban = str(transporter.get("IBAN", "") or "").strip()
                self.current_db_bic = str(transporter.get("SWIFT", "") or "").strip()

        except Exception as e:
            self.transporter_info.setPlainText(f"Erreur chargement transporteur :\n{e}")



    def on_bank_fields_changed(self):
        self.check_bank_information()
        self.load_transporter_information()

    def search_transporters(self, text: str):
        # si déjà format "Name (123)" on ne relance pas de recherche
        if "(" in text and ")" in text:
            return
        if len(text.strip()) < 2:
            self.transporter_model.setStringList([])
            return

        try:
            rows = self.transporter_repo.search_transporters_by_name(text.strip())
            suggestions = [f"{r['name1']} ({r['kundennr']})" for r in rows]
            self.transporter_model.setStringList(suggestions)
        except Exception as e:
            print("Erreur recherche transporteur:", e)

    def on_transporter_selected(self, text: str):
        self.transporter_input.setText(text)

        if "(" in text and ")" in text:
            self.selected_kundennr = text.split("(")[-1].replace(")", "").strip()
        else:
            self.selected_kundennr = None

        # ✅ On passe en mode "transporteur choisi"
        self.transporter_selected_mode = bool(self.selected_kundennr)

        # ✅ Charger le transporteur par KundenNr (pas par IBAN/BIC)
        self.load_transporter_information(force_by_kundennr=True)

        self.enable_transporter_update()
        # ✅ si un n° de facture est déjà saisi, on vérifie tout de suite le doublon
        try:
            self._maybe_prompt_duplicate_invoice()
        except Exception:
            pass

    def on_transporter_action(self):
        if not self.selected_kundennr:
            return

        kundennr = self.selected_kundennr
        new_iban = self.iban_input.text().strip()
        new_bic = self.bic_input.text().strip()

        old_record = self.transporter_repo.get_bank_by_kundennr(kundennr)
        old_iban = old_record.get("IBAN", "") if old_record else ""
        old_bic = old_record.get("SWIFT", "") if old_record else ""

        msg = QMessageBox(self)
        msg.setWindowTitle("Mise à jour banque")
        msg.setText(
            "Voulez-vous mettre à jour les coordonnées bancaires ?\n\n"
            f"Ancien IBAN : {old_iban}\n"
            f"Ancien BIC  : {old_bic}\n\n"
            f"Nouveau IBAN : {new_iban}\n"
            f"Nouveau BIC  : {new_bic}"
        )
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() == QMessageBox.Yes:
            self.transporter_repo.update_bank(kundennr, new_iban, new_bic)

            # IMPORTANT: on ne relance PAS load_transporter_information() ici,
            # sinon ça risque de re-lire la BDD (pas encore commit/latence) et
            # de recalculer un état qui te “regrise”.
            self.current_db_iban = new_iban
            self.current_db_bic = new_bic

            QMessageBox.information(self, "Succès", "Coordonnées mises à jour.")

        self.enable_transporter_update()

    def enable_transporter_update(self):
        new_iban = self.iban_input.text().strip()
        new_bic = self.bic_input.text().strip()

        if not self.selected_kundennr:
            self.btn_transporter_action.setEnabled(False)
            return

        # Activer uniquement si modif réelle par rapport aux valeurs de référence
        base_iban = (self.current_db_iban or "").strip()
        base_bic = (self.current_db_bic or "").strip()

        if new_iban and new_bic and (new_iban != base_iban or new_bic != base_bic):
            self.btn_transporter_action.setEnabled(True)
        else:
            self.btn_transporter_action.setEnabled(False)

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
                invoice_filename = it.text().strip()
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

    def update_folder_totals(self):
        rows = self.get_folder_rows()

        tour_nrs = [r["tour_nr"] for r in rows if r.get("tour_nr")]
        kosten_map = self.tour_repo.get_kosten_by_tournrs(tour_nrs) if tour_nrs else {}

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

    def _add_folder_row(self, dossier: str = "", amount: str = "", vat_theo: str = ""):
        row = self.folder_table.rowCount()
        self.folder_table.insertRow(row)

        dossier_le = self._make_folder_cell("Numéro de dossier")
        amount_le = self._make_folder_cell("Montant HT (OCR)")

        vat_theo_le = self._make_folder_cell("TVA théorique (%)")
        vat_theo_le.setReadOnly(True)
        vat_theo_le.setFocusPolicy(Qt.NoFocus)
        vat_theo_le.setStyleSheet("background-color: #f3f3f3;")

        cmr_lbl = QLabel("")
        cmr_lbl.setAlignment(Qt.AlignCenter)
        cmr_lbl.setToolTip("CMR OK ?")
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

        dossier_le.mousePressEvent = lambda e, f=dossier_le: self.set_active_field(f)
        amount_le.mousePressEvent = lambda e, f=amount_le: self.set_active_field(f)

        dossier_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        amount_le.textChanged.connect(lambda _=None, r=row: self._on_folder_row_changed(r))
        dossier_le.editingFinished.connect(self.compact_folder_rows)
        amount_le.editingFinished.connect(self.compact_folder_rows)

        self.folder_table.setCellWidget(row, 0, dossier_le)
        self.folder_table.setCellWidget(row, 1, amount_le)
        self.folder_table.setCellWidget(row, 2, vat_theo_le)

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
                ok, missing_by_tour = self._check_all_orders_have_cmr()
                required = self._get_required_orders_by_tour({tour_nr})
                attached = self._get_cmr_attached_orders_for_entry()

                req = required.get(tour_nr, set())
                att = attached.get(tour_nr, set())

                if not tour_nr:
                    cmr_lbl.setText("")
                    cmr_lbl.setToolTip("")
                elif not req:
                    cmr_lbl.setText("🧾❓")
                    cmr_lbl.setToolTip("Aucune commande (AufNr) trouvée en BDD pour ce dossier.")
                elif req.issubset(att):
                    cmr_lbl.setText("🧾✅")
                    cmr_lbl.setToolTip(f"Toutes les commandes ont une CMR ({len(req)}/{len(req)}).")
                elif len(att) > 0:
                    miss = sorted(req - att)
                    cmr_lbl.setText("🧾⚠️")
                    cmr_lbl.setToolTip(f"CMR partielle: {len(att)}/{len(req)}. Manque: {', '.join(miss[:10])}" + ("..." if len(miss) > 10 else ""))
                else:
                    cmr_lbl.setText("🧾❌")
                    cmr_lbl.setToolTip(f"Aucune CMR sur les commandes. Attendu: {len(req)} commande(s).")



        amount_ocr = self._parse_amount(amount_le.text())

        dossier_le.setStyleSheet("")
        amount_le.setStyleSheet("")
        amount_le.setToolTip("")

        # ligne vide => neutre
        if not tour_nr:
            vat_theo_le.setText("")
            vat_theo_le.setToolTip("")
            return
        
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
            db_kosten = self.tour_repo.get_kosten_by_tournr(tour_nr)
        except Exception as e:
            amount_le.setStyleSheet("background-color: #ffe6e6;")
            amount_le.setToolTip(f"Erreur BDD: {e}")
            return

        if db_kosten is None:
            dossier_le.setStyleSheet("background-color: #ffe6e6;")
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
        rate_le.mousePressEvent = lambda e, f=rate_le: self.set_active_field(f)
        base_le.mousePressEvent = lambda e, f=base_le: self.set_active_field(f)
        vat_le.mousePressEvent  = lambda e, f=vat_le: self.set_active_field(f)

        # changements => total + ligne vide
        rate_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))
        base_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))
        vat_le.textChanged.connect(lambda _=None, r=row: self._on_vat_row_changed(r))

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

