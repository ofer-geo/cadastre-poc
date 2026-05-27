import os

from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.core import (
    QgsProject, QgsDataSourceUri, QgsVectorLayer, QgsFeatureRequest, QgsRectangle
)
from .map_tool import MapClickTool
from .history_dialog import HistoryDialog
from .submit_dialog import SubmitDraftDialog
from .review_dialog import ReviewDraftsDialog

# Fallback connection defaults — change if your setup differs
_DEFAULT_HOST   = "localhost"
_DEFAULT_PORT   = "5432"
_DEFAULT_DB     = "cadastre_poc"
_DEFAULT_USER   = "postgres"
_DEFAULT_PASS   = "postgres"


class CadastreHistoryViewer:

    def __init__(self, iface):
        self.iface          = iface
        self.canvas         = iface.mapCanvas()
        self.action         = None
        self.action_submit  = None
        self.action_review  = None
        self.tool           = None
        self.prev_tool      = None

    # ------------------------------------------------------------------
    # Plugin lifecycle
    # ------------------------------------------------------------------

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        self.action = QAction(QIcon(icon_path), "Parcel History Viewer", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setToolTip("Click a parcel to view its version history")
        self.action.triggered.connect(self._toggle_tool)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Cadastre", self.action)

        icon_submit = os.path.join(os.path.dirname(__file__), "icon_submit.svg")
        icon_review = os.path.join(os.path.dirname(__file__), "icon_review.svg")

        self.action_submit = QAction(
            QIcon(icon_submit), "Submit Draft", self.iface.mainWindow()
        )
        self.action_submit.setToolTip("Submit a parcel draft (create / modify / retire)")
        self.action_submit.triggered.connect(self._open_submit)
        self.iface.addToolBarIcon(self.action_submit)
        self.iface.addPluginToMenu("&Cadastre", self.action_submit)

        self.action_review = QAction(
            QIcon(icon_review), "Review Drafts", self.iface.mainWindow()
        )
        self.action_review.setToolTip("Review and approve/reject pending drafts")
        self.action_review.triggered.connect(self._open_review)
        self.iface.addToolBarIcon(self.action_review)
        self.iface.addPluginToMenu("&Cadastre", self.action_review)

    def unload(self):
        for action in (self.action, self.action_submit, self.action_review):
            if action:
                self.iface.removeToolBarIcon(action)
                self.iface.removePluginMenu("&Cadastre", action)
        if self.tool:
            self.canvas.unsetMapTool(self.tool)

    # ------------------------------------------------------------------
    # Tool activation
    # ------------------------------------------------------------------

    def _toggle_tool(self, checked):
        if checked:
            self.prev_tool = self.canvas.mapTool()
            self.tool = MapClickTool(self.canvas)
            self.tool.clicked.connect(self._on_map_clicked)
            self.tool.deactivated.connect(lambda: self.action.setChecked(False))
            self.canvas.setMapTool(self.tool)
        else:
            if self.prev_tool:
                self.canvas.setMapTool(self.prev_tool)
            else:
                self.canvas.unsetMapTool(self.tool)

    # ------------------------------------------------------------------
    # Click handler
    # ------------------------------------------------------------------

    def _on_map_clicked(self, point):
        layer = self._find_active_parcels_layer()
        if layer is None:
            QMessageBox.warning(
                self.iface.mainWindow(), "Cadastre History Viewer",
                "No parcel layer found in the project.\n\n"
                "Add the cadaster.active_parcels PostGIS view as a layer first."
            )
            return

        radius = self.canvas.mapUnitsPerPixel() * 10
        rect = QgsRectangle(
            point.x() - radius, point.y() - radius,
            point.x() + radius, point.y() + radius
        )
        features = list(layer.getFeatures(QgsFeatureRequest().setFilterRect(rect).setLimit(1)))
        if not features:
            return

        parcel_id = features[0]["parcel_id"]
        self._load_and_show_history(parcel_id)

    # ------------------------------------------------------------------
    # History query
    # ------------------------------------------------------------------

    def _load_and_show_history(self, parcel_id):
        params = self._connection_params()

        history_uri = QgsDataSourceUri()
        history_uri.setConnection(
            params["host"], params["port"],
            params["dbname"], params["user"], params["password"]
        )
        history_uri.setDataSource(
            "cadaster", "parcel_history", "geom",
            f"parcel_id = {int(parcel_id)}", "gid"
        )

        layer = QgsVectorLayer(history_uri.uri(False), f"history_{parcel_id}", "postgres")
        if not layer.isValid():
            QMessageBox.critical(
                self.iface.mainWindow(), "Cadastre History Viewer",
                f"Could not load history for parcel {parcel_id}.\n"
                "Check your database connection settings in plugin.py."
            )
            return

        features = list(layer.getFeatures())
        if not features:
            QMessageBox.information(
                self.iface.mainWindow(), "Cadastre History Viewer",
                f"No history found for parcel {parcel_id}."
            )
            return

        dlg = HistoryDialog(self.iface, parcel_id, features, self.canvas)
        dlg.exec_()

    def _open_submit(self):
        self._submit_dlg = SubmitDraftDialog(self.iface, self.canvas)
        self._submit_dlg.show()

    def _open_review(self):
        dlg = ReviewDraftsDialog(self.iface, self.canvas)
        dlg.exec_()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_active_parcels_layer(self):
        """Return the first loaded layer that looks like the active parcels layer."""
        for layer in QgsProject.instance().mapLayers().values():
            name = layer.name().lower()
            if "active_parcel" in name or ("cadaster" in name and "parcel" in name):
                return layer
        return None

    def _connection_params(self):
        """Try to read DB params from an existing cadaster layer; fall back to defaults."""
        layer = self._find_active_parcels_layer()
        p = {
            "host":     _DEFAULT_HOST,
            "port":     _DEFAULT_PORT,
            "dbname":   _DEFAULT_DB,
            "user":     _DEFAULT_USER,
            "password": _DEFAULT_PASS,
        }
        if layer:
            uri = QgsDataSourceUri(layer.dataProvider().dataSourceUri())
            if uri.host():     p["host"]     = uri.host()
            if uri.port():     p["port"]     = uri.port()
            if uri.database(): p["dbname"]   = uri.database()
            if uri.username(): p["user"]     = uri.username()
            if uri.password(): p["password"] = uri.password()
        return p
