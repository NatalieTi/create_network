import os

from qgis.PyQt import uic, QtWidgets
from qgis.gui import QgsMapToolEmitPoint, QgsSnapIndicator
from qgis.core import (QgsProject, QgsFeature, QgsLineSymbol, QgsGeometry, QgsField, QgsSpatialIndex,
                       QgsWkbTypes, QgsPointXY, QgsFeatureRequest, QgsMapLayer, QgsVectorLayer, QgsPoint)
from PyQt5.QtCore import QVariant, Qt, pyqtSignal
from PyQt5.QtWidgets import QMessageBox

from networkx.algorithms.approximation import steiner_tree
import networkx as nx

import processing

# Load the .ui file
FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'create_network_dialog_base.ui'))

class SnappingMapToolEmitPoint(QgsMapToolEmitPoint):
    snapClicked = pyqtSignal(QgsPointXY, Qt.MouseButton)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.canvasClicked.connect(self.snapClick)
        self.snapIndicator = QgsSnapIndicator(canvas)
        self.snapper = self.canvas.snappingUtils()

    def canvasMoveEvent(self, event):
        snapMatch = self.snapper.snapToMap(event.pos())
        self.snapIndicator.setMatch(snapMatch)

    def snapClick(self, point, button):
        if self.snapIndicator.match().type():
            point = self.snapIndicator.match().point()
        self.snapClicked.emit(point, button)

class CreateNetworkDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, iface, parent=None):
        """Constructor."""
        super(CreateNetworkDialog, self).__init__(parent)
        self.setupUi(self)

        # Get the main QGIS interface (to access the map canvas)
        self.iface = iface
        self.mapCanvas = self.iface.mapCanvas()

        # Populate the combo boxes with vector layers
        self.populate_layer_comboboxes()
        
        # Connect the buttons to their handlers
        self.btnBuild.clicked.connect(self.build_network)
        self.btnNetwork.clicked.connect(self.connect_network)
        self.btnStart.clicked.connect(self.toggle_start_point_dialog)
        self.btnHeat.clicked.connect(self.toggle_heat_point_dialog)

    def populate_layer_comboboxes(self):
        # Get the list of vector layers
        layers = [layer for layer in QgsProject.instance().mapLayers().values() if layer.type() == QgsMapLayer.VectorLayer]

        # Clear any existing items in the combo boxes
        self.cmbBuild.clear()
        self.cmbRoad.clear()
        self.cmbEnd.clear()
        self.cmbNetwork.clear()
        self.cmbRoad_2.clear()

        self.cmbBuild.addItem("Select building layer")
        self.cmbRoad.addItem("Select road layer")
        self.cmbNetwork.addItem("Select new lines layer")
        self.cmbRoad_2.addItem("Select road layer")
        self.cmbEnd.addItem("Select end point layer")

        # Add layer names to combo boxes
        for layer in layers:
            if layer.type() == QgsMapLayer.VectorLayer:
                self.cmbBuild.addItem(layer.name(), layer)
                self.cmbRoad.addItem(layer.name(), layer)
                self.cmbRoad_2.addItem(layer.name(), layer)
                self.cmbNetwork.addItem(layer.name(), layer)
                self.cmbEnd.addItem(layer.name(), layer)

    def toggle_start_point_dialog(self):
        """Toggle the dialog visibility for selecting start point."""
        if self.isHidden():
            self.show()
        else:
            self.hide()
            self.activate_start_point_tool()

    def toggle_heat_point_dialog(self):
        """Toggle the dialog visibility for selecting heat point."""
        if self.isHidden():
            self.show()
        else:
            self.hide()
            self.activate_heat_point_tool()

    def activate_start_point_tool(self):
        """Activate the point tool to select the start point on the map."""
        self.pointTool = SnappingMapToolEmitPoint(self.mapCanvas)
        self.pointTool.setCursor(Qt.CrossCursor)
        self.pointTool.snapClicked.connect(self.start_point_clicked)
        self.mapCanvas.setMapTool(self.pointTool)

    def activate_heat_point_tool(self):
        """Activate the point tool to select the heat point on the map."""
        self.pointTool = SnappingMapToolEmitPoint(self.mapCanvas)
        self.pointTool.setCursor(Qt.CrossCursor)
        self.pointTool.snapClicked.connect(self.heat_point_clicked)
        self.mapCanvas.setMapTool(self.pointTool)

    def start_point_clicked(self, point):
        """Handle the map click event to select a start point."""
        self.lineStart.setText(f"{point.x()}, {point.y()}")
        self.show()

    def heat_point_clicked(self, point):
        """Handle the map click event to select a heat point."""
        self.lineHeat.setText(f"{point.x()}, {point.y()}")
        self.show()

    def clean_topology(self, layer):
        """Clean the topology of a vector layer."""
        parameters = {
            'INPUT': layer,
            'OUTPUT': 'memory:'
        }
        result = processing.run('native:fixgeometries', parameters)
        cleaned_layer = result['OUTPUT']
        return cleaned_layer
    
    def build_network(self):
        buildings_layer = self.cmbBuild.currentData()
        roads_layer = self.cmbRoad.currentData()
    
        if not buildings_layer or not roads_layer:
            QMessageBox.critical(self, "Error", "Please select both layers")
            return
    
        # Extract lineHeat value from the user interface
        line_heat_text = self.lineHeat.text()
        try:
            line_heat_point = QgsPointXY(*map(float, line_heat_text.split(',')))
        except ValueError:
            QMessageBox.critical(self, "Error", "Invalid heat point coordinates")
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
    
                # Create a line from the building point to the nearest road point
                line_geom = QgsGeometry.fromPolyline([QgsPoint(building_point), QgsPoint(nearest_point)])
                line_feat = QgsFeature()
                line_feat.setGeometry(line_geom)
                line_feat.setAttributes([building_feat.id(), nearest_road_feat.id()])
                features.append(line_feat)
    
        # Create a line from the line heat point to its nearest road point
        nearest_ids_line_heat = index.nearestNeighbor(line_heat_point, 1)
        if nearest_ids_line_heat:
            nearest_road_feat_line_heat = next(roads_layer.getFeatures(QgsFeatureRequest().setFilterFid(nearest_ids_line_heat[0])))
            nearest_road_geom_line_heat = nearest_road_feat_line_heat.geometry()
            nearest_point_line_heat = nearest_road_geom_line_heat.closestSegmentWithContext(line_heat_point)[1]
    
            line_geom_line_heat = QgsGeometry.fromPolyline([QgsPoint(line_heat_point), QgsPoint(nearest_point_line_heat)])
            line_feat_line_heat = QgsFeature()
            line_feat_line_heat.setGeometry(line_geom_line_heat)
            line_feat_line_heat.setAttributes([-1, nearest_road_feat_line_heat.id()])  # Use -1 as a placeholder for lineHeat point
            features.append(line_feat_line_heat)
    
        new_lines_provider.addFeatures(features)
        QgsProject.instance().addMapLayer(new_lines_layer)
        QMessageBox.information(self, "Success", "Network lines created successfully")



    def connect_network(self):
        # Get selected layers from the combo boxes
        network_lines_layer = self.cmbNetwork.currentData()
        roads_layer = self.cmbRoad_2.currentData()
        buildings_layer = self.cmbEnd.currentData()

        if not network_lines_layer or not roads_layer:
            QMessageBox.critical(self, "Error", "Please select both layers")
            return

        # Snap geometries in the roads layer
        snap_params = {
            'INPUT': roads_layer,
            'REFERENCE_LAYER': network_lines_layer,
            'TOLERANCE': 0.1,
            'BEHAVIOR': 0,
            'OUTPUT': 'memory:'  # Use memory to store results
        }
        snap_result = processing.run("native:snapgeometries", snap_params)
        snapped_roads_layer = snap_result['OUTPUT']

        # Copy snapped geometries back to the original roads layer
        roads_layer.startEditing()
        roads_layer.dataProvider().truncate()
        for feature in snapped_roads_layer.getFeatures():
            roads_layer.addFeature(feature)
        roads_layer.commitChanges()

        # Перевірка, чи шар готовий до редагування
        if not roads_layer.isEditable():
            roads_layer.startEditing()

        # Додавання поля для довжини лінії, якщо його ще немає
        if 'length' not in [field.name() for field in roads_layer.fields()]:
            roads_layer.dataProvider().addAttributes([QgsField('length', QVariant.Double)])
            roads_layer.updateFields()

        # Обчислення довжини кожного відрізка
        for feature in roads_layer.getFeatures():
            geom = feature.geometry()
            length = geom.length()
            feature['length'] = length
            roads_layer.updateFeature(feature)

        # Створення індексу просторових об'єктів
        index = QgsSpatialIndex(roads_layer.getFeatures())

        # Створення списку всіх ліній
        all_segments = [feature for feature in roads_layer.getFeatures()]

        # Функція для перевірки, чи заснаповані дві лінії
        def are_snapped(f1, f2):
            return f1.geometry().touches(f2.geometry())

        # Функція для побудови групи заснапованих ліній
        def build_group(feature, all_features, index):
            group = set()
            to_process = [feature]
            processed = set()
            while to_process:
                current = to_process.pop()
                if current.id() in processed:
                    continue
                processed.add(current.id())
                group.add(current)
                geom = current.geometry()
                nearby_ids = index.intersects(geom.boundingBox())
                for nearby_id in nearby_ids:
                    if nearby_id == current.id():
                        continue
                    nearby_feature = roads_layer.getFeature(nearby_id)
                    if nearby_feature not in group and are_snapped(current, nearby_feature):
                        to_process.append(nearby_feature)
            return group

        # Побудова групи заснапованих ліній, починаючи з першої лінії
        main_group = build_group(all_segments[0], all_segments, index)

        # Знаходження та видалення незаснапованих ліній
        to_delete = [feature.id() for feature in all_segments if feature not in main_group]

        for fid in to_delete:
            roads_layer.deleteFeature(fid)

        # Збереження змін
        if roads_layer.isEditable():
            roads_layer.commitChanges()
        else:
            print("Шар не зміг перейти в режим редагування.")

        # Extract the initial point from lineStart
        line_start_text = self.lineStart.text()
        try:
            line_start_point = QgsPointXY(*map(float, line_start_text.split(',')))
        except ValueError:
            QMessageBox.critical(self, "Error", "Invalid start point coordinates")
            return

        # Extract building points
        building_points = [(line_start_point.x(), line_start_point.y())]  # Include lineStart as the initial point
        for feature in buildings_layer.getFeatures(QgsFeatureRequest()):
            geom = feature.geometry()
            if geom.wkbType() == QgsWkbTypes.Point:
                point = geom.asPoint()
                building_points.append((point.x(), point.y()))
            elif geom.wkbType() == QgsWkbTypes.MultiPoint:
                for part in geom.asMultiPoint():
                    building_points.append((part.x(), part.y()))

        # Extract road edges
        G = nx.Graph()
        for feature in roads_layer.getFeatures(QgsFeatureRequest()):
            geom = feature.geometry()
            if geom.wkbType() == QgsWkbTypes.LineString:
                line = geom.asPolyline()
                for i in range(len(line) - 1):
                    start, end = line[i], line[i + 1]
                    G.add_edge((start.x(), start.y()), (end.x(), end.y()), weight=start.distance(end))
            elif geom.wkbType() == QgsWkbTypes.MultiLineString:
                for part in geom.asMultiPolyline():
                    for i in range(len(part) - 1):
                        start, end = part[i], part[i + 1]
                        G.add_edge((start.x(), start.y()), (end.x(), end.y()), weight=start.distance(end))

        for feature in network_lines_layer.getFeatures(QgsFeatureRequest()):
            geom = feature.geometry()
            if geom.wkbType() == QgsWkbTypes.LineString:
                line = geom.asPolyline()
                for i in range(len(line) - 1):
                    start, end = line[i], line[i + 1]
                    G.add_edge((start.x(), start.y()), (end.x(), end.y()), weight=start.distance(end))
            elif geom.wkbType() == QgsWkbTypes.MultiLineString:
                for part in geom.asMultiPolyline():
                    for i in range(len(part) - 1):
                        start, end = part[i], part[i + 1]
                        G.add_edge((start.x(), start.y()), (end.x(), end.y()), weight=start.distance(end))

        # Verify that all steiner_nodes exist in the graph
        steiner_nodes = [node for node in building_points if node in G.nodes]

        # Compute the Steiner tree
        steiner_tree_graph = steiner_tree(G, steiner_nodes, weight='weight')

        # Create a new memory layer to store the Steiner tree
        steiner_layer = QgsVectorLayer('LineString?crs=EPSG:2056', 'Steiner Tree', 'memory')
        steiner_provider = steiner_layer.dataProvider()

        # Add Steiner tree edges to the new layer
        features = []
        for u, v in steiner_tree_graph.edges():
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(u[0], u[1]), QgsPointXY(v[0], v[1])]))
            feature.setAttributes([len(features)])  # Assign unique id
            features.append(feature)

        steiner_provider.addFeatures(features)
        steiner_layer.updateExtents()

        # Add the layer to the project
        QgsProject.instance().addMapLayer(steiner_layer)

    def clean_topology(self, layer):
        """Clean the topology of a vector layer."""
        params = {'INPUT': layer, 'OUTPUT': 'memory:'}
        result = processing.run("native:fixgeometries", params)
        return result['OUTPUT']


