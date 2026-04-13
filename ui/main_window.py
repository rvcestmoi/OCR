from __future__ import annotations

import re
import getpass
from .mainwindow.common import *
from .mainwindow.core_mixin import MainWindowCoreMixin
from .mainwindow.transport_tables_mixin import MainWindowTransportTablesMixin
from .mainwindow.documents_mixin import MainWindowDocumentsMixin
from .mainwindow.validation_mixin import MainWindowValidationMixin
from .mainwindow.cmr_mixin import MainWindowCmrMixin
from .mainwindow.links_mixin import MainWindowLinksMixin
from app.paths import PDF_INBOX_DIR
from db.xxare_repository import XXAReRepository
from db.lisinvoice_repository import LISInvoiceRepository


class MainWindow(
    MainWindowCoreMixin,
    MainWindowTransportTablesMixin,
    MainWindowDocumentsMixin,
    MainWindowValidationMixin,
    MainWindowCmrMixin,
    MainWindowLinksMixin,
    QMainWindow,
):    
    
    DEFAULT_PDF_FOLDER = PDF_INBOX_DIR

    def __init__(self):
        super().__init__()
        
        from db.connection import SqlServerConnection
        from db.config import DB_CONFIG
        from db.logmail_repository import LogmailRepository
        from db.transporter_repository import TransporterRepository
        from db.bank_repository import BankRepository
        from db.tour_repository import TourRepository
        from ui.ocr_text_view import OcrTextView
        from db.tour_repository import TourRepository
        from ocr.folder_patterns import DOSSIER_PATTERN, is_valid_folder_number
        
        self.current_folder_path: str | None = None
        self.pending_pool_size = 300
   
        self.current_username = getpass.getuser().strip()
        self._claimed_entry_id: str | None = None

        self.db_conn = SqlServerConnection(**DB_CONFIG)

        self.logmail_repo = LogmailRepository(self.db_conn)
        self.transporter_repo = TransporterRepository(self.db_conn)
        self.bank_repo = BankRepository(self.db_conn)
        self.tour_repo = TourRepository(self.db_conn)
        self.xxare_repo = XXAReRepository(self.db_conn)
        self.lisinvoice_repo = LISInvoiceRepository(self.db_conn)
        self.DOSSIER_PATTERN = DOSSIER_PATTERN
        # --- State ---
        self.current_pdf_path: str | None = None
        self.active_field: QLineEdit | None = None
        self.search_selections = []
        self.current_match_index = -1

        self.selected_kundennr: str | None = None
        self.current_db_iban: str | None = None
        self.current_db_bic: str | None = None
        self.bank_valid: bool | None = None
        self.selected_invoice_entry_id = None
        self.selected_invoice_filename = None
        self.transporter_selected_mode = False 
        # anti double-trigger (cellClicked + currentCellChanged)
        self._last_main_selected_path: str | None = None
        self._did_autoload_default_folder = False

        self._vat_theo_cache: dict[str, float | None] = {}
        self._ab_cache: dict[str, bool] = {}
        self._europal_cache: dict[str, bool] = {}
        self._pending_tags_to_add: set[str] = set()
        self._lkz_cache: dict[tuple[str, str], str] = {}

        

        # PDF "affiché" (peut être la facture ou une PJ)
        self.view_pdf_path: str | None = None

        # Groupe de PDFs (même entry_id)
        self.entry_pdf_paths: list[str] = []
        self.current_doc_index: int = 0

        # --- Window ---
        self.setWindowTitle("OCR Factures Fournisseurs")
        self.resize(1200, 800)

        # =========================
        # Widget central + layout
        # =========================
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(8)
        main_layout.addWidget(self.main_splitter)

        # =========================
        # Panneau gauche (splitter)
        # =========================
        left_widget = QWidget()
        left_root_layout = QVBoxLayout(left_widget)

        self.btn_scan_folder = QPushButton("📂 Analyser un dossier")
        self.btn_scan_folder.clicked.connect(self.select_folder)

        self.btn_ocr_all = QPushButton("⚙️ OCRiser")
        self.btn_ocr_all.clicked.connect(self.ocr_all_pdfs)

        self.lbl_last_mail_date = QLabel("Dernier mail : chargement…")
        self.lbl_last_mail_date.setWordWrap(True)
        self.lbl_last_mail_date.setStyleSheet("color: #666; padding: 2px 0 6px 2px;")

        # Splitter vertical : tableau en haut / infos transporteur en bas
        left_splitter = QSplitter(Qt.Vertical)

        # --- Haut gauche : filtres + recherche + tableau ---
        left_top_widget = QWidget()
        left_layout = QVBoxLayout(left_top_widget)

        left_layout.addWidget(self.btn_ocr_all)
        left_layout.addWidget(self.lbl_last_mail_date)

        self.pdf_table = QTableWidget(left_top_widget)
        self.pdf_table.setObjectName("pdf_table")
        # Cols: 0 Nom | 1 Date | 2 IBAN | 3 BIC | 4 Pays (LKZ)
        self.pdf_table.setColumnCount(5)
        self.pdf_table.setHorizontalHeaderLabels(["Nom du fichier", "Date", "IBAN", "BIC", "Pays"])

        hdr = self.pdf_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.pdf_table.setAlternatingRowColors(True)
        self.pdf_table.cellClicked.connect(self.on_pdf_selected)

        self.pdf_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pdf_table.customContextMenuRequested.connect(self.on_pdf_table_context_menu)
        self.pdf_table.currentCellChanged.connect(self._on_pdf_current_cell_changed)

        self.left_filter_mode = "pending"

        filter_bar = QHBoxLayout()

        self.btn_filter_pending = QPushButton("🕓 En attente")
        self.btn_filter_pending.setCheckable(True)
        self.btn_filter_pending.setChecked(True)

        self.btn_filter_validated = QPushButton("✅ Validés")
        self.btn_filter_validated.setCheckable(True)

        self.btn_filter_errors = QPushButton("⚠️ Erreurs")
        self.btn_filter_errors.setCheckable(True)

        self.btn_filter_ecart = QPushButton("🟠 Ecarts")
        self.btn_filter_ecart.setCheckable(True)

        # Filtre pays (LKZ) – ne filtre que sur la colonne "Pays"
        self.left_country_filter_input = QLineEdit()
        self.left_country_filter_input.setPlaceholderText("Pays (ex: FR)")
        self.left_country_filter_input.setClearButtonEnabled(True)
        self.left_country_filter_input.setMaximumWidth(110)
        self.left_country_filter_input.textChanged.connect(self.apply_left_table_search_filter)

        filter_bar.addStretch(1)
        filter_bar.addWidget(QLabel("Pays:"))
        filter_bar.addWidget(self.left_country_filter_input)

        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        self._filter_group.addButton(self.btn_filter_pending)
        self._filter_group.addButton(self.btn_filter_validated)
        self._filter_group.addButton(self.btn_filter_errors)
        self._filter_group.addButton(self.btn_filter_ecart)

        filter_bar.addWidget(self.btn_filter_pending)
        filter_bar.addWidget(self.btn_filter_validated)
        filter_bar.addWidget(self.btn_filter_errors)
        filter_bar.addWidget(self.btn_filter_ecart)
        filter_bar.addStretch(1)

        left_layout.addLayout(filter_bar)

        self.btn_filter_pending.clicked.connect(lambda: self.set_left_filter("pending"))
        self.btn_filter_validated.clicked.connect(lambda: self.set_left_filter("validated"))
        self.btn_filter_errors.clicked.connect(lambda: self.set_left_filter("errors"))
        self.btn_filter_ecart.clicked.connect(lambda: self.set_left_filter("ecart"))

        self.left_search_input = QLineEdit()
        self.left_search_input.setPlaceholderText("🔎 Rechercher fichier / date / IBAN / BIC / dossier…")
        self.left_search_input.textChanged.connect(self.on_left_search_text_changed)
        left_layout.addWidget(self.left_search_input)

        left_layout.addWidget(self.pdf_table)

        # --- Bas gauche : infos transporteur ---
        left_bottom_widget = QWidget()
        left_bottom_layout = QVBoxLayout(left_bottom_widget)
        left_bottom_layout.addWidget(QLabel("🚚 Informations transporteur"))

        self.transporter_info = QPlainTextEdit()
        self.transporter_info.setReadOnly(True)
        self.transporter_info.setPlaceholderText("Informations transporteur / banque…")
        self.transporter_info.setMinimumHeight(140)
        font = self.transporter_info.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        self.transporter_info.setFont(font)
        
        left_bottom_layout.addWidget(self.transporter_info)



        left_splitter.addWidget(left_top_widget)
        left_splitter.addWidget(left_bottom_widget)
        left_splitter.setStretchFactor(0, 4)
        left_splitter.setStretchFactor(1, 2)

        left_root_layout.addWidget(left_splitter)
        left_widget.setMinimumWidth(260)


        left_bottom_widget = QWidget()
        left_bottom_layout = QVBoxLayout(left_bottom_widget)
        left_bottom_layout.addWidget(QLabel("📎 Pièces jointes associées"))

        self.related_pdf_table = QTableWidget()
        self.related_pdf_table.setColumnCount(1)
        self.related_pdf_table.setHorizontalHeaderLabels(["Fichier lié"])
        self.related_pdf_table.horizontalHeader().setStretchLastSection(True)
        self.related_pdf_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.related_pdf_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.related_pdf_table.setAlternatingRowColors(True)
        self.related_pdf_table.cellClicked.connect(self.on_related_pdf_selected)
        self.related_pdf_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.related_pdf_table.customContextMenuRequested.connect(self.on_related_pdf_context_menu)

        left_bottom_layout.addWidget(self.related_pdf_table)



        # =========================
        # Panneau central (PDF)
        # =========================
        center_widget = QWidget()
        center_widget.setMinimumWidth(360)
        center_panel = QVBoxLayout(center_widget)

        # --- Barres navigation PDF ---
        pdf_nav = QHBoxLayout()
        pdf_tools_nav = QHBoxLayout()

        # ✅ navigation documents (même entry_id)
        self.btn_prev_doc = QPushButton("⏪")
        self.btn_next_doc = QPushButton("⏩")
        self.lbl_doc_info = QLabel("Doc 0 / 0")

        self.btn_attach_cmr = QPushButton("Rattacher CMR…")
        self.btn_attach_cmr.setToolTip("Rattacher le document affiché à un dossier (liste de droite)")
        self.btn_attach_cmr.clicked.connect(self.on_attach_cmr_main)

        self.btn_fetch_links = QPushButton("🔗 Télécharger liens…")
        self.btn_fetch_links.setToolTip("Télécharger les documents pointés par des liens dans le PDF actuellement affiché")
        self.btn_fetch_links.clicked.connect(self.on_fetch_links_main)


        self.btn_prev_doc.setToolTip("Document précédent")
        self.btn_next_doc.setToolTip("Document suivant")

        self.btn_prev_doc.clicked.connect(self.on_prev_doc)
        self.btn_next_doc.clicked.connect(self.on_next_doc)

        # ✅ navigation pages (dans le PDF)
        self.btn_prev_page = QPushButton("⏮")
        self.btn_next_page = QPushButton("⏭")
        self.lbl_page_info = QLabel("Page 0 / 0")

        self.btn_zoom_out = QPushButton("➖")
        self.btn_zoom_out.setToolTip("Zoom arrière")
        self.btn_fit_width = QPushButton("↔ Ajuster")
        self.btn_fit_width.setToolTip("Ajuster à la largeur")
        self.btn_zoom_in = QPushButton("➕")
        self.btn_zoom_in.setToolTip("Zoom avant")
        self.btn_rotate_left = QPushButton("↺")
        self.btn_rotate_left.setToolTip("Rotation gauche")
        self.btn_rotate_right = QPushButton("↻")
        self.btn_rotate_right.setToolTip("Rotation droite")
        self.lbl_view_info = QLabel("100% · 0°")

        self.btn_prev_page.clicked.connect(self.on_prev_page)
        self.btn_next_page.clicked.connect(self.on_next_page)
        self.btn_zoom_out.clicked.connect(self.on_zoom_out)
        self.btn_fit_width.clicked.connect(self.on_fit_width)
        self.btn_zoom_in.clicked.connect(self.on_zoom_in)
        self.btn_rotate_left.clicked.connect(self.on_rotate_left)
        self.btn_rotate_right.clicked.connect(self.on_rotate_right)

        pdf_nav.addStretch()
        pdf_nav.addWidget(self.btn_prev_doc)
        pdf_nav.addWidget(self.lbl_doc_info)
        pdf_nav.addWidget(self.btn_next_doc)

        pdf_nav.addSpacing(8)
        pdf_nav.addWidget(self.btn_attach_cmr)

        pdf_nav.addWidget(self.btn_fetch_links)


        pdf_nav.addSpacing(16)

        pdf_nav.addWidget(self.btn_prev_page)
        pdf_nav.addWidget(self.lbl_page_info)
        pdf_nav.addWidget(self.btn_next_page)

        pdf_nav.addStretch()

        pdf_tools_nav.addStretch()
        pdf_tools_nav.addWidget(self.btn_zoom_out)
        pdf_tools_nav.addWidget(self.btn_fit_width)
        pdf_tools_nav.addWidget(self.btn_zoom_in)
        pdf_tools_nav.addSpacing(8)
        pdf_tools_nav.addWidget(self.btn_rotate_left)
        pdf_tools_nav.addWidget(self.btn_rotate_right)
        pdf_tools_nav.addWidget(self.lbl_view_info)
        pdf_tools_nav.addStretch()

        center_panel.addLayout(pdf_nav)
        center_panel.addLayout(pdf_tools_nav)

        # --- Viewer ---
        self.pdf_viewer = PdfViewer()
        self.pdf_viewer.setMinimumSize(400, 400)
        self.pdf_viewer.text_selected.connect(self.fill_active_field)
        self.pdf_viewer.text_selected.connect(self.append_ocr_text)
        self.pdf_viewer.view_changed.connect(self.update_view_indicator)
        center_panel.addWidget(self.pdf_viewer)

        # --- Clic droit sur la zone PDF ---
        target = getattr(self.pdf_viewer, "label", self.pdf_viewer)
        target.setContextMenuPolicy(Qt.CustomContextMenu)
        target.customContextMenuRequested.connect(self.on_pdf_context_menu)

        # Stockage en mémoire des détails palettes (chargé depuis le JSON)
        self.pallet_details = {}

        self.block_options = {}   # { "nom_fichier.pdf": {"blocked": bool, "reason": str, "free_comment": str, "comment": str} }


        # --- Volet info dossier / tournée ---
        self.tour_info = QPlainTextEdit()
        self.tour_info.setReadOnly(True)
        self.tour_info.setMaximumHeight(140)
        self.tour_info.setPlaceholderText("Informations dossier / tournée…")
        center_panel.addWidget(self.tour_info)
        # =========================
        # Panneau droit (form)
        # =========================
        right_widget = QWidget()
        # Garde une largeur minimale confortable pour le formulaire et la table dossiers.
        right_widget.setMinimumWidth(420)
        right_panel = QVBoxLayout(right_widget)
        form_layout = QFormLayout()

        self.iban_input = QLineEdit()
        self.bic_input = QLineEdit()
        self.iban_input.editingFinished.connect(self.on_bank_fields_changed)
        self.bic_input.editingFinished.connect(self.on_bank_fields_changed)

        self.date_input = QLineEdit()
        self.invoice_number_input = QLineEdit()
        # ✅ contrôle anti-doublon (XXARe) dès qu'on quitte le champ
        self.invoice_number_input.editingFinished.connect(self.on_invoice_number_editing_finished)

        form_layout.addRow("IBAN :", self.iban_input)
        form_layout.addRow("BIC :", self.bic_input)

        # ----- Transporteur + completer -----
        self.transporter_input = QLineEdit()
        self.transporter_input.setPlaceholderText("Rechercher transporteur…")
        self.transporter_input.setClearButtonEnabled(True)

        self.transporter_model = QStringListModel()
        self.transporter_completer = QCompleter()
        self.transporter_completer.setModel(self.transporter_model)
        self.transporter_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.transporter_completer.setFilterMode(Qt.MatchContains)
        self.transporter_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.transporter_input.setCompleter(self.transporter_completer)

        self.transporter_input.textChanged.connect(self.search_transporters)
        self.transporter_completer.activated.connect(self.on_transporter_selected)

        self.btn_transporter_action = QPushButton("➡")
        self.btn_transporter_action.setFixedWidth(30)
        self.btn_transporter_action.clicked.connect(self.on_transporter_action)

        transporter_layout = QHBoxLayout()
        transporter_layout.addWidget(self.transporter_input)
        transporter_layout.addWidget(self.btn_transporter_action)
        transporter_layout.addStretch()
        form_layout.addRow("Trp :", transporter_layout)
        
        self.transporter_aux_input = QLineEdit()
        self.transporter_aux_input.setPlaceholderText("Compte auxiliaire")
        self.transporter_aux_input.setClearButtonEnabled(True)

        # par défaut : non modifiable (sera rendu modifiable si vide en base)
        self.transporter_aux_input.setReadOnly(True)
        self.transporter_aux_input.setFocusPolicy(Qt.NoFocus)
        self.transporter_aux_input.setStyleSheet("background-color: #f3f3f3;")

        form_layout.addRow("Cpte a.:", self.transporter_aux_input)

        # Nouveau champ expéditeur
        self.sender_input = QLineEdit()
        self.sender_input.setReadOnly(True)
        self.sender_input.setFocusPolicy(Qt.ClickFocus)  # Permet la sélection pour copie
        self.sender_input.setStyleSheet("background-color: #f3f3f3;")
        self.sender_input.setContextMenuPolicy(Qt.DefaultContextMenu)  # Menu contextuel par défaut pour copie

        form_layout.addRow("Expéditeur :", self.sender_input)

        form_layout.addRow("Date facture :", self.date_input)
        form_layout.addRow("N° facture :", self.invoice_number_input)

        # =========================
        # Table dossiers (N° dossier / Montant HT)
        # =========================
        self.folder_table = QTableWidget(0, 6)
        self.folder_table.setHorizontalHeaderLabels([
            "N° Tournée",
            "Montant HT",
            "TVA th. (%)",
            "CMR",
            "AB",
            ""  # colonne réservée
        ])

        folder_header = self.folder_table.horizontalHeader()
        folder_header.setMinimumSectionSize(28)

        # Colonne tournée : largeur minimale pour voir confortablement 9 chiffres.
        folder_header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.folder_table.setColumnWidth(0, 96)

        # Colonnes montant / TVA : volontairement plus compactes.
        folder_header.setSectionResizeMode(1, QHeaderView.Interactive)
        self.folder_table.setColumnWidth(1, 108)
        folder_header.setSectionResizeMode(2, QHeaderView.Interactive)
        self.folder_table.setColumnWidth(2, 92)

        # Indicateurs compacts.
        folder_header.setSectionResizeMode(3, QHeaderView.Fixed)
        folder_header.setSectionResizeMode(4, QHeaderView.Fixed)
        folder_header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.folder_table.setColumnWidth(3, 34)
        self.folder_table.setColumnWidth(4, 30)
        self.folder_table.setColumnWidth(5, 30)


        # Totaux dossiers
        self.lbl_folder_totals = QLabel("")
        self.lbl_folder_totals.setStyleSheet("padding:4px;")

        # =========================
        # TVA (table sous les dossiers)
        # =========================
        self.vat_table = QTableWidget(0, 3)
        self.vat_table.setHorizontalHeaderLabels(["Taux TVA (%)", "Base HT", "Montant TVA"])
        self.vat_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.vat_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.vat_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.vat_table.setAlternatingRowColors(True)
        self.vat_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.vat_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.vat_table.setMinimumHeight(50)
        self.vat_table.setMaximumHeight(100)

        self.lbl_vat_total = QLabel("")
        self.lbl_vat_total.setStyleSheet("padding:4px;")

        # =========================
        # Conteneur vertical (dossiers + totaux + TVA)
        # =========================
        folders_box = QWidget()
        self.folders_layout = QVBoxLayout(folders_box)
        self.folders_layout.setContentsMargins(0, 0, 0, 0)

        self.folders_layout.addWidget(self.folder_table)
        self.folders_layout.addWidget(self.lbl_folder_totals)

        self.folders_layout.addWidget(QLabel("TVA :"))
        self.folders_layout.addWidget(self.vat_table)
        self.folders_layout.addWidget(self.lbl_vat_total)

        form_layout.addRow("Dossiers :", folders_box)

        # Lignes vides permanentes
        self._ensure_empty_folder_row()
        self._ensure_empty_vat_row()

        # =========================
        # Gestion champ actif (PDF -> champ)
        # =========================
        self.FIELD_COLORS = {
            self.iban_input: QColor(100, 149, 237, 80),           # bleu
            self.bic_input: QColor(186, 85, 211, 80),             # violet
            self.date_input: QColor(60, 179, 113, 80),            # vert
            self.invoice_number_input: QColor(255, 215, 0, 80),   # jaune
        }

        # Boutons principaux
        self.btn_analyze_pdf = QPushButton("🔍 Analyser le PDF (OCR)")
        self.btn_analyze_pdf.clicked.connect(self.on_analyze_pdf_clicked)

        self.btn_deep_ocr = QPushButton("OCR profond")
        self.btn_deep_ocr.setToolTip("Force un OCR Tesseract et complète les informations déjà chargées sans écraser les champs remplis.")
        self.btn_deep_ocr.clicked.connect(self.on_analyze_pdf_deep_clicked)

        self.btn_save_data = QPushButton("💾 Sauvegarder")
        self.btn_save_data.clicked.connect(self.on_save_clicked)

        self.shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_save.setContext(Qt.ApplicationShortcut)
        self.shortcut_save.activated.connect(self.on_ctrl_s_save)

        self.shortcut_save_supplier = QShortcut(QKeySequence("Ctrl+M"), self)
        self.shortcut_save_supplier.setContext(Qt.ApplicationShortcut)
        self.shortcut_save_supplier.activated.connect(self.on_ctrl_m_save_supplier_model)

        self.shortcut_zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        self.shortcut_zoom_in.setContext(Qt.ApplicationShortcut)
        self.shortcut_zoom_in.activated.connect(self.on_zoom_in)

        self.shortcut_zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        self.shortcut_zoom_out.setContext(Qt.ApplicationShortcut)
        self.shortcut_zoom_out.activated.connect(self.on_zoom_out)

        self.shortcut_fit_width = QShortcut(QKeySequence("Ctrl+0"), self)
        self.shortcut_fit_width.setContext(Qt.ApplicationShortcut)
        self.shortcut_fit_width.activated.connect(self.on_fit_width)

        self.btn_validate = QPushButton("✅ Valider la facture (V)")
        self.btn_validate.clicked.connect(self.on_validate_invoice)
        
 
        self.shortcut_validate = QShortcut(QKeySequence("V"), self)
        self.shortcut_validate.setContext(Qt.ApplicationShortcut)
        self.shortcut_validate.activated.connect(self.on_validate_invoice)

        right_panel.addWidget(self.btn_save_data)
        right_panel.addWidget(self.btn_validate)

        #self.btn_save_supplier = QPushButton("⭐ Mettre à jour modèle fournisseur")
        #self.btn_save_supplier.clicked.connect(self.save_supplier_model)
        #right_panel.addWidget(self.btn_save_supplier)

        right_panel.addLayout(form_layout)
        right_panel.addStretch()
        right_panel.addWidget(self.btn_analyze_pdf)
        right_panel.addWidget(self.btn_deep_ocr)

        # Layout global via splitter horizontal
        self.main_splitter.addWidget(left_widget)
        self.main_splitter.addWidget(center_widget)
        self.main_splitter.addWidget(right_widget)
        self.main_splitter.setStretchFactor(0, 3)
        self.main_splitter.setStretchFactor(1, 5)
        self.main_splitter.setStretchFactor(2, 4)
        self.main_splitter.setSizes([320, 520, 420])

        # Champs cliquables
        for field in [self.iban_input, self.bic_input, self.date_input, self.invoice_number_input]:
            field.mousePressEvent = lambda e, f=field: self.set_active_field(f)
            field.textChanged.connect(lambda _, f=field: f.setStyleSheet(""))
            self.transporter_input.mousePressEvent = lambda e, f=self.transporter_input: self.set_active_field(f)
            self.transporter_input.textChanged.connect(lambda _: self.transporter_input.setStyleSheet(""))

        # =========================
        # Recherche dans texte OCR
        # =========================
        self.ocr_search_input = QLineEdit()
        self.ocr_search_input.setPlaceholderText("🔍 Rechercher dans le texte OCR…")
        self.ocr_search_input.textChanged.connect(self.search_in_ocr_text)
        right_panel.addWidget(self.ocr_search_input)

        # =========================
        # Zone OCR brut
        # =========================
        self.ocr_text_view = OcrTextView()
        self.ocr_text_view.setReadOnly(True)
        self.ocr_text_view.setPlaceholderText("Texte brut OCR (Tesseract / PDF)…")
        self.ocr_text_view.setMinimumHeight(200)
        right_panel.addWidget(QLabel("🧾 Texte OCR brut :"))
        right_panel.addWidget(self.ocr_text_view)

        self.ocr_text_view.assign_to_field.connect(self.assign_text_to_field)

        # =========================
        # Navigation recherche OCR
        # =========================
        nav_layout = QHBoxLayout()
        self.btn_prev_match = QPushButton("⬅️")
        self.btn_next_match = QPushButton("➡️")
        self.search_counter_label = QLabel("0 / 0")
        self.btn_prev_match.clicked.connect(self.goto_previous_match)
        self.btn_next_match.clicked.connect(self.goto_next_match)
        nav_layout.addWidget(self.btn_prev_match)
        nav_layout.addWidget(self.btn_next_match)
        nav_layout.addWidget(self.search_counter_label)
        nav_layout.addStretch()
        right_panel.addLayout(nav_layout)

        # =========================
        # Reactive arrow when edit
        # =========================
        self.iban_input.textChanged.connect(self.enable_transporter_update)
        self.bic_input.textChanged.connect(self.enable_transporter_update)
        self.transporter_input.textChanged.connect(self.enable_transporter_update)

        # Optionnel (mais utile) : état initial boutons doc
        self.btn_prev_doc.setEnabled(False)
        self.btn_next_doc.setEnabled(False)

        self.refresh_last_mail_date_label()

    def _format_last_mail_date_value(self, value) -> str | None:
        if value is None:
            return None

        if isinstance(value, datetime):
            return value.strftime("%d/%m/%Y %H:%M")

        raw = str(value).strip()
        if not raw:
            return None

        candidates = [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]
        for fmt in candidates:
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return raw

    def refresh_last_mail_date_label(self):
        label = getattr(self, "lbl_last_mail_date", None)
        if label is None:
            return

        try:
            value = self.logmail_repo.get_latest_mail_date()
            formatted = self._format_last_mail_date_value(value)
            if formatted:
                label.setText(f"Dernier mail : {formatted}")
            else:
                label.setText("Dernier mail : non disponible")
        except Exception:
            label.setText("Dernier mail : non disponible")

    def closeEvent(self, event):
        try:
            username = str(getattr(self, "current_username", "") or "").strip()
            if username:
                self.logmail_repo.release_all_entries_for_user(username)
        except Exception as e:
            print(f"[closeEvent] release_all_entries_for_user error: {e}")

        super().closeEvent(event)
