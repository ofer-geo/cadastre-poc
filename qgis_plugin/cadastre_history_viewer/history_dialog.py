import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
    QTableWidgetItem, QLabel, QPushButton, QHeaderView, QFrame
)
from qgis.PyQt.QtGui import QColor, QBrush, QFont
from qgis.PyQt.QtCore import Qt, QDate, QObject, QEvent


class _ClickFilter(QObject):
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
        return False
from qgis.gui import QgsRubberBand
from qgis.core import (
    QgsWkbTypes, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsProject, QgsRectangle
)

ACTIVE_FILL    = QColor(144, 238, 144, 191)   # 25% transparent
ACTIVE_STROKE  = QColor(  0, 120,   0, 220)
HIST_FILL      = QColor(169, 169, 169, 191)   # 25% transparent
HIST_STROKE    = QColor( 80,  80,  80, 220)
SEL_FILL       = QColor(255, 255,   0, 191)   # 25% transparent
SEL_STROKE     = QColor(200, 140,   0, 230)

ACTIVE_ROW_BG  = QColor(210, 245, 210)
HIST_ROW_BG    = QColor(225, 225, 225)


def _fmt_date(val):
    if val is None:
        return "—"
    if isinstance(val, QDate):
        return val.toString("dd-MM-yyyy")
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.strftime("%d-%m-%Y")
    try:
        if not val:
            return "—"
    except Exception:
        pass
    return str(val)


class HistoryDialog(QDialog):

    def __init__(self, iface, parcel_id, features, canvas):
        super().__init__(iface.mainWindow())
        self.canvas       = canvas
        self.parcel_id    = parcel_id
        self.rubber_bands      = []   # list of (QgsRubberBand, status_str)
        self.table             = None
        self._deselect_pending = False

        self.setWindowTitle(f"Parcel History — ID {parcel_id}")
        self.setMinimumWidth(660)
        self.setMinimumHeight(300)

        self._build_ui(features)
        self._draw_rubber_bands(features)
        self._zoom_to_features(features)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self, features):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(f"<b>Version history for Parcel {self.parcel_id}</b>")
        title.setFont(QFont("", 11))
        layout.addWidget(title)

        self.table = self._build_table(features)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.table.clicked.connect(self._on_row_clicked)
        self._click_filter = _ClickFilter(
            self.table,
            lambda was_sel: setattr(self, '_deselect_pending', was_sel)
        )
        self.table.viewport().installEventFilter(self._click_filter)
        layout.addWidget(self.table)

        layout.addWidget(self._build_legend())

        btn_close = QPushButton("Close")
        btn_close.setFixedWidth(80)
        btn_close.clicked.connect(self.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _build_table(self, features):
        headers = ["#", "Owner", "Created By", "Valid From", "Valid To", "Status", "Notes"]
        table = QTableWidget(len(features), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(False)

        for row, feat in enumerate(features):
            status    = feat["version_status"]
            is_active = status == "active"
            bg        = ACTIVE_ROW_BG if is_active else HIST_ROW_BG

            cells = [
                str(row + 1),
                str(feat["owner_name"] or ""),
                str(feat["created_by_username"] or ""),
                _fmt_date(feat["valid_from"]),
                _fmt_date(feat["valid_to"]),
                "Active" if is_active else "Historical",
                str(feat["notes"] or ""),
            ]

            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setBackground(QBrush(bg))
                if col == 5 and is_active:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, col, item)

        table.resizeColumnsToContents()
        table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        return table

    def _build_legend(self):
        frame = QFrame()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        for label_text, color in [("Active", ACTIVE_FILL), ("Historical", HIST_FILL), ("Selected", SEL_FILL)]:
            swatch = QLabel()
            swatch.setFixedSize(16, 16)
            swatch.setStyleSheet(
                f"background-color: rgba({color.red()},{color.green()},"
                f"{color.blue()},{color.alpha()}); border: 1px solid #555;"
            )
            row.addWidget(swatch)
            row.addWidget(QLabel(label_text))
        row.addStretch()
        return frame

    # ------------------------------------------------------------------
    # Rubber bands
    # ------------------------------------------------------------------

    def _draw_rubber_bands(self, features):
        src_crs   = QgsCoordinateReferenceSystem("EPSG:4326")
        dst_crs   = self.canvas.mapSettings().destinationCrs()
        transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())

        for feat in features:
            status    = feat["version_status"]
            is_active = status == "active"

            rb = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            rb.setFillColor(ACTIVE_FILL   if is_active else HIST_FILL)
            rb.setStrokeColor(ACTIVE_STROKE if is_active else HIST_STROKE)
            rb.setWidth(2)

            geom = feat.geometry()
            geom.transform(transform)
            rb.setToGeometry(geom, None)

            self.rubber_bands.append((rb, status))

    def _zoom_to_features(self, features):
        src_crs   = QgsCoordinateReferenceSystem("EPSG:4326")
        dst_crs   = self.canvas.mapSettings().destinationCrs()
        transform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())

        extent = QgsRectangle()
        for feat in features:
            geom = feat.geometry()
            geom.transform(transform)
            extent.combineExtentWith(geom.boundingBox())

        extent.grow(max(extent.width(), extent.height()) * 0.3 + 1e-5)
        self.canvas.setExtent(extent)
        self.canvas.refresh()

    # ------------------------------------------------------------------
    # Row selection → highlight rubber band
    # ------------------------------------------------------------------

    def _on_selection_changed(self, selected, deselected):
        selected_rows = {idx.row() for idx in self.table.selectedIndexes()}

        for i, (rb, status) in enumerate(self.rubber_bands):
            if i in selected_rows:
                rb.setFillColor(SEL_FILL)
                rb.setStrokeColor(SEL_STROKE)
                rb.setWidth(3)
            else:
                is_active = status == "active"
                rb.setFillColor(ACTIVE_FILL   if is_active else HIST_FILL)
                rb.setStrokeColor(ACTIVE_STROKE if is_active else HIST_STROKE)
                rb.setWidth(2)

        self.canvas.refresh()

    def _on_row_clicked(self, index):
        if self._deselect_pending:
            self.table.clearSelection()
            self._deselect_pending = False

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        for rb, _ in self.rubber_bands:
            rb.reset(QgsWkbTypes.PolygonGeometry)
            self.canvas.scene().removeItem(rb)
        self.rubber_bands.clear()
        self.canvas.refresh()
        super().closeEvent(event)
