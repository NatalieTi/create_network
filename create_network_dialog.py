import os

from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtWidgets import QDialog
from qgis.gui import QgsMapLayerComboBox, QgsMessageBar
from qgis.core import QgsProject, QgsFeature, QgsGeometry,QgsWkbTypes, QgsExpression, QgsPointXY, QgsVectorLayer, QgsField, QgsSpatialIndex, QgsLineSymbol, QgsPoint, QgsFeatureRequest, QgsMapLayer
from PyQt5.QtCore import QVariant
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtGui import QColor

# Load the .ui file
FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'create_network_dialog_base.ui'))


class CreateNetworkDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        """Constructor."""
        super(CreateNetworkDialog, self).__init__(parent)
        self.setupUi(self)

        # Populate the combo boxes with vector layers
        self.populate_layer_comboboxes()
        
        # Connect the Run button to its handler
        self.btnBuild.clicked.connect(self.build_network)
        self.btnNetwork.clicked.connect(self.connect_network)
        self.btnHeat.clicked.connect(self.connect_heat)

    def populate_layer_comboboxes(self):
        # Get the list of vector layers
        layers = [layer for layer in QgsProject.instance().mapLayers().values() if layer.type() == QgsMapLayer.VectorLayer]

        # Clear any existing items in the combo boxes
        self.cmbBuild.clear()
        self.cmbRoad.clear()
        self.cmbHeat.clear()
        self.cmbNetwork.clear()
        self.cmbRoad_2.clear()
        self.cmbNetwork_2.clear()

        self.cmbBuild.addItem("Select building layer")
        self.cmbRoad.addItem("Select road layer")
        self.cmbNetwork.addItem("Select new lines layer")
        self.cmbRoad_2.addItem("Select road layer")
        self.cmbHeat.addItem("Select heating center layer")
        self.cmbNetwork_2.addItem("Select connected lines layer")

        # Add layer names to combo boxes
        for layer in layers:
            if layer.type() == QgsMapLayer.VectorLayer:
                self.cmbBuild.addItem(layer.name(), layer)
                self.cmbRoad.addItem(layer.name(), layer)
                self.cmbHeat.addItem(layer.name(), layer)
                self.cmbNetwork.addItem(layer.name(), layer)
                self.cmbRoad_2.addItem(layer.name(), layer)
                self.cmbNetwork_2.addItem(layer.name(), layer)

    def build_network(self):
        buildings_layer = self.cmbBuild.currentData()
        roads_layer = self.cmbRoad.currentData()

        if not buildings_layer or not roads_layer:
            QMessageBox.critical(self, "Error", "Please select both layers")
            return

        crs = buildings_layer.crs().authid()
        new_lines_layer = QgsVectorLayer(f"LineString?crs={crs}", "network_lines", "memory")
        new_lines_provider = new_lines_layer.dataProvider()
        new_lines_provider.addAttributes([QgsField("BuildingID", QVariant.Int), QgsField("RoadID", QVariant.Int)])
        new_lines_layer.updateFields()

        index = QgsSpatialIndex(flags=QgsSpatialIndex.FlagStoreFeatureGeometries)
        for road_feat in roads_layer.getFeatures():
            index.insertFeature(road_feat)

        features = []
        for building_feat in buildings_layer.getFeatures():
            building_point = building_feat.geometry().asPoint()
            nearest_ids = index.nearestNeighbor(building_point, 1)
            if nearest_ids:
                nearest_road_feat = next(roads_layer.getFeatures(QgsFeatureRequest().setFilterFid(nearest_ids[0])))
                nearest_road_geom = nearest_road_feat.geometry()
                nearest_point = nearest_road_geom.closestSegmentWithContext(building_point)[1]

                # Create a line from the building to the nearest road
                line_geom = QgsGeometry.fromPolyline([QgsPoint(building_point), QgsPoint(nearest_point)])
                line_feat = QgsFeature()
                line_feat.setGeometry(line_geom)
                line_feat.setAttributes([building_feat.id(), nearest_road_feat.id()])
                features.append(line_feat)

        new_lines_provider.addFeatures(features)
        QgsProject.instance().addMapLayer(new_lines_layer)
        QMessageBox.information(self, "Success", "Network lines created successfully")

        # Style the connection lines for better visualization
        self.style_lines_layer(new_lines_layer)

    def snap_to_road(self, line_geom, road_layer):
        """Snap line geometry to the nearest road."""
        for road_feat in road_layer.getFeatures():
            road_geom = road_feat.geometry()
            intersection = QgsGeometry.fromPoint(line_geom).intersection(road_geom)
            if intersection is not None and not intersection.isEmpty():
                if intersection.isMultipart():
                    return intersection.asMultiPoint()[0]
                else:
                    return intersection.asPoint()
        return None
    
    def connect_network(self):
        network_lines_layer = self.cmbNetwork.currentData()
        roads_layer = self.cmbRoad_2.currentData()

        if not network_lines_layer or not roads_layer:
            self.message_bar.pushMessage("Error", "Please select both layers", level=3)
            return

        crs = network_lines_layer.crs().toWkt()
        connected_lines_layer = QgsVectorLayer(f"LineString?crs={crs}", "connected_network_lines", "memory")
        connected_lines_provider = connected_lines_layer.dataProvider()
        connected_lines_provider.addAttributes([QgsField("SegmentID", QVariant.Int)])
        connected_lines_layer.updateFields()

        features = []
        for net_feat in network_lines_layer.getFeatures():
            net_geom = net_feat.geometry()
            if net_geom.isMultipart():
                line_strings = net_geom.asMultiPolyline()
            else:
                line_strings = [net_geom.asPolyline()]

            for line_string in line_strings:
                for i in range(len(line_string) - 1):
                    geom1 = QgsPoint(line_string[i])
                    geom2 = QgsPoint(line_string[i + 1])
                    closest_point1 = self.snap_to_road(geom1, roads_layer)
                    closest_point2 = self.snap_to_road(geom2, roads_layer)

                    if closest_point1 and closest_point2:
                        new_line = QgsGeometry.fromPolylineXY([closest_point1, closest_point2])
                        new_feature = QgsFeature(connected_lines_layer.fields())
                        new_feature.setGeometry(new_line)
                        connected_lines_provider.addFeatures([new_feature])

        connected_lines_layer.commitChanges()
        QgsProject.instance().addMapLayer(connected_lines_layer)
        QMessageBox.information(self, "Success", "Network lines connected successfully")

        # Style the lines for better visualization
        self.style_lines_layer(connected_lines_layer)


    def connect_heat(self):
        pass

    def style_lines_layer(self, layer):
        """Styles the lines layer for better visualization."""
        symbol = QgsLineSymbol.createSimple({'color': 'yellow', 'width': '0.5'})
        renderer = layer.renderer()
        renderer.setSymbol(symbol)
        layer.triggerRepaint()