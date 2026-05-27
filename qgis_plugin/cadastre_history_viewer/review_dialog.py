from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QComboBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QPushButton, QHeaderView, QMessageBox
)
from qgis.PyQt.QtGui import QColor, QBrush, QFont
from qgis.PyQt.QtCore import Qt, QObject, QEvent


class _ClickFilter(QObject):
    """Captures whether a row was already selected BEFORE the click, not after."""
    def __init__(self, table, callback):
        super().__init__(table)
        self._table = table
        self._cb    = callback

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.MouseButtonPress and
                event.button() == Qt.LeftButton):
            idx = self._table.indexAt(event.pos())
            if idx.isValid():
                self._cb(self._table.selectionModel().isSelected(idx))
        return False  # never consume the event
from qgis.gui import QgsRubberBand
from qgis.core import (
    QgsGeometry, QgsWkbTypes, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsProject
)
from . import db

# Rubber band colours
_RB_PENDING  = QColor(255, 165,   0, 191)   # orange  — pending draft geometry
_RB_APPROVED = QColor( 76, 175,  80, 191)   # green   — approved draft geometry
_RB_REJECTED = QColor(244,  67,  54, 191)   # red     — rejected draft geometry
_RB_STROKE   = {
    'pending':  QColor(200, 100,   0, 230),
    'approved': QColor(  0, 120,   0, 230),
    'rejected': QColor(180,   0,   0, 230),
}
_RB_CURR_FILL   = QColor(100, 149, 237, 160)  # blue — current active (pending only)
_RB_CURR_STROKE = QColor(  0,  80, 200, 230)

# Row background per status
_STATUS_BG = {
    'pending':  QColor(255, 243, 205),
    'approved': QColor(210, 245, 210),
    'rejected': QColor(255, 220, 220),
}

_STATUS_OPTIONS = [
    ('Pending',  'pending'),
    ('Approved', 'approved'),
    ('Rejected', 'rejected'),
    ('All',      'all'),
]


