from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled
import fitz


class MainWindowCmrMixin:
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
        links = {int(x.get("page", 0) or 0): x for x in self._get_cmr_page_links(pdf_path)}

        if page_count <= 0:
            return ""

        lines = []
        for page_no in range(1, page_count + 1):
            link = links.get(page_no)
            if link:
                lines.append(
                    f"Page {page_no} → tournée {link.get('tour_nr', '')} / commande {link.get('auf_nr', '')}"
                )
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

        existing_page_link = self._get_cmr_page_link(pdf_path, page_no)
        if existing_page_link:
            resp = QMessageBox.question(
                self,
                "Rattachement CMR",
                f"La page {page_no} est déjà rattachée à la tournée "
                f"{existing_page_link.get('tour_nr', '')} / commande {existing_page_link.get('auf_nr', '')}.\n\n"
                "Remplacer ce rattachement ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return

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
        existing["cmr_attached_at"] = datetime.now().isoformat(timespec="seconds")

        # Sauvegarde page -> tournée / commande
        links = existing.get("cmr_page_links")
        if not isinstance(links, list):
            links = []

        links = [x for x in links if int(x.get("page", 0) or 0) != int(page_no)]
        links.append(
            {
                "page": int(page_no),
                "tour_nr": tour_nr,
                "auf_nr": auf_nr,
                "attached_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        links.sort(key=lambda x: int(x.get("page", 0) or 0))
        existing["cmr_page_links"] = links

        # Compat ancienne logique: on conserve aussi la dernière page rattachée
        existing["cmr_tour_nr"] = tour_nr
        existing["cmr_auf_nr"] = auf_nr

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # Refresh UI gauche
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                if page_count > 1:
                    it0.setToolTip(f"CMR page {page_no} rattachée au dossier {tour_nr} / commande {auf_nr}")
                else:
                    it0.setToolTip(f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}")
                break

        if page_count > 1:
            self.statusBar().showMessage(
                f"CMR page {page_no} rattachée au dossier {tour_nr} / commande {auf_nr}.",
                3000
            )
        else:
            self.statusBar().showMessage(
                f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}.",
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

    def _choose_representative_pdf(self, group_paths: list[str]) -> str:
        """
        Choisit le meilleur PDF pour représenter un entry_id.
        Priorité :
        1) JSON avec iban+bic + au moins un dossier (TourNr)
        2) JSON avec iban+bic
        3) premier fichier
        """
        if not group_paths:
            return ""

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


    def _collect_cmr_attachments_for_current_entry(self) -> list[dict]:
        """
        Construit la liste des CMR rattachées (depuis les JSON des docs du même entry_id).
        Version page-aware.
        """
        out: list[dict] = []
        seen = set()

        paths = self.entry_pdf_paths or []
        for p in paths:
            if self.current_pdf_path and os.path.abspath(p) == os.path.abspath(self.current_pdf_path):
                continue

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

                    key = (os.path.basename(p), page_no, tour_nr, auf_nr)
                    if key in seen:
                        continue
                    seen.add(key)

                    out.append(
                        {
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

            fn = os.path.basename(p)
            key = (fn, 0, tour_nr)
            if key in seen:
                continue
            seen.add(key)

            out.append(
                {
                    "filename": fn,
                    "page": 0,
                    "tour_nr": tour_nr,
                    "auf_nr": str(data.get("cmr_auf_nr") or "").strip(),
                    "attached_at": str(data.get("cmr_attached_at") or "").strip(),
                }
            )

        return out

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
        print(required)
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

        try:
            for info in (block_options or {}).values():
                if _matches_cmr_missing(info):
                    return True
        except Exception:
            pass

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
        """
        pdf_path = str(pdf_path or "").strip()
        target_dir = str(target_dir or "").strip()
        if not pdf_path or not os.path.exists(pdf_path) or not target_dir:
            return {}

        links = self._get_cmr_page_links(pdf_path)
        if not isinstance(links, list) or not links:
            return {}

        os.makedirs(target_dir, exist_ok=True)
        doc = fitz.open(pdf_path)
        try:
            split_paths: dict[int, str] = {}
            src_name = os.path.basename(pdf_path)
            base_name, ext = os.path.splitext(src_name)

            for link in links:
                page_no = int(link.get("page", 0) or 0)
                if page_no <= 0 or page_no > doc.page_count:
                    continue

                tour_nr = re.sub(r"[^0-9A-Za-z_-]", "", str(link.get("tour_nr") or "").strip())
                auf_nr = re.sub(r"[^0-9A-Za-z_-]", "", str(link.get("auf_nr") or "").strip())
                suffix = [f"CMR_p{page_no:02d}"]
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
                    new_data["cmr_page_links"] = [link]
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
            return links
        return []

    def _save_cmr_page_link(self, pdf_path: str, page_no: int, tour_nr: str, auf_nr: str):
        data = self._read_saved_invoice_json(pdf_path) or {}
        links = data.get("cmr_page_links")
        if not isinstance(links, list):
            links = []

        new_links = [x for x in links if int(x.get("page", 0) or 0) != int(page_no)]
        new_links.append(
            {
                "page": int(page_no),
                "tour_nr": str(tour_nr or "").strip(),
                "auf_nr": str(auf_nr or "").strip(),
                "attached_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        new_links.sort(key=lambda x: int(x.get("page", 0) or 0))

        data["cmr_page_links"] = new_links
        data["cmr_tour_nr"] = str(tour_nr or "").strip()
        data["cmr_auf_nr"] = str(auf_nr or "").strip()

        self._write_saved_invoice_json(pdf_path, data)

    def _get_cmr_page_link(self, pdf_path: str, page_no: int) -> dict | None:
        for link in self._get_cmr_page_links(pdf_path):
            if int(link.get("page", 0) or 0) == int(page_no):
                return link
        return None