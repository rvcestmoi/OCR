from __future__ import annotations

from .common import *
from .workers import LinkDownloadWorker, LinkPostProcessWorker, _DownloadCanceled


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

        # 2) Fallback : lire les JSON d'un doc du même entry_id (utile si clic-droit sans ouvrir)
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
                    found[tournr] = {"tour_nr": tournr, "amount_ht_ocr": str(f.get("amount_ht_ocr") or "").strip()}

        return list(found.values())

    def attach_cmr_to_dossier_from_right_list(self, pdf_path: str, filename: str, entry_id: str | None = None):
        """
        Rattache un PDF (souvent une CMR) à un dossier (TourNr) du même entry_id.

        - Les choix de dossiers viennent du tableau de droite (si l'entry_id est celui affiché),
        sinon fallback : lecture des JSON des docs du même entry_id.
        - La popup n'affiche plus les montants : elle affiche Dossier / Trajet / VPE / Palettes / Poids.
        - On écrit dans le JSON du document : tag 'cmr', cmr_tour_nr, cmr_attached_at.
        """

        if not pdf_path:
            return
        if not filename:
            filename = os.path.basename(pdf_path)

        # ✅ priorité : entry_id déjà connu (fenêtre principale), sinon fallback BDD
        entry_id = (entry_id or self.selected_invoice_entry_id or self.logmail_repo.get_entry_id_for_file(filename))
        entry_id = (entry_id or "").strip()

        if not entry_id:
            QMessageBox.information(self, "Rattacher CMR", "Impossible de déterminer l'entry_id de ce document.")
            return

        # Liste des dossiers possibles
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

        # TourNr uniques (dans l'ordre)
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

        # Détails SQL (VPE / palettes / poids / trajet)
        details_rows = []
        try:
            # nécessite la méthode ajoutée dans TourRepository
            details_rows = self.tour_repo.get_palette_details_with_trajet_by_tournrs(tour_numbers) or []
        except Exception:
            details_rows = []

        dlg = FolderSelectDialog(tour_numbers, details_rows, parent=self, title="Rattacher CMR à une commande")
        if dlg.exec() != QDialog.Accepted or not dlg.selected_tour_nr or not dlg.selected_auf_nr:
            return

        tour_nr = str(dlg.selected_tour_nr).strip()
        auf_nr  = str(dlg.selected_auf_nr).strip()
        if not tour_nr:
            return

        # Si le document courant = celui qu'on rattache, on sauvegarde d'abord l'UI (optionnel mais safe)
        try:
            if self.current_pdf_path == pdf_path:
                self.save_current_data(show_message=False)
        except Exception:
            pass

        # --- Update JSON du document CMR ---
        json_path = self._get_saved_json_path(pdf_path)
        existing = self._read_saved_invoice_json(pdf_path) or {}

        # tags -> ajouter "cmr"
        tags = existing.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []
        tags_set = {str(t).strip() for t in tags if str(t).strip()}
        tags_set.add("cmr")
        existing["tags"] = sorted(tags_set)

        # rattachement
        existing["entry_id"] = entry_id
        existing["cmr_tour_nr"] = tour_nr
        existing["cmr_attached_at"] = datetime.now().isoformat(timespec="seconds")
        existing["cmr_auf_nr"] = auf_nr

        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # --- Refresh UI gauche (ligne fichier) ---
        for r in range(self.pdf_table.rowCount()):
            it0 = self.pdf_table.item(r, 0)
            if it0 and it0.data(Qt.UserRole) == pdf_path:
                it0.setToolTip(f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}")
                self.statusBar().showMessage(f"CMR rattachée au dossier {tour_nr} / commande {auf_nr}.", 2500)
                break

        self.apply_left_filter_to_table()
        self.statusBar().showMessage(f"CMR rattachée au dossier {tour_nr}.", 2500)

        # --- Refresh icônes CMR (table dossiers à droite) si on est sur le même entry affiché ---
        try:
            if self.selected_invoice_entry_id and self.selected_invoice_entry_id.strip() == entry_id:
                for r in range(self.folder_table.rowCount()):
                    self._update_folder_row_status(r)
        except Exception:
            pass

        # Optionnel : refresh volet tour si tu en as un affiché
        try:
            if getattr(self, "last_loaded_tour_nr", None):
                self.load_tour_information(self.last_loaded_tour_nr)
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
        # Le document réellement affiché peut être view_pdf_path (navigation doc)
        pdf_path = self.view_pdf_path or self.current_pdf_path
        if not pdf_path or not os.path.exists(pdf_path):
            QMessageBox.information(self, "Rattacher CMR", "Aucun document affiché.")
            return

        filename = os.path.basename(pdf_path)

        # On passe entry_id si déjà connu (plus rapide)
        entry_id = self.selected_invoice_entry_id
        self.attach_cmr_to_dossier_from_right_list(pdf_path, filename, entry_id=entry_id)

    def _collect_cmr_attachments_for_current_entry(self) -> list[dict]:
        """
        Construit la liste des CMR rattachées (depuis les JSON des docs du même entry_id).
        Stocké dans le JSON de la facture sous 'cmr_attachments'.
        """
        out: list[dict] = []
        seen = set()

        paths = self.entry_pdf_paths or []
        for p in paths:
            # on exclut la facture elle-même (current_pdf_path) par sécurité
            if self.current_pdf_path and os.path.abspath(p) == os.path.abspath(self.current_pdf_path):
                continue

            data = self._read_saved_invoice_json(p) or {}
            tour_nr = str(data.get("cmr_tour_nr") or "").strip()

            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            tags_norm = {str(t).strip().lower() for t in tags if str(t).strip()}

            # on considère "CMR" si tag cmr OU cmr_tour_nr rempli
            if "cmr" not in tags_norm and not tour_nr:
                continue

            fn = os.path.basename(p)
            key = (fn, tour_nr)
            if key in seen:
                continue
            seen.add(key)

            out.append({
                "filename": fn,
                "tour_nr": tour_nr,
                "attached_at": str(data.get("cmr_attached_at") or "").strip()
            })

        return out

    def _get_current_invoice_tours(self) -> set[str]:
        """TourNr présents dans le tableau de droite (dossiers)."""
        tours = set()
        for f in (self.get_folder_rows() or []):
            t = str(f.get("tour_nr") or "").strip()
            if t:
                tours.add(t)
        return tours

    def _get_cmr_attached_tours_for_entry(self) -> set[str]:
        """TourNr qui ont au moins une CMR rattachée (via JSON des docs du même entry_id)."""
        tours = set()
        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}
            t = str(data.get("cmr_tour_nr") or "").strip()
            if t:
                tours.add(t)
        return tours

    def _get_cmr_attached_orders_for_entry(self) -> dict[str, set[str]]:
        """
        Retourne les commandes CMR rattachées pour l'entry courant :
        {tour_nr: set(auf_nr)}

        Compatibilité :
        - si une ancienne CMR n'a pas de cmr_auf_nr mais a un cmr_tour_nr,
          on la compte dans _cmr_legacy_cache pour le fallback de validation.
        """
        attached = defaultdict(set)
        legacy = defaultdict(int)

        for p in (self.entry_pdf_paths or []):
            data = self._read_saved_invoice_json(p) or {}

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
        """
        Retourne (ok, missing_tours).
        ok = True si tous les TourNr présents dans le tableau de droite ont au moins une CMR rattachée.
        """
        invoice_tours = self._get_current_invoice_tours()  # set[str] depuis tableau de droite
        if not invoice_tours:
            # s'il n'y a aucun dossier, on ne bloque pas ici (tu as peut-être déjà d'autres règles)
            return True, []

        cmr_tours = self._get_cmr_attached_tours_for_entry()  # set[str] depuis JSON CMR
        missing = sorted(invoice_tours - cmr_tours)
        return (len(missing) == 0), missing

    def _get_required_orders_by_tour(self, tours: set[str]) -> dict[str, set[str]]:
        """Retour: {tour_nr -> set(auf_nr)} depuis la BDD (via get_palette_details_with_trajet_by_tournrs)."""
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
            auf  = str(r.get("AufNr") or "").strip()
            if tour and auf:
                req[tour].add(auf)

        self._req_orders_cache_key = key
        self._req_orders_cache = dict(req)
        return self._req_orders_cache

    def _check_all_orders_have_cmr(self) -> tuple[bool, dict[str, list[str]]]:
        """
        ok=True si toutes les commandes (AufNr) de tous les dossiers ont une CMR.
        Retourne missing_by_tour = {tour_nr: [auf_nr, ...]}
        """
        invoice_tours = self._get_current_invoice_tours()
        if not invoice_tours:
            return True, {}

        required = self._get_required_orders_by_tour(invoice_tours)
        attached = self._get_cmr_attached_orders_for_entry()
        legacy = getattr(self, "_cmr_legacy_cache", {}) or {}

        missing_by_tour = {}

        for tour in sorted(invoice_tours):
            req = set(required.get(tour, set()))
            att = set(attached.get(tour, set()))

            # compat: si on a une CMR "ancienne" sans auf_nr et qu'il n'y a qu'UNE commande, on considère OK
            if not att and legacy.get(tour, 0) > 0 and len(req) == 1:
                att = set(req)

            if req:
                miss = sorted(req - att)
                if miss:
                    missing_by_tour[tour] = miss
            else:
                # pas de commandes trouvées en BDD -> on bloque (sinon validation fausse)
                missing_by_tour[tour] = ["(aucune commande trouvée en BDD)"]

        return (len(missing_by_tour) == 0), missing_by_tour

    def _block_validate_if_missing_cmr(self) -> bool:
        ok, missing_by_tour = self._check_all_orders_have_cmr()
        if ok:
            return True

        lines = []
        for tour, miss in missing_by_tour.items():
            lines.append(f"{tour}: {', '.join(miss)}")

        QMessageBox.warning(
            self,
            "Validation impossible",
            "Tous les dossiers doivent avoir une CMR pour CHAQUE commande.\n\n"
            "Commandes sans CMR :\n" + "\n".join(lines)
        )
        return False

    def relink_left_document_to_other_group(self, row: int):
        it0 = self.pdf_table.item(row, 0)
        if not it0:
            return

        # --- source: choisir quel PDF du groupe on déplace ---
        group_paths = it0.data(Qt.UserRole + 5)
        if isinstance(group_paths, (list, tuple)) and group_paths:
            src_paths = [p for p in group_paths if p and os.path.exists(p)]
        else:
            p = it0.data(Qt.UserRole)
            src_paths = [p] if p and os.path.exists(p) else []

        if not src_paths:
            QMessageBox.information(self, "Regrouper", "Impossible de retrouver le fichier source.")
            return

        # si plusieurs docs dans le groupe, on laisse choisir lequel déplacer
        if len(src_paths) > 1:
            labels = [f"{i+1}) {os.path.basename(p)}" for i, p in enumerate(src_paths)]
            default_idx = 0
            # si le PDF affiché fait partie du groupe, on le pré-sélectionne
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

        # --- cible: choisir un autre fichier (donc un autre entry_id) ---
        candidates = []
        targets = []

        for r in range(self.pdf_table.rowCount()):
            it = self.pdf_table.item(r, 0)
            if not it:
                continue

            target_entry = str(it.data(Qt.UserRole + 4) or "").strip()  # ✅ entry_id
            target_path = it.data(Qt.UserRole)
            if not target_entry or not target_path:
                continue

            # exclure le même groupe
            if src_entry_id and target_entry == src_entry_id:
                continue

            rep_name = os.path.basename(str(target_path))
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

        # --- UPDATE base: regrouper en base ---
        try:
            self.logmail_repo.set_entry_id_for_file(src_name, target_entry_id)
        except Exception as e:
            QMessageBox.warning(self, "Regrouper", f"Erreur SQL:\n{e}")
            return

        # --- UPDATE JSON local: entry_id ---
        try:
            data = self._read_saved_invoice_json(src_path) or {}
            data["entry_id"] = target_entry_id
            json_path = self._get_saved_json_path(src_path)
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            # pas bloquant
            pass

        # --- refresh UI ---
        try:
            self.load_folder(os.path.dirname(src_path))
        except Exception:
            pass

        self.statusBar().showMessage(f"{src_name} rattaché au groupe {target_entry_id}.", 3000)

