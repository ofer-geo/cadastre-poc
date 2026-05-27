from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QComboBox, QLineEdit, QTextEdit, QPushButton,
    QRadioButton, QButtonGroup, QMessageBox, QWidget
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject, QgsFeatureRequest, QgsRectangle, QgsGeometry
)
from .map_tool import MapClickTool, PolygonDrawTool
from . import db


class SubmitDraftDialog(QDialog):

    def __init__(self, iface, canvas):
        super().__init__(iface.mainWindow())
        self.iface         = iface
        self.canvas        = canvas
        self.prev_tool     = None
        self.map_tool      = None
        self.selected_feat = None   # QgsFeature of clicked parcel
        self.drawn_geom    = None   # QgsGeometry from PolygonDrawTool
        self.users         = []

        self.setWindowTitle("Submit Draft")
        self.setMinimumWidth(420)

        self._load_users()
        self._build_ui()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_users(self):
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, username, role FROM cadaster.users ORDER BY username"
                )
                self.users = cur.fetchall()
            conn.close()
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(), "Cadastre", f"Could not load users:\n{e}"
            )

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Submitted by
        form = QFormLayout()
        self.combo_user = QComboBox()
        for uid, uname, role in self.users:
            self.combo_user.addItem(f"{uname}  ({role})", uid)
        form.addRow("Submitted by:", self.combo_user)
        layout.addLayout(form)

        # Action radio buttons
        action_box = QGroupBox("Action")
        action_row = QHBoxLayout(action_box)
        self.rb_modify = QRadioButton("Modify")
        self.rb_retire = QRadioButton("Retire")
        self.rb_create = QRadioButton("Create New")
        self.rb_modify.setChecked(True)
        self._action_grp = QButtonGroup(self)
        for rb in (self.rb_modify, self.rb_retire, self.rb_create):
            self._action_grp.addButton(rb)
            action_row.addWidget(rb)
        self.rb_modify.toggled.connect(self._refresh_visibility)
        self.rb_retire.toggled.connect(self._refresh_visibility)
        self.rb_create.toggled.connect(self._refresh_visibility)
        layout.addWidget(action_box)

        # --- Modify / Retire: select parcel ---
        self.grp_select = QGroupBox("Select Existing Parcel")
        sel_row = QHBoxLayout(self.grp_select)
        self.lbl_parcel = QLabel("—")
        self.btn_click_map = QPushButton("📍  Click on map")
        self.btn_click_map.clicked.connect(self._start_map_click)
        sel_row.addWidget(QLabel("Parcel ID:"))
        sel_row.addWidget(self.lbl_parcel)
        sel_row.addStretch()
        sel_row.addWidget(self.btn_click_map)
        layout.addWidget(self.grp_select)

        # --- Create: draw polygon ---
        self.grp_draw = QGroupBox("New Parcel")
        draw_form = QFormLayout(self.grp_draw)

        id_row = QHBoxLayout()
        self.lbl_new_id = QLabel("—")
        id_row.addWidget(self.lbl_new_id)
        id_row.addStretch()
        draw_form.addRow("Parcel ID (auto):", id_row)

        geom_row = QHBoxLayout()
        self.btn_draw = QPushButton("✏  Draw polygon on map")
        self.btn_draw.clicked.connect(self._start_polygon_draw)
        self.lbl_geom_status = QLabel("No geometry yet")
        geom_row.addWidget(self.btn_draw)
        geom_row.addWidget(self.lbl_geom_status)
        draw_form.addRow("Geometry:", geom_row)
        self.grp_draw.setVisible(False)
        layout.addWidget(self.grp_draw)

        # --- Attributes ---
        attrs_box = QGroupBox("Attributes")
        attrs_form = QFormLayout(attrs_box)

        self.row_owner = QWidget()
        owner_layout = QHBoxLayout(self.row_owner)
        owner_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_owner = QLineEdit()
        owner_layout.addWidget(self.edit_owner)
        attrs_form.addRow("Owner Name:", self.row_owner)

        self.edit_notes = QTextEdit()
        self.edit_notes.setFixedHeight(65)
        attrs_form.addRow("Notes:", self.edit_notes)
        layout.addWidget(attrs_box)

        # Buttons
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        self.btn_submit = QPushButton("Submit Draft")
        self.btn_submit.setDefault(True)
        self.btn_submit.clicked.connect(self._submit)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self.btn_submit)
        layout.addLayout(btn_row)

    def _refresh_visibility(self):
        is_create = self.rb_create.isChecked()
        is_retire = self.rb_retire.isChecked()
        self.grp_select.setVisible(not is_create)
        self.grp_draw.setVisible(is_create)
        self.row_owner.setVisible(not is_retire)
        if is_create:
            self._suggest_next_id()

    def _suggest_next_id(self):
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(parcel_id), 0) + 1 FROM cadaster.parcels"
                )
                self.lbl_new_id.setText(str(cur.fetchone()[0]))
            conn.close()
        except Exception:
            self.lbl_new_id.setText("?")

    # ------------------------------------------------------------------
    # Map interactions
    # ------------------------------------------------------------------

    def _start_map_click(self):
        self.prev_tool = self.canvas.mapTool()
        self.hide()
        QApplication.processEvents()
        self.map_tool = MapClickTool(self.canvas)
        self.map_tool.clicked.connect(self._on_parcel_clicked)
        self.map_tool.deactivated.connect(self._restore)
        self.canvas.setMapTool(self.map_tool)

    def _on_parcel_clicked(self, point):
        self.canvas.unsetMapTool(self.map_tool)
        layer = self._find_parcel_layer()
        if layer:
            radius = self.canvas.mapUnitsPerPixel() * 10
            rect = QgsRectangle(
                point.x() - radius, point.y() - radius,
                point.x() + radius, point.y() + radius
            )
            feats = list(
                layer.getFeatures(QgsFeatureRequest().setFilterRect(rect).setLimit(1))
            )
            if feats:
                self.selected_feat = feats[0]
                self.lbl_parcel.setText(str(feats[0]['parcel_id']))
                if not self.rb_retire.isChecked():
                    self.edit_owner.setText(str(feats[0]['owner_name'] or ''))
        self._restore()

    def _start_polygon_draw(self):
        self.prev_tool = self.canvas.mapTool()
        self.hide()
        QApplication.processEvents()
        self.map_tool = PolygonDrawTool(self.canvas)
        self.map_tool.polygon_drawn.connect(self._on_polygon_drawn)
        self.map_tool.cancelled.connect(self._restore)
        self.map_tool.deactivated.connect(self._restore)
        self.canvas.setMapTool(self.map_tool)

    def _on_polygon_drawn(self, geom):
        self.drawn_geom = geom
        self.lbl_geom_status.setText("✓ Polygon drawn")
        self._restore()

    def _restore(self):
        tool, self.prev_tool = self.prev_tool, None  # clear first to prevent re-entry
        if tool:
            self.canvas.setMapTool(tool)
        self.show()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def _submit(self):
        uid = self.combo_user.currentData()
        if uid is None:
            QMessageBox.warning(self, "Cadastre", "Select a user.")
            return
        if self.rb_create.isChecked():
            self._do_create(uid)
        elif self.rb_modify.isChecked():
            self._do_modify(uid)
        else:
            self._do_retire(uid)

    def _do_create(self, uid):
        if not self.drawn_geom:
            QMessageBox.warning(self, "Cadastre", "Draw a polygon on the map first.")
            return
        parcel_id_str = self.lbl_new_id.text()
        if not parcel_id_str.isdigit():
            QMessageBox.warning(self, "Cadastre", "Invalid parcel ID.")
            return

        src = self.canvas.mapSettings().destinationCrs()
        dst = QgsCoordinateReferenceSystem("EPSG:4326")
        t   = QgsCoordinateTransform(src, dst, QgsProject.instance())
        geom = QgsGeometry.fromWkt(self.drawn_geom.asWkt())
        geom.transform(t)

        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cadaster.draft_parcels
                        (parcel_id, action, geom, owner_name, notes, submitted_by)
                    VALUES (%s, 'create', ST_Multi(ST_GeomFromText(%s, 4326)), %s, %s, %s)
                """, (
                    int(parcel_id_str),
                    geom.asWkt(),
                    self.edit_owner.text().strip() or None,
                    self.edit_notes.toPlainText().strip() or None,
                    uid
                ))
            conn.commit()
            conn.close()
            QMessageBox.information(
                self, "Cadastre",
                f"Create draft submitted for new parcel {parcel_id_str}."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Cadastre", f"Submit failed:\n{e}")

    def _do_modify(self, uid):
        if not self.selected_feat:
            QMessageBox.warning(self, "Cadastre", "Click a parcel on the map first.")
            return
        pid = self.selected_feat['parcel_id']
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                # Carry over current geometry; only attributes change
                cur.execute("""
                    INSERT INTO cadaster.draft_parcels
                        (parcel_id, action, geom, owner_name, notes, submitted_by)
                    SELECT %s, 'modify', geom, %s, %s, %s
                    FROM cadaster.active_parcels WHERE parcel_id = %s
                """, (
                    pid,
                    self.edit_owner.text().strip() or None,
                    self.edit_notes.toPlainText().strip() or None,
                    uid,
                    pid
                ))
            conn.commit()
            conn.close()
            QMessageBox.information(
                self, "Cadastre", f"Modify draft submitted for parcel {pid}."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Cadastre", f"Submit failed:\n{e}")

    def _do_retire(self, uid):
        if not self.selected_feat:
            QMessageBox.warning(self, "Cadastre", "Click a parcel on the map first.")
            return
        pid = self.selected_feat['parcel_id']
        reply = QMessageBox.question(
            self, "Cadastre",
            f"Submit a RETIRE draft for parcel {pid}?\n"
            "If approved, it will be removed from the active layer.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        try:
            conn = db.get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cadaster.draft_parcels
                        (parcel_id, action, notes, submitted_by)
                    VALUES (%s, 'retire', %s, %s)
                """, (
                    pid,
                    self.edit_notes.toPlainText().strip() or None,
                    uid
                ))
            conn.commit()
            conn.close()
            QMessageBox.information(
                self, "Cadastre", f"Retire draft submitted for parcel {pid}."
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Cadastre", f"Submit failed:\n{e}")

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _find_parcel_layer(self):
        for layer in QgsProject.instance().mapLayers().values():
            name = layer.name().lower()
            if 'active_parcel' in name or ('cadaster' in name and 'parcel' in name):
                return layer
        return None
