from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import QgsPointXY, QgsGeometry, QgsWkbTypes


class MapClickTool(QgsMapTool):
    clicked = pyqtSignal(QgsPointXY)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.setCursor(Qt.CrossCursor)

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.toMapCoordinates(event.pos()))


class PolygonDrawTool(QgsMapTool):
    """Left-click to add vertices, right-click OR Enter to finish, Escape to cancel."""
    polygon_drawn = pyqtSignal(QgsGeometry)
    cancelled     = pyqtSignal()

    _HINT = ("Draw polygon — left-click to add points  |  "
             "right-click or Enter to finish  |  Escape to cancel")

    def __init__(self, canvas):
        super().__init__(canvas)
        self.points = []
        self._rb = QgsRubberBand(canvas, QgsWkbTypes.PolygonGeometry)
        self._rb.setFillColor(QColor(255, 165, 0, 80))
        self._rb.setStrokeColor(QColor(220, 100, 0, 220))
        self._rb.setWidth(2)
        self.setCursor(Qt.CrossCursor)

    def activate(self):
        super().activate()
        from qgis.utils import iface
        iface.mainWindow().statusBar().showMessage(self._HINT)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pt = self.toMapCoordinates(event.pos())
            self.points.append(pt)
            self._rb.addPoint(pt)
        elif event.button() == Qt.RightButton:
            self._finish()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish()
        elif event.key() == Qt.Key_Escape:
            self._reset()
            self.cancelled.emit()

    def _finish(self):
        if len(self.points) >= 3:
            geom = QgsGeometry.fromPolygonXY([self.points])
            self._reset()
            self.polygon_drawn.emit(geom)
        else:
            self._reset()
            self.cancelled.emit()

    def _reset(self):
        self._rb.reset(QgsWkbTypes.PolygonGeometry)
        self.points = []

    def deactivate(self):
        self._reset()
        from qgis.utils import iface
        iface.mainWindow().statusBar().clearMessage()
        super().deactivate()