class ReviewDraftsDialog(QDialog):

    def __init__(self, iface, canvas):
        super().__init__(iface.mainWindow())
        self.iface             = iface
        self.canvas            = canvas
        self.managers          = []
        self.drafts            = []
        self.draft_cols        = []
        self.rubber_bands      = []
        self._deselect_pending = False

        self.setWindowTitle("Review Drafts")
        self.setMinimumWidth(760)
        self.setMinimumHeight(460)

        self._load_managers()
        self._load_drafts('pending')
        self._build_ui()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _load_managers(self):
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, username FROM cadaster.users "
                    "WHERE role = 'manager' ORDER BY username"
                )
                self.managers = cur.fetchall()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self.iface.mainWindow(), "Cadastre", f"DB error:\n{e}")

    def _load_drafts(self, status_filter):
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                where = "" if status_filter == 'all' else "WHERE d.status = %s"
                params = () if status_filter == 'all' else (status_filter,)
                cur.execute(f"""
                    SELECT d.draft_id, d.parcel_id, d.action, d.owner_name,
                           u.username AS submitted_by_username,
                           d.submitted_at, d.status, d.notes,
                           ST_AsText(d.geom) AS geom_wkt
                    FROM cadaster.draft_parcels d
                    JOIN cadaster.users u ON u.user_id = d.submitted_by
                    {where}
                    ORDER BY d.submitted_at DESC
                """, params)
                self.draft_cols = [d[0] for d in cur.description]
                self.drafts     = cur.fetchall()
            conn.close()
        except Exception as e:
            QMessageBox.critical(self.iface.mainWindow(), "Cadastre", f"DB error:\n{e}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Top row: reviewer + status filter
        top_row = QHBoxLayout()

        reviewer_form = QFormLayout()
        self.combo_reviewer = QComboBox()
        for uid, uname in self.managers:
            self.combo_reviewer.addItem(uname, uid)
        reviewer_form.addRow("Reviewing as:", self.combo_reviewer)
        top_row.addLayout(reviewer_form)

        top_row.addSpacing(24)

        filter_form = QFormLayout()
        self.combo_status = QComboBox()
        for label, value in _STATUS_OPTIONS:
            self.combo_status.addItem(label, value)
        self.combo_status.currentIndexChanged.connect(self._on_filter_changed)
        filter_form.addRow("Show:", self.combo_status)
        top_row.addLayout(filter_form)
        top_row.addStretch()
        layout.addLayout(top_row)

        # Drafts table
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.selectionModel().selectionChanged.connect(self._on_row_selected)
        self.table.clicked.connect(self._on_row_clicked)
        self._click_filter = _ClickFilter(
            self.table,
            lambda was_sel: setattr(self, '_deselect_pending', was_sel)
        )
        self.table.viewport().installEventFilter(self._click_filter)
        layout.addWidget(self.table)
        self._fill_table()

        self.lbl_empty = QLabel("No drafts match the selected filter.")
        self.lbl_empty.setAlignment(Qt.AlignCenter)
        self.lbl_empty.setVisible(len(self.drafts) == 0)
        layout.addWidget(self.lbl_empty)

        # Draft notes (read-only)
        notes_grp = QGroupBox("Draft notes")
        notes_layout = QVBoxLayout(notes_grp)
        self.lbl_draft_notes = QLabel("—")
        self.lbl_draft_notes.setWordWrap(True)
        notes_layout.addWidget(self.lbl_draft_notes)
        layout.addWidget(notes_grp)

        # Review notes (manager input)
        review_grp = QGroupBox("Review notes  (optional — shown to editor on rejection)")
        review_layout = QVBoxLayout(review_grp)
        self.edit_review_notes = QTextEdit()
        self.edit_review_notes.setFixedHeight(50)
        review_layout.addWidget(self.edit_review_notes)
        layout.addWidget(review_grp)

        # Buttons
        btn_row = QHBoxLayout()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)

        self.btn_reject = QPushButton("Reject")
        self.btn_reject.setEnabled(False)
        self.btn_reject.clicked.connect(self._reject)

        self.btn_approve = QPushButton("Approve ✓")
        self.btn_approve.setEnabled(False)
        self.btn_approve.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold;"
        )
        self.btn_approve.clicked.connect(self._approve)

        btn_row.addWidget(btn_close)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_reject)
        btn_row.addWidget(self.btn_approve)
        layout.addLayout(btn_row)

    def _fill_table(self):
        headers = ["Draft ID", "Parcel", "Action", "Proposed Owner",
                   "Submitted By", "Date", "Status"]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(self.drafts))

        for row, draft in enumerate(self.drafts):
            d      = dict(zip(self.draft_cols, draft))
            status = d['status']
            bg     = _STATUS_BG.get(status, QColor(255, 255, 255))

            submitted_at = d['submitted_at']
            if hasattr(submitted_at, 'strftime'):
                date_str = submitted_at.strftime("%d-%m-%Y %H:%M")
            else:
                date_str = str(submitted_at)[:16] if submitted_at else "—"

            cells = [
                str(d['draft_id']),
                str(d['parcel_id']),
                d['action'].upper(),
                str(d['owner_name'] or '—'),
                str(d['submitted_by_username'] or '—'),
                date_str,
                status.upper(),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setBackground(QBrush(bg))
                if col == 6:                          # Status column — bold
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)

        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _on_filter_changed(self):
        status_filter = self.combo_status.currentData()
        self._reload(status_filter=status_filter)

    # ------------------------------------------------------------------
    # Row selection → rubber bands + deselect
    # ------------------------------------------------------------------

    def _on_row_clicked(self, index):
        if self._deselect_pending:
            self.table.clearSelection()
            self._deselect_pending = False

    def _on_row_selected(self, selected, deselected):
        self._clear_rubber_bands()
        rows = {idx.row() for idx in self.table.selectedIndexes()}

        if not rows:
            self.lbl_draft_notes.setText("—")
            self.btn_approve.setEnabled(False)
            self.btn_reject.setEnabled(False)
            return

        row    = next(iter(rows))
        draft  = dict(zip(self.draft_cols, self.drafts[row]))
        status = draft['status']
        action = draft['action']

        self.lbl_draft_notes.setText(draft.get('notes') or '—')

        is_pending = status == 'pending'
        self.btn_approve.setEnabled(is_pending)
        self.btn_reject.setEnabled(is_pending)

        # Draft geometry
        fill   = {'pending': _RB_PENDING, 'approved': _RB_APPROVED,
                  'rejected': _RB_REJECTED}.get(status, _RB_PENDING)
        stroke = _RB_STROKE.get(status, _RB_STROKE['pending'])

        if action in ('create', 'modify') and draft.get('geom_wkt'):
            self._add_rubber_band(draft['geom_wkt'], fill, stroke)

        # Current active geometry — only meaningful for pending modify/retire
        if is_pending and action in ('modify', 'retire'):
            try:
                conn = db.get_connection()
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT ST_AsText(geom) FROM cadaster.active_parcels "
                        "WHERE parcel_id = %s",
                        (draft['parcel_id'],)
                    )
                    row_data = cur.fetchone()
                conn.close()
                if row_data and row_data[0]:
                    self._add_rubber_band(row_data[0], _RB_CURR_FILL, _RB_CURR_STROKE)
            except Exception:
                pass

        self.canvas.refresh()

    # ------------------------------------------------------------------
    # Rubber bands
    # ------------------------------------------------------------------

    def _add_rubber_band(self, wkt, fill, stroke):
        geom = QgsGeometry.fromWkt(wkt)
        src  = QgsCoordinateReferenceSystem("EPSG:4326")
        dst  = self.canvas.mapSettings().destinationCrs()
        geom.transform(QgsCoordinateTransform(src, dst, QgsProject.instance()))

        rb = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        rb.setFillColor(fill)
        rb.setStrokeColor(stroke)
        rb.setWidth(2)
        rb.setToGeometry(geom, None)
        self.rubber_bands.append(rb)

    def _clear_rubber_bands(self):
        for rb in self.rubber_bands:
            rb.reset(QgsWkbTypes.PolygonGeometry)
            self.canvas.scene().removeItem(rb)
        self.rubber_bands.clear()
        self.canvas.refresh()

    # ------------------------------------------------------------------
    # Approve / Reject
    # ------------------------------------------------------------------

    def _selected_draft(self):
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        if not rows:
            return None
        return dict(zip(self.draft_cols, self.drafts[next(iter(rows))]))

    def _approve(self):
        draft = self._selected_draft()
        if not draft:
            return
        reviewer_id = self.combo_reviewer.currentData()
        if reviewer_id is None:
            QMessageBox.warning(self, "Cadastre", "Select a reviewer.")
            return
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cadaster.approve_draft(%s, %s)",
                    (draft['draft_id'], reviewer_id)
                )
            conn.commit()
            conn.close()
            QMessageBox.information(
                self, "Cadastre",
                f"Draft {draft['draft_id']} approved.\n"
                f"Parcel {draft['parcel_id']} has been updated."
            )
            self._reload()
            self._refresh_map_layer()
        except Exception as e:
            QMessageBox.critical(self, "Cadastre", f"Approval failed:\n{e}")

    def _reject(self):
        draft = self._selected_draft()
        if not draft:
            return
        reviewer_id = self.combo_reviewer.currentData()
        if reviewer_id is None:
            QMessageBox.warning(self, "Cadastre", "Select a reviewer.")
            return
        review_notes = self.edit_review_notes.toPlainText().strip() or None
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cadaster.reject_draft(%s, %s, %s)",
                    (draft['draft_id'], reviewer_id, review_notes)
                )
            conn.commit()
            conn.close()
            QMessageBox.information(
                self, "Cadastre", f"Draft {draft['draft_id']} rejected."
            )
            self._reload()
        except Exception as e:
            QMessageBox.critical(self, "Cadastre", f"Rejection failed:\n{e}")

    # ------------------------------------------------------------------
    # Reload
    # ------------------------------------------------------------------

    def _reload(self, status_filter=None):
        if status_filter is None:
            status_filter = self.combo_status.currentData()
        self._clear_rubber_bands()
        self._load_drafts(status_filter)
        self._fill_table()
        self.lbl_empty.setVisible(len(self.drafts) == 0)
        self.lbl_draft_notes.setText("—")
        self.edit_review_notes.clear()
        self.btn_approve.setEnabled(False)
        self.btn_reject.setEnabled(False)

    def _refresh_map_layer(self):
        for layer in QgsProject.instance().mapLayers().values():
            name = layer.name().lower()
            if 'active_parcel' in name or ('cadaster' in name and 'parcel' in name):
                layer.reload()
                layer.triggerRepaint()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._clear_rubber_bands()
        super().closeEvent(event)
