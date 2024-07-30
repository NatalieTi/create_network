import os
import math
from scipy.optimize import fsolve
from osgeo import ogr, osr
from PyQt5.QtGui import QStandardItem, QStandardItemModel, QColor
from qgis.PyQt import uic, QtWidgets
from qgis.core import (QgsProject, QgsFeature, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsField, QgsSpatialIndex,
                       QgsWkbTypes, QgsPointXY, QgsFeatureRequest, QgsDropShadowEffect, QgsMarkerSymbol, QgsRasterLayer, QgsVectorLayer, QgsPoint, QgsLineSymbol)
from PyQt5.QtWidgets import QMessageBox, QFileDialog, QDialogButtonBox
from PyQt5 import uic
from PyQt5.QtCore import QVariant
from networkx.algorithms.approximation import steiner_tree
import networkx as nx
import processing
import logging

# Load the .ui file
FORM_CLASS, _ = uic.loadUiType(os.path.join(os.path.dirname(__file__), 'create_network_dialog_base.ui'))

class CreateNetworkDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, iface, parent=None):
        """Constructor."""
        super(CreateNetworkDialog, self).__init__(parent)
        self.setupUi(self)
        self.layers = {}

        # Get the main QGIS interface (to access the map canvas)
        self.iface = iface
        self.mapCanvas = self.iface.mapCanvas()
        
        # Initialize the list filter model
        self.list_filter_model = QStandardItemModel(self)
        self.listFilter.setModel(self.list_filter_model)

        # Connect signal for list view item click
        self.listFilter.clicked.connect(self.zoom_to_group)
        
        # Connect the buttons to their handlers
        self.btnBuild.clicked.connect(self.build_connection)
        self.btnNetwork.clicked.connect(self.connect_network)
        self.btnImpBuild.clicked.connect(self.select_buildings_file)
        self.btnImpHeat.clicked.connect(self.select_heating_file)
        self.btnImpRoads.clicked.connect(self.select_roads_file)
        self.btnFilter.clicked.connect(self.filter_and_group_lines)
        self.btnSimplify.clicked.connect(self.simplify_lines)
        self.btnBend.clicked.connect(self.bend_lines)
        self.btnManual.clicked.connect(self.show_manual_message)
        self.btnDn.clicked.connect(self.calculate_diameter)
        self.dblSpinBox_Density.valueChanged.connect(self.update_density)
        self.dblSpinBox_Viscosity.valueChanged.connect(self.update_viscosity)
        self.spinBox_Temperature.valueChanged.connect(self.update_temperature_difference)
        self.dblSpinBox_Heat_Capacity.valueChanged.connect(self.update_heat_capacity)
        self.btnHeat_Loss.clicked.connect(self.calculate_heat_loss)
        
        # Connect the OK buttons
        self.buttonBox.button(QDialogButtonBox.Ok).clicked.connect(self.create_project_and_import_files)

        # Connect the Path button
        self.bntPath.clicked.connect(self.select_project_path)

        # Initialize variables for file paths
        self.buildings_file_path = None
        self.heating_file_path = None
        self.roads_file_path = None
        self.project_path = None
    
    def open_file_dialog(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Shapefile", "", "Shapefiles (*.shp)", options=options)
        return file_path

    def select_buildings_file(self):
        file_path = self.open_file_dialog()
        if file_path:
            self.buildings_file_path = file_path
            self.lineBuildings.setText(file_path)

    def select_heating_file(self):
        file_path = self.open_file_dialog()
        if file_path:
            self.heating_file_path = file_path
            self.lineHeating.setText(file_path)

    def select_roads_file(self):
        file_path = self.open_file_dialog()
        if file_path:
            self.roads_file_path = file_path
            self.lineRoads.setText(file_path)

    def select_project_path(self):
        options = QFileDialog.Options()
        directory = QFileDialog.getExistingDirectory(self, "Select Directory", "", options=options)
        if directory:
            self.project_path = directory
            self.linePath.setText(directory)

    def create_project_and_import_files(self):
        if not self.project_path:
            QMessageBox.critical(self, "Error", "Please select a path for the new project.")
            return
    
        # Define the new project path
        project_path = os.path.join(self.project_path, 'new_project.qgz')
        gpkg_path = os.path.join(self.project_path, 'new_project_data.gpkg')
    
        # Create a new QGIS project
        self.iface.newProject(False)
        project = QgsProject.instance()
    
        crs = QgsCoordinateReferenceSystem("EPSG:2056")
        project.setCrs(crs)
    
        driver = ogr.GetDriverByName("GPKG")
        data_source = driver.CreateDataSource(gpkg_path)
    
        layers_to_create = [
            ('Roads', ogr.wkbLineString, 'roads.qml'),
            ('Buildings', ogr.wkbPoint, 'buildings.qml', [("heat_kw", ogr.OFTReal)]),  # Add heat_kw attribute here
            ('Heating center', ogr.wkbPoint, 'heating_center.qml'),
            ('Buildings connection', ogr.wkbLineString, 'buildings_connection.qml'),
            ('Network', ogr.wkbLineString, 'network.qml', [("topology", ogr.OFTString)]),
            ('Nodes', ogr.wkbPoint, 'nodes.qml')
        ]
    
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(2056)
    
        for layer_info in layers_to_create:
            layer_name, geom_type, style_path = layer_info[:3]
            layer = data_source.CreateLayer(layer_name, srs, geom_type)
            layer.CreateField(ogr.FieldDefn('id', ogr.OFTInteger))
            
            # If additional fields are specified, add them
            if len(layer_info) > 3 and isinstance(layer_info[3], list):
                for field_name, field_type in layer_info[3]:
                    field_defn = ogr.FieldDefn(field_name, field_type)
                    if field_name == "topology":
                        field_defn.SetWidth(10)
                        field_defn.SetDomainName("topology_domain")
                        domain = ogr.CreateStringAttributeDomain("topology_domain", "Topology values", ogr.OFSTMax)
                        domain.AddField(ogr.FieldDomain("simplified"))
                        domain.AddField(ogr.FieldDomain("bended"))
                        domain.AddField(ogr.FieldDomain("manual"))
                        layer.GetLayerDefn().AddDomain(domain)
                    layer.CreateField(field_defn)
    
        data_source = None
    
        for layer_info in layers_to_create:
            layer_name, style_path = layer_info[0], layer_info[2]
            uri = f"{gpkg_path}|layername={layer_name}"
            layer = QgsVectorLayer(uri, layer_name, "ogr")
    
            # Check if the layer was successfully loaded
            if not layer.isValid():
                print(f"Failed to load layer {layer_name} from {gpkg_path}")
            else:
                # Add layer to the project
                project.addMapLayer(layer)
    
                # Apply the predefined style
                self.apply_layer_style(layer, style_path)
    
        # Add OSM Standard map layer
        osm_uri = "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        osm_layer = QgsRasterLayer(osm_uri, "OSM Standard", "wms")
        if not osm_layer.isValid():
            print("Failed to add OSM Standard map layer!")
        else:
            QgsProject.instance().addMapLayer(osm_layer, False)
            root = QgsProject.instance().layerTreeRoot()
            root.addLayer(osm_layer)
    
        # Save the new project
        project.write(project_path)
    
        # Save and open the new project
        project.write()
        self.iface.addProject(project_path)
    
        self.close()
    
        # Import the selected shapefiles into the new project
        if self.buildings_file_path:
            self.add_features_to_layer('Buildings', self.buildings_file_path)
        if self.heating_file_path:
            self.add_features_to_layer('Heating center', self.heating_file_path)
        if self.roads_file_path:
            self.add_features_to_layer('Roads', self.roads_file_path)
    
    def apply_layer_style(self, layer, style_path):
        qml_path = os.path.join(os.path.dirname(__file__), 'styles', style_path)
        if os.path.exists(qml_path):
            layer.loadNamedStyle(qml_path)
            layer.triggerRepaint()
        else:
            print(f"Style file {qml_path} not found for layer {layer.name()}")


    def add_features_to_layer(self, layer_name, shp_file_path):
        # Load the existing layer
        layer = QgsProject.instance().mapLayersByName(layer_name)[0]
    
        # Load a new layer from a shapefile
        new_layer = QgsVectorLayer(shp_file_path, 'new_layer', 'ogr')
        if not new_layer.isValid():
            QMessageBox.critical(None, "Error", f"Shapefile layer {shp_file_path} failed to load!")
            return
    
        # Get the project's coordinate reference system
        project_crs = QgsProject.instance().crs()
        new_layer_crs = new_layer.crs()
    
        # Create a coordinate transformer
        transform = QgsCoordinateTransform(new_layer_crs, project_crs, QgsProject.instance())
    
        # Start editing the existing layer
        if not layer.isEditable():
            layer.startEditing()
    
        # Add objects from the new layer to the existing layer with coordinate transformation
        for feature in new_layer.getFeatures():
            new_feature = QgsFeature(layer.fields())  # Create a new object with fields from the existing layer
            geom = QgsGeometry(feature.geometry())
            geom.transform(transform)
            new_feature.setGeometry(geom)
    
            for field in layer.fields():
                if field.name() in feature.fields().names():
                    new_feature.setAttribute(field.name(), feature.attribute(field.name()))
                else:
                    if field.name() == "heat_kw":
                        new_feature.setAttribute(field.name(), 0.0)  # Set default value for heat_kw if not present in the shapefile
    
            layer.addFeature(new_feature)
    
        # Finish editing and save changes
        layer.commitChanges()
    
        self.zoom_to_layer_extent(layer)

    def zoom_to_layer_extent(self, layer):
        canvas = self.iface.mapCanvas()
        canvas.setExtent(layer.extent())
        canvas.refresh()

    
    def build_connection(self):
        roads_layer = QgsProject.instance().mapLayersByName("Roads")[0]
        buildings_layer = QgsProject.instance().mapLayersByName("Buildings")[0]
        heat_layer = QgsProject.instance().mapLayersByName("Heating center")[0]

        if not buildings_layer or not roads_layer:
            QMessageBox.critical(self, "Error", "Please select both layers")
            return

        # Get the existing 'Buildings connection' layer
        building_connection_layer = None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == "Buildings connection":
                building_connection_layer = layer
                break

        if not building_connection_layer:
            QMessageBox.critical(self, "Error", "Buildings connection layer not found")
            return

        new_lines_provider = building_connection_layer.dataProvider()
        building_connection_layer.startEditing()  # Start editing the layer

        index = QgsSpatialIndex(flags=QgsSpatialIndex.FlagStoreFeatureGeometries)  # Create a QgsSpatialIndex instance
        for road_feat in roads_layer.getFeatures():
            index.addFeature(road_feat)  # Use addFeature to populate the spatial index

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
                # Set default attributes or no attributes
                line_feat.setAttributes([building_feat.id()])  # Adjust this if default attributes are needed
                features.append(line_feat)

        # Find the nearest point from heat_layer
        heat_points = [feat.geometry().asPoint() for feat in heat_layer.getFeatures()]
        if not heat_points:
            QMessageBox.critical(self, "Error", "No points found in Heating center layer.")
            return

        # Use the first heat point for simplicity
        line_heat_point = heat_points[0]

        # Create a line from the line heat point to its nearest road point
        nearest_ids_line_heat = index.nearestNeighbor(line_heat_point, 1)
        if nearest_ids_line_heat:
            nearest_road_feat_line_heat = next(roads_layer.getFeatures(QgsFeatureRequest().setFilterFid(nearest_ids_line_heat[0])))
            nearest_road_geom_line_heat = nearest_road_feat_line_heat.geometry()
            nearest_point_line_heat = nearest_road_geom_line_heat.closestSegmentWithContext(line_heat_point)[1]

            line_geom_line_heat = QgsGeometry.fromPolyline([QgsPoint(line_heat_point), QgsPoint(nearest_point_line_heat)])
            line_feat_line_heat = QgsFeature()
            line_feat_line_heat.setGeometry(line_geom_line_heat)
            # Set default attributes or no attributes
            line_feat_line_heat.setAttributes([-1])  # Use -1 as a placeholder for lineHeat point, adjust if needed
            features.append(line_feat_line_heat)

        new_lines_provider.addFeatures(features)
        building_connection_layer.commitChanges()  # Commit changes to the layer

        QMessageBox.information(self, "Success", "Buildings connection created successfully")

    def connect_network(self):
        # Get layers by name from the project
        build_connection_layer = QgsProject.instance().mapLayersByName("Buildings connection")[0]
        roads_layer = QgsProject.instance().mapLayersByName("Roads")[0]
        buildings_layer = QgsProject.instance().mapLayersByName("Buildings")[0]
        heat_layer = QgsProject.instance().mapLayersByName("Heating center")[0]
        node_layer = QgsProject.instance().mapLayersByName("Nodes")[0]

        if not build_connection_layer or not roads_layer or not buildings_layer or not heat_layer:
            QMessageBox.critical(self, "Error", "Please make sure all layers are loaded.")
            return

        # Snap geometries in the roads layer
        snap_params = {
            'INPUT': roads_layer,
            'REFERENCE_LAYER': build_connection_layer,
            'TOLERANCE': 0.1,
            'BEHAVIOR': 0,
            'OUTPUT': 'memory:'  # Use memory to store results
        }
        snap_result = processing.run("native:snapgeometries", snap_params)
        snapped_roads_layer = snap_result['OUTPUT']

        print("Road is snapped")

        if roads_layer.startEditing():
            roads_layer.dataProvider().truncate()
            for feature in snapped_roads_layer.getFeatures():
                roads_layer.addFeature(feature)
            roads_layer.commitChanges()

            if not roads_layer.isEditable():
                roads_layer.startEditing()

            # Step 1: Convert to Network Graph
            G = nx.Graph()

            # Add edges to the graph
            for feature in roads_layer.getFeatures():
                geom = feature.geometry()
                if geom.type() in (QgsWkbTypes.LineGeometry, QgsWkbTypes.MultiLineString):
                    parts = geom.asMultiPolyline() if geom.isMultipart() else [geom.asPolyline()]
                    for part in parts:
                        for i in range(len(part) - 1):
                            start_point = QgsPointXY(part[i])
                            end_point = QgsPointXY(part[i + 1])
                            G.add_edge((start_point.x(), start_point.y()), (end_point.x(), end_point.y()), fid=feature.id())

            # Step 2: Find Connected Components
            connected_components = list(nx.connected_components(G))

            # Step 3: Isolate Main Component
            main_component = max(connected_components, key=len)

            # Step 4: Remove Small Components
            features_to_delete = set()
            for component in connected_components:
                if component != main_component:
                    for node in component:
                        edges = G.edges(node, data=True)
                        for edge in edges:
                            features_to_delete.add(edge[2]['fid'])

            for fid in features_to_delete:
                roads_layer.deleteFeature(fid)

            # Delete identified features
            if roads_layer.isEditable():
                roads_layer.commitChanges()
            else:
                print("Layer could not enter editing mode.")

            print(f"Deleted {len(features_to_delete)} features.")

            print("Road is cleaned")

        # Extract the nearest point from the heat layer
        heat_points = [feat.geometry().asPoint() for feat in heat_layer.getFeatures()]
        if not heat_points:
            QMessageBox.critical(self, "Error", "No points found in Heating center layer.")
            return

        # Find the nearest point in the heat layer to the first building point
        building_points = [feat.geometry().asPoint() for feat in buildings_layer.getFeatures()]
        if not building_points:
            QMessageBox.critical(self, "Error", "No points found in Buildings layer.")
            return

        nearest_heat_point = min(heat_points, key=lambda point: point.distance(building_points[0]))
        line_start_point = QgsPointXY(nearest_heat_point.x(), nearest_heat_point.y())

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

        for feature in build_connection_layer.getFeatures(QgsFeatureRequest()):
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

        print("Graph is built")


        # Verify that all steiner_nodes exist in the graph
        steiner_nodes = [node for node in building_points if node in G.nodes]

        # Compute the Steiner tree
        steiner_tree_graph = steiner_tree(G, steiner_nodes, weight='weight')

        # Get the existing 'Network' layer
        network_lines_layer = QgsProject.instance().mapLayersByName("Network")[0]

        # Add Steiner tree edges to the existing Network layer
        if network_lines_layer.startEditing():
            network_provider = network_lines_layer.dataProvider()

            # Add Steiner tree edges to the existing Network layer
            features = []
            for u, v in steiner_tree_graph.edges():
                feature = QgsFeature()
                feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(u[0], u[1]), QgsPointXY(v[0], v[1])]))
                features.append(feature)

            network_provider.addFeatures(features)
            network_lines_layer.commitChanges()
            network_lines_layer.updateExtents()

            print("Network is updated")

        else:
            QMessageBox.critical(self, "Error", "Network layer is not editable")
            return
        
        # Function to add a field if it doesn't exist
        def add_field_if_not_exists(layer, field_name, field_type):
            if field_name not in [field.name() for field in layer.fields()]:
                layer.startEditing()
                layer.addAttribute(QgsField(field_name, field_type))
                layer.commitChanges()
        
        # Add required fields to the nodes layer
        add_field_if_not_exists(node_layer, "heat_kw", QVariant.Double)
        add_field_if_not_exists(node_layer, "nr_con", QVariant.Int)
        
        
        def extract_endpoints(line_geom):
            end_points = []
            if line_geom.isMultipart():
                for part in line_geom.asMultiPolyline():
                    end_points.append(part[0])  # Start point of each part
                    end_points.append(part[-1])  # End point of each part
            else:
                end_points.append(line_geom.asPolyline()[0])  # Start point
                end_points.append(line_geom.asPolyline()[-1])  # End point
            return end_points
        
        # Function to get the intersection points
        def find_intersection_points(line_layer):
            intersection_points = {}
            for feature in line_layer.getFeatures():
                geom = feature.geometry()
                points = extract_endpoints(geom)
                for point in points:
                    point_tuple = (point.x(), point.y())
                    if point_tuple in intersection_points:
                        intersection_points[point_tuple] += 1
                    else:
                        intersection_points[point_tuple] = 1
            return [QgsPointXY(x, y) for (x, y), count in intersection_points.items() if count >= 3]
        
        # Get all intersection points from the line layer
        intersection_points = find_intersection_points(network_lines_layer)
        
        # Debug: Print intersection points
        print("Intersection Points: ", intersection_points)
        
        def add_points_to_node_layer(intersection_points, node_layer):
            node_layer.startEditing()
            for point in intersection_points:
                feature = QgsFeature(node_layer.fields())
                feature.setGeometry(QgsGeometry.fromPointXY(point))  # Updated to fromPointXY
                node_layer.addFeature(feature)
            node_layer.commitChanges()
        
        # Add dead ends and intersections to the node layer
        add_points_to_node_layer(intersection_points, node_layer)
        
        print("Nodes added successfully.")
        
        # Create a spatial index for the building layer
        building_index = QgsSpatialIndex(buildings_layer.getFeatures())
        
        # Update nodes with heat_kw from buildings
        node_layer.startEditing()
        for node in node_layer.getFeatures():
            node_geom = node.geometry()
            nearest_building_ids = building_index.nearestNeighbor(node_geom.asPoint(), 1)
            if nearest_building_ids:
                nearest_building = buildings_layer.getFeature(nearest_building_ids[0])
                heat_kw = nearest_building["heat_kw"]
                if isinstance(heat_kw, QVariant):
                    heat_kw = heat_kw.toDouble()[0]  # Convert QVariant to double
                node["heat_kw"] = heat_kw
                node_layer.updateFeature(node)
        node_layer.commitChanges()
        
        # Create a directed graph
        G = nx.DiGraph()
        
        # Add nodes to the graph
        for node in node_layer.getFeatures():
            node_id = node.id()
            heat_kw = node["heat_kw"]
            G.add_node(node_id, heat_kw=heat_kw if isinstance(heat_kw, (int, float)) else 0)
        
        # Create a spatial index for the node layer to find the nearest nodes for each line
        node_index = QgsSpatialIndex(node_layer.getFeatures())
        
        # Add edges to the graph
        for feature in network_lines_layer.getFeatures():
            geom = feature.geometry()
            if geom.isMultipart():
                lines = geom.asMultiPolyline()
            else:
                lines = [geom.asPolyline()]
            for line in lines:
                start_point = line[0]
                end_point = line[-1]
                start_node_id = node_index.nearestNeighbor(QgsGeometry.fromPointXY(QgsPointXY(start_point)), 1)[0]  # Updated to fromPointXY
                end_node_id = node_index.nearestNeighbor(QgsGeometry.fromPointXY(QgsPointXY(end_point)), 1)[0]  # Updated to fromPointXY
                if start_node_id and end_node_id:
                    G.add_edge(start_node_id, end_node_id)
        
        # Function to calculate descendants and heat_kw sum
        def calculate_descendants_and_heat_kw(graph, node_id):
            print(node_id)
            descendants = nx.descendants(graph, node_id)
            nr_con = 0
            heat_kw_sum = 0
            for d in descendants:
                heat_kw = graph.nodes[d]["heat_kw"]
                if isinstance(heat_kw, (int, float)) and heat_kw != 0:
                    nr_con += 1
                    heat_kw_sum += heat_kw
            return nr_con, heat_kw_sum
        
        # Update nodes with nr_con and heat_kw
        node_layer.startEditing()
        for node in node_layer.getFeatures():
            node_id = node.id()
            nr_con, heat_kw_sum = calculate_descendants_and_heat_kw(G, node_id)
            node["nr_con"] = nr_con
            if nr_con > 1:
                node["heat_kw"] = heat_kw_sum
            node_layer.updateFeature(node)
        node_layer.commitChanges()
        
        print("Nodes updated with nr_con and heat_kw values successfully.")

        QMessageBox.information(self, "Success", "Network and nodes created successfully")
                                                                                                                             
    
    def clean_topology(self, layer):
        """Clean the topology of a vector layer."""
        params = {'INPUT': layer, 'OUTPUT': 'memory:'}
        result = processing.run("native:fixgeometries", params)
        return result['OUTPUT']

    def filter_and_group_lines(self):
        def calculate_angle(line1, line2):
            x1, y1 = line1[0]
            x2, y2 = line1[1]
            x3, y3 = line2[0]
            x4, y4 = line2[1]
    
            vector1 = (x2 - x1, y2 - y1)
            vector2 = (x4 - x3, y4 - y3)
    
            length1 = math.sqrt(vector1[0]**2 + vector1[1]**2)
            length2 = math.sqrt(vector2[0]**2 + vector2[1]**2)
    
            dot_product = vector1[0] * vector2[0] + vector1[1] * vector2[1]
    
            cos_angle = dot_product / (length1 * length2)
            cos_angle = max(-1, min(1, cos_angle))
    
            angle = math.acos(cos_angle)
            angle_degrees = math.degrees(angle)
    
            return angle_degrees
    
        def line_length(line_geom, threshold=0.5):
            return line_geom.length() > threshold
    
        def extract_endpoints(line_geom):
            points = []
            if line_geom.isMultipart():
                for part in line_geom.asMultiPolyline():
                    points.append(part[0])  # Start point of each part
                    points.append(part[-1])  # End point of each part
            else:
                points.append(line_geom.asPolyline()[0])  # Start point
                points.append(line_geom.asPolyline()[-1])  # End point
            return points
    
        def find_connected_lines(line_layer):
            endpoints_dict = {}
            connected_lines = {}
    
            for feat in line_layer.getFeatures():
                geom = feat.geometry()
                endpoints = extract_endpoints(geom)
                for point in endpoints:
                    point_tuple = (point.x(), point.y())
                    if point_tuple not in endpoints_dict:
                        endpoints_dict[point_tuple] = []
                    endpoints_dict[point_tuple].append(feat.id())
    
            for endpoints, line_ids in endpoints_dict.items():
                if len(line_ids) > 1:
                    for line_id in line_ids:
                        if line_id not in connected_lines:
                            connected_lines[line_id] = set()
                        connected_lines[line_id].update(line_ids)
                        connected_lines[line_id].remove(line_id)
    
            return connected_lines
    
        def get_intersection_points_from_nodes():
            nodes_layer = QgsProject.instance().mapLayersByName("Nodes")[0]
            intersection_points = []
            for feature in nodes_layer.getFeatures():
                geom = feature.geometry()
                if geom.isMultipart():
                    points = geom.asMultiPoint()
                else:
                    points = [geom.asPoint()]
                for point in points:
                    intersection_points.append(QgsPointXY(point.x(), point.y()))
            return intersection_points
    
        def filter_lines_by_angle_and_length(line_layer, connected_lines):
            filtered_lines = {}
    
            for line_id, connected_ids in connected_lines.items():
                line_feat = line_layer.getFeature(line_id)
                line_geom = line_feat.geometry()
    
                if line_length(line_geom):
                    for connected_id in connected_ids:
                        if line_id != connected_id:
                            connected_feat = line_layer.getFeature(connected_id)
                            connected_geom = connected_feat.geometry()
    
                            angle_degrees = calculate_angle(line_geom.asPolyline(), connected_geom.asPolyline())
    
                            if 20 < angle_degrees > 90:
                                if line_id not in filtered_lines:
                                    filtered_lines[line_id] = {'connected_ids': set(), 'reasons': []}
                                filtered_lines[line_id]['connected_ids'].add(connected_id)
                                filtered_lines[line_id]['reasons'].append("angle less than 20° or greater than 90°")
    
                else:
                    if line_id not in filtered_lines:
                        filtered_lines[line_id] = {'connected_ids': set(), 'reasons': []}
                    filtered_lines[line_id]['reasons'].append("line is less than 0.5 m")
    
            return filtered_lines
    
        def split_groups_at_intersections(filtered_lines, intersection_points):
            final_groups = []
            line_to_group = {}
            print("Filtered lines", str(filtered_lines))
            for line_id, data in filtered_lines.items():
                if line_id not in line_to_group:
                    group = set()
                    group.add(line_id)
                    line_to_group[line_id] = group
    
                    for connected_id in data['connected_ids']:
                        group.add(connected_id)
                        line_to_group[connected_id] = group
    
                    final_groups.append(group)
            print("Final groups ", str(final_groups))
    
            split_groups = []
            for group in final_groups:
                sub_groups = []
                current_sub_group = set()
                visited_lines = set()
    
                for line_id in group:
                    line_feat = line_layer.getFeature(line_id)
                    line_geom = line_feat.geometry()
                    endpoints = extract_endpoints(line_geom)
    
                    intersects = any(QgsPointXY(point.x(), point.y()) in intersection_points for point in endpoints)
    
                    if intersects:
                        if current_sub_group:
                            sub_groups.append(current_sub_group)
                        current_sub_group = set([line_id])
                    else:
                        current_sub_group.add(line_id)
    
                    visited_lines.add(line_id)
                print(sub_groups)
                if current_sub_group:
                    sub_groups.append(current_sub_group)
                split_groups.extend(sub_groups)
    
            return split_groups
    
        def merge_groups_by_points(groups, intersection_points, line_layer):
            point_to_group = {}
            line_to_group = {}
    
            for group in groups:
                for line_id in group:
                    line_feat = line_layer.getFeature(line_id)
                    line_geom = line_feat.geometry()
                    endpoints = extract_endpoints(line_geom)
                    for point in endpoints:
                        point_tuple = (point.x(), point.y())
                        if QgsPointXY(point.x(), point.y()) not in intersection_points:
                            if point_tuple not in point_to_group:
                                point_to_group[point_tuple] = set()
                            point_to_group[point_tuple].add(line_id)
                    if line_id not in line_to_group:
                        line_to_group[line_id] = group
                    else:
                        line_to_group[line_id].update(group)
    
            merged_groups = []
            seen = set()
    
            for group in groups:
                merged_group = set(group)
                for line_id in group:
                    line_feat = line_layer.getFeature(line_id)
                    line_geom = line_feat.geometry()
                    endpoints = extract_endpoints(line_geom)
                    for point in endpoints:
                        point_tuple = (point.x(), point.y())
                        if QgsPointXY(point.x(), point.y()) not in intersection_points and point_tuple in point_to_group:
                            for connected_line_id in point_to_group[point_tuple]:
                                if connected_line_id not in merged_group:
                                    merged_group.add(connected_line_id)
                        if line_id in line_to_group:
                            for connected_line_id in line_to_group[line_id]:
                                if connected_line_id not in merged_group:
                                    merged_group.add(connected_line_id)
    
                if not merged_group.isdisjoint(seen):
                    for existing_group in merged_groups:
                        if not merged_group.isdisjoint(existing_group):
                            existing_group.update(merged_group)
                            break
                else:
                    merged_groups.append(merged_group)
                seen.update(merged_group)
    
            unique_merged_groups = []
            all_seen_lines = set()
            for group in merged_groups:
                unique_group = set(group)
                if unique_group not in unique_merged_groups and not unique_group.intersection(all_seen_lines):
                    unique_merged_groups.append(unique_group)
                    all_seen_lines.update(unique_group)
    
            connecting_lines = []
            line_to_connecting_groups = {}
            for line_feat in line_layer.getFeatures():
                line_geom = line_feat.geometry()
                endpoints = extract_endpoints(line_geom)
                groups_found = set()
                for point in endpoints:
                    point_tuple = (point.x(), point.y())
                    if point_tuple in point_to_group:
                        for line_id in point_to_group[point_tuple]:
                            for group in unique_merged_groups:
                                if line_id in group:
                                    groups_found.add(tuple(group))
                if len(groups_found) > 1:
                    connecting_lines.append(line_feat.id())
                    line_to_connecting_groups[line_feat.id()] = groups_found
                    print(f"Line {line_feat.id()} connects groups: {groups_found}")
    
            for line_id, groups_found in line_to_connecting_groups.items():
                groups_to_merge = list(groups_found)
                if len(groups_to_merge) > 1:
                    group_1, group_2 = groups_to_merge[0], groups_to_merge[1]
                    for group in unique_merged_groups:
                        if group == set(group_1):
                            group.update(group_2)
                            group.add(line_id)
                        elif group == set(group_2):
                            group.update(group_1)
                            group.add(line_id)
    
            def find_group(line_id, group_mapping):
                for group in group_mapping:
                    if line_id in group:
                        return group
                return None
    
            final_merged_groups = []
            group_mapping = []
    
            for group in unique_merged_groups:
                new_group = set(group)
                for line_id in group:
                    existing_group = find_group(line_id, group_mapping)
                    if existing_group:
                        new_group.update(existing_group)
                        group_mapping.remove(existing_group)
                group_mapping.append(new_group)
    
            seen = set()
            for group in group_mapping:
                frozen_group = frozenset(group)
                if frozen_group not in seen:
                    final_merged_groups.append(group)
                    seen.add(frozen_group)
    
            return final_merged_groups
    
        line_layer = QgsProject.instance().mapLayersByName("Network")[0]
        connected_lines = find_connected_lines(line_layer)
        filtered_lines = filter_lines_by_angle_and_length(line_layer, connected_lines)
        intersection_points = get_intersection_points_from_nodes()
        final_groups = split_groups_at_intersections(filtered_lines, intersection_points)
        final_groups = merge_groups_by_points(final_groups, intersection_points, line_layer)
    
        self.update_list_filter(final_groups, filtered_lines)
    
    def update_list_filter(self, line_groups, filtered_lines):
        self.list_filter_model.clear()
        self.final_groups = []
    
        group_number = 1
        for group in line_groups:
            if len(group) > 1:
                item_text = f"Group {group_number}"
                reasons = set()
                for line_id in group:
                    if line_id in filtered_lines:
                        reasons.update(filtered_lines[line_id]['reasons'])
                if reasons:
                    item_text += " - " + ", ".join(reasons)
                item = QStandardItem(item_text)
                self.list_filter_model.appendRow(item)
                self.final_groups.append(list(group))
                group_number += 1
    
    def zoom_to_group(self, index):
        item = self.list_filter_model.itemFromIndex(index)
        group_info = item.text().split(" ")
        group_index = int(group_info[1]) - 1
    
        if group_index < 0 or not self.final_groups or group_index >= len(self.final_groups):
            QMessageBox.critical(self, "Error", "Group index out of range")
            return
    
        line_layer = QgsProject.instance().mapLayersByName("Network")[0]
        if not line_layer:
            QMessageBox.critical(self, "Error", "Network layer not found")
            return
    
        self.selected_group_index = group_index  # Store the selected group index
        line_layer.removeSelection()
    
        grouped_lines = self.final_groups[group_index]
    
        feature_ids = []
        for feature_id in grouped_lines:
            if isinstance(feature_id, tuple):
                feature_id = feature_id[0]
            feature_id = int(feature_id)
            feature = line_layer.getFeature(feature_id)
            if feature:
                feature_ids.append(feature.id())
    
        if feature_ids:
            line_layer.selectByIds(feature_ids)
            self.iface.mapCanvas().zoomToSelected(line_layer)
        else:
            QMessageBox.critical(self, "Error", "No valid features found in the group")
    

    def simplify_lines(self):
        if self.selected_group_index is None:
            QMessageBox.critical(self, "Error", "No group selected.")
            return

        line_layer = QgsProject.instance().mapLayersByName("Network")[0]

        if not line_layer:
            QMessageBox.critical(self, "Error", "Network layer not found")
            return

        if line_layer.fields().indexFromName('topology') == -1:
            line_layer.dataProvider().addAttributes([QgsField('topology', QVariant.String)])
            line_layer.updateFields()

        group = self.final_groups[self.selected_group_index]
        merged_geom = None
        feature_ids_to_delete = []

        for line_id in group:
            feature = line_layer.getFeature(line_id)
            if feature:
                geom = feature.geometry()
                if merged_geom is None:
                    merged_geom = QgsGeometry(geom)
                else:
                    merged_geom = merged_geom.combine(geom)
                feature_ids_to_delete.append(feature.id())  # Collect feature IDs to delete

        if merged_geom is not None:
            # Simplify the merged geometry to 2 points
            simplified_geom = self.simplify_to_2_points(merged_geom)

            # Create a new feature with the simplified geometry
            new_feature = QgsFeature(line_layer.fields())  # Ensure new feature has all fields
            new_feature.setGeometry(simplified_geom)
            new_feature.setAttribute('topology', 'simplified')

            line_layer.startEditing()

            # Delete old features
            for feature_id in feature_ids_to_delete:
                line_layer.deleteFeature(feature_id)

            # Add new simplified feature
            line_layer.addFeature(new_feature)
            line_layer.commitChanges()

        self.iface.mapCanvas().refresh()
        QMessageBox.information(self, "Success", "Lines simplified successfully")

    def simplify_to_2_points(self, geom):
        if geom.type() == QgsWkbTypes.LineGeometry:
            points = geom.asPolyline()
        elif geom.type() == QgsWkbTypes.MultiLineString:
            points = geom.asMultiPolyline()[0]
        else:
            return geom

        if len(points) > 2:
            simplified_points = [QgsPoint(points[0]), QgsPoint(points[-1])]
            simplified_geom = QgsGeometry.fromPolyline(simplified_points)
        else:
            simplified_geom = geom

        return simplified_geom
    
    def bend_lines(self):
        if self.selected_group_index is None:
            QMessageBox.critical(self, "Error", "No group selected.")
            return
    
        line_layer = QgsProject.instance().mapLayersByName("Network")[0]
    
        if not line_layer:
            QMessageBox.critical(self, "Error", "Network layer not found")
            return

        if line_layer.fields().indexFromName('topology') == -1:
            line_layer.dataProvider().addAttributes([QgsField('topology', QVariant.String)])
            line_layer.updateFields()
    
        group = self.final_groups[self.selected_group_index]
        merged_geom = None
        feature_ids_to_delete = []
    
        for line_id in group:
            feature = line_layer.getFeature(line_id)
            if feature:
                geom = feature.geometry()
                if merged_geom is None:
                    merged_geom = QgsGeometry(geom)
                else:
                    merged_geom = merged_geom.combine(geom)
                feature_ids_to_delete.append(feature.id())  # Collect feature IDs to delete
    
        if merged_geom is not None:
            # Smooth the merged geometry
            smoothed_geom = self.smooth_geometry(merged_geom)
    
            # Create a new feature with the smoothed geometry
            new_feature = QgsFeature(line_layer.fields())  # Ensure new feature has all fields
            new_feature.setGeometry(smoothed_geom)
            new_feature.setAttribute('topology', 'bended')
    
            line_layer.startEditing()
    
            # Delete old features
            for feature_id in feature_ids_to_delete:
                line_layer.deleteFeature(feature_id)
    
            # Add new smoothed feature
            line_layer.addFeature(new_feature)
            line_layer.commitChanges()
    
        self.iface.mapCanvas().refresh()
        QMessageBox.information(self, "Success", "Lines bended successfully")
    
    def smooth_geometry(self, geom):
        if geom.type() == QgsWkbTypes.LineGeometry:
            points = geom.asPolyline()
        elif geom.type() == QgsWkbTypes.MultiLineString:
            points = geom.asMultiPolyline()[0]
        else:
            return geom
    
        if len(points) > 1:
            # Remove duplicate points
            unique_points = [points[0]]
            for pt in points[1:]:
                if pt != unique_points[-1]:
                    unique_points.append(pt)
    
            if len(unique_points) > 1:
                # Apply Chaikin's algorithm
                smoothed_points = self.chaikin_smooth(unique_points, iterations=2)
    
                # Ensure the first and last points are the same as the original
                smoothed_points[0] = QgsPoint(unique_points[0].x(), unique_points[0].y())
                smoothed_points[-1] = QgsPoint(unique_points[-1].x(), unique_points[-1].y())
    
                smoothed_geom = QgsGeometry.fromPolyline(smoothed_points)
            else:
                smoothed_geom = QgsGeometry.fromPolyline([QgsPoint(p.x(), p.y()) for p in unique_points])
        else:
            smoothed_geom = QgsGeometry.fromPolyline([QgsPoint(p.x(), p.y()) for p in points])
    
        return smoothed_geom
    
    def chaikin_smooth(self, points, iterations=2):
        for _ in range(iterations):
            new_points = []
            for i in range(len(points) - 1):
                p0 = points[i]
                p1 = points[i + 1]
                q = QgsPoint(0.75 * p0.x() + 0.25 * p1.x(), 0.75 * p0.y() + 0.25 * p1.y())
                r = QgsPoint(0.25 * p0.x() + 0.75 * p1.x(), 0.25 * p0.y() + 0.75 * p1.y())
                new_points.extend([q, r])
            new_points.append(points[-1])
            points = new_points
        return points
    
    def show_manual_message(self):
        QMessageBox.information(self, "Manual Change", "Please change the topology manually")
        line_layer = QgsProject.instance().mapLayersByName("Network")[0]

        if not line_layer:
            QMessageBox.critical(self, "Error", "Network layer not found")
            return

        # Add the 'topology' field if it does not exist
        if line_layer.fields().indexFromName('topology') == -1:
            line_layer.dataProvider().addAttributes([QgsField('topology', QVariant.String)])
            line_layer.updateFields()

        group = self.final_groups[self.selected_group_index]
        merged_geom = None
        feature_ids_to_delete = []

        for line_id in group:
            feature = line_layer.getFeature(line_id)
            if feature:
                geom = feature.geometry()
                if merged_geom is None:
                    merged_geom = QgsGeometry(geom)
                else:
                    merged_geom = merged_geom.combine(geom)
                feature_ids_to_delete.append(feature.id())  # Collect feature IDs to delete

        if merged_geom is not None:
            # Create a new feature with the same geometry
            new_feature = QgsFeature(line_layer.fields())  # Ensure new feature has all fields
            new_feature.setGeometry(merged_geom)
            new_feature.setAttribute('topology', 'manual')

            line_layer.startEditing()

            # Delete old features
            for feature_id in feature_ids_to_delete:
                line_layer.deleteFeature(feature_id)

            # Add new feature
            line_layer.addFeature(new_feature)
            line_layer.commitChanges()

        self.iface.mapCanvas().refresh()
        QMessageBox.information(self, "Success", "Topology set to manual")


    def update_density(self, value):
        self.density = value
        print(f"Density updated to: {self.density}")

    def update_viscosity(self, value):
        self.viscosity = value
        print(f"Viscosity updated to: {self.viscosity}")

    def update_temperature_difference(self, value):
        self.temperature_difference = value
        print(f"Temperature difference updated to: {self.temperature_difference}")

    def update_heat_capacity(self, value):
        self.Cp = value
        print(f"Heat capacity updated to: {self.Cp}")
    
    def calculate_diameter(self):
        D = 0.62
        d = 0.8  # Example value, can be changed

        # density = 1000  # Density of the liquid, kg/m³
        # viscosity = 0.0009  # Viscosity of the liquid, Pa·s
        # temperature_difference = 30  # Temperature difference, °C
        # Cp = 4.186  # Specific heat capacity, kJ/(kg·°C)
        
        # Pipe parameters
        pipe_length = 1000  # Length of the pipe, m
        roughness = 0.0001  # Roughness of the pipe, m
        name_pipes = [20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 350, 400, 450]
        pipe_diameters = [0.0217, 0.0273, 0.0360, 0.0419, 0.0539, 0.0697, 0.0825, 0.1071, 0.1325, 0.1603, 0.2101, 0.2630, 0.3127,  0.3444, 0.3938, 0.4446]  # Various pipe diameters, m
        max_pressure_loss = 150000  # Maximum pressure loss, Pa
        step = 0.01  # Velocity change step, m/s

        # Ensure lists have the same length
        assert len(pipe_diameters) == len(name_pipes), "pipe_diameters and name_pipes lists must have the same length"

        # Calculation functions
        def calculate_pressure_loss(friction_factor, pipe_length, pipe_diameter, density, velocity):
            return friction_factor * pipe_length * density * pow(velocity, 2) / (2 * pipe_diameter)

        def calculate_reynolds_number(density, velocity, pipe_diameter, viscosity):
            return (density * velocity * pipe_diameter) / viscosity

        def colebrook(f, reynolds, roughness, pipe_diameter):
            f = f[0]  # Extract single element from array
            return 1 / math.sqrt(f) + 2 * math.log10(roughness / (3.7 * pipe_diameter) + 2.51 / (reynolds * math.sqrt(f)))

        def calculate_friction_factor(reynolds, roughness, pipe_diameter):
            if reynolds < 2000:
                # Laminar flow
                return 64 / reynolds if reynolds != 0 else float('inf')
            else:
                # Turbulent flow, use Colebrook equation
                initial_guess = [0.02]
                friction_factor, = fsolve(colebrook, initial_guess, args=(reynolds, roughness, pipe_diameter))
                return friction_factor

        def calculate_heat_flow(density, velocity, pipe_diameter, viscosity, temperature_difference, Cp):
            area = (math.pi * pow(pipe_diameter / 2, 2))
            return area * density * velocity * Cp * temperature_difference

        # Calculating heat flow ranges
        heat_flow_ranges = {}
        for pipe_diameter, name_pipe in zip(pipe_diameters, name_pipes):
            velocity = 0  # Initial velocity, m/s
            max_heat_flow = 0
            
            while velocity <= 5:
                reynolds = calculate_reynolds_number(self.density, velocity, pipe_diameter, self.viscosity)
                friction_factor = calculate_friction_factor(reynolds, roughness, pipe_diameter)
                
                if friction_factor == float('inf'):
                    pressure_loss = 0
                else:
                    pressure_loss = calculate_pressure_loss(friction_factor, pipe_length, pipe_diameter, self.density, velocity)
                
                if pressure_loss >= max_pressure_loss:
                    max_heat_flow = calculate_heat_flow(self.density, velocity, pipe_diameter, self.viscosity, self.temperature_difference, self.Cp)
                    break

                velocity += step
            else:
                max_heat_flow = calculate_heat_flow(self.density, 5, pipe_diameter, self.viscosity, self.temperature_difference, self.Cp)
            
            heat_flow_ranges[name_pipe] = max_heat_flow

        # Load layers
        point_layer = QgsProject.instance().mapLayersByName("Nodes")[0]
        line_layer = QgsProject.instance().mapLayersByName("Network")[0]

        if not point_layer:
            raise ValueError(f"Layer 'Nodes' not found.")
        if not line_layer:
            raise ValueError(f"Layer 'Network' not found.")

        # Ensure point_layer is valid
        if not point_layer.isValid():
            raise ValueError("Point layer is not valid")

        # Add new columns if they don't exist
        if point_layer.fields().indexFromName('qs_kw') == -1:
            point_layer.dataProvider().addAttributes([QgsField('qs_kw', QVariant.Double)])
            point_layer.updateFields()

        # Calculate qs_kw for each point
        def calculate_qs_kw_for_points():
            for point_feature in point_layer.getFeatures():
                if point_feature.geometry() is None or point_feature.geometry().isEmpty():
                    continue

                # Check for the existence of attributes
                attribute_names = [field.name() for field in point_layer.fields()]
                if 'heat_kw' not in attribute_names or 'nr_con' not in attribute_names:
                    logging.warning(f"Missing attributes in point ID: {point_feature.id()}")
                    continue

                heat_kw = point_feature['heat_kw']
                connected_houses = point_feature['nr_con']
                
                if heat_kw is None or connected_houses is None:
                    logging.warning(f"Missing heat_kw or nr_con for point ID: {point_feature.id()}")
                    continue

                n = connected_houses
                qs_kw = heat_kw * (D + (1 - D) / n) if n > 0 else 0

                point_layer.dataProvider().changeAttributeValues({point_feature.id(): {point_layer.fields().indexFromName('qs_kw'): qs_kw}})
                logging.info(f"Node ID: {point_feature.id()}, qs_kw: {qs_kw}")

        # Call function to calculate qs_kw for points
        calculate_qs_kw_for_points()

        # Create spatial index for point layer
        spatial_index = QgsSpatialIndex(point_layer.getFeatures())

        # Ensure line_layer is a valid vector layer with line geometries
        if not isinstance(line_layer, QgsVectorLayer) or line_layer.geometryType() != QgsWkbTypes.LineGeometry:
            raise ValueError(f"Layer 'Network' is not a valid line layer")

        # Add dn column if it doesn't exist
        if line_layer.fields().indexFromName('dn') == -1:
            line_layer.dataProvider().addAttributes([QgsField('dn', QVariant.Int)])
            line_layer.updateFields()

        # Add qs_kw column if it doesn't exist
        if line_layer.fields().indexFromName('qs_kw') == -1:
            line_layer.dataProvider().addAttributes([QgsField('qs_kw', QVariant.Double)])
            line_layer.updateFields()

        # Update dn attribute for each line feature based on qs_kw
        line_layer.startEditing()

        for feature in line_layer.getFeatures():
            geometry = feature.geometry()
            if geometry.isMultipart():
                line = geometry.asMultiPolyline()[0]  # Assuming we take the first part of multi-part geometries
            else:
                line = geometry.asPolyline()

            if line:
                nearest_id = spatial_index.nearestNeighbor(QgsGeometry.fromPointXY(line[-1]), 1)
                if nearest_id:
                    nearest_feature = next(point_layer.getFeatures(QgsFeatureRequest(nearest_id[0])))
                    if nearest_feature:
                        nearest_qs_kw = nearest_feature['qs_kw']
                        if nearest_qs_kw is not None:
                            feature['qs_kw'] = nearest_qs_kw
                            line_layer.updateFeature(feature)
                            logging.info(f"Updated qs_kw for line feature ID: {feature.id()} with value from nearest node ID: {nearest_feature.id()}")
                        else:
                            logging.warning(f"qs_kw attribute is None in nearest feature ID: {nearest_feature.id()}")
                    else:
                        logging.warning(f"Could not find nearest feature for feature ID: {feature.id()}")
                else:
                    logging.warning(f"No nearest neighbor found for feature ID: {feature.id()}")
            else:
                logging.warning(f"Line geometry is empty for feature ID: {feature.id()}")

        # Update dn attribute based on heat flow ranges
        for feature in line_layer.getFeatures():
            if feature['qs_kw'] is None:
                logging.warning(f"Missing qs_kw for line feature ID: {feature.id()}")
                continue
            heat_kw = feature['qs_kw']
            found = False
            for dn, max_heat_flow in heat_flow_ranges.items():
                if heat_kw <= max_heat_flow:
                    feature['dn'] = dn
                    found = True
                    break
            if not found:
                logging.warning(f"Heat flow {heat_kw} exceeds all ranges for feature ID: {feature.id()}")
            line_layer.updateFeature(feature)

        line_layer.commitChanges()

        # Remove qs_kw attribute from Network layer
        if line_layer.fields().indexFromName('qs_kw') != -1:
            line_layer.dataProvider().deleteAttributes([line_layer.fields().indexFromName('qs_kw')])
            line_layer.updateFields()

        # Repaint layers to update the view
        point_layer.updateFields()
        point_layer.triggerRepaint()
        line_layer.updateFields()
        line_layer.triggerRepaint()

        # Show success message
        QMessageBox.information(self, "Calculation Complete", "Diameters have been calculated successfully.")

    def calculate_heat_loss(self):
        t_f = 90
        t_r = 60
        t_s = 10
        lambda_s = 2
        lambda_i = 0.03
        lambda_g = 1
        C = 0.5
        D_c = 300
        D_pur = 295
        d_0 = 166.7
        Z = 1.5
        R0 = 0.0685
        H = 1
        D = 1
        r_i = 1
        Zc = Z + (R0 * lambda_s)
        omega = (lambda_i - lambda_g) / (lambda_i + lambda_g)

        def calculate_insulane_of_the_soil():
            Rs = (1 / (2 * math.pi * lambda_s)) * math.log((4 * Zc) / D_c)
            return Rs
        
        Rs = calculate_insulane_of_the_soil()
        print("Rs=", Rs)

        def calculate_insulane_of_the_insulation_material():
            Ri = (1 / (2 * math.pi * lambda_i)) * math.log(D_pur / d_0)
            return Ri
        
        Ri = calculate_insulane_of_the_insulation_material()
        print("Ri=", Ri)

        def insulance_of_the_heat_exchange_between_supply_and_return_pipe():
            x = 2 * Zc / C
            Rh = (1 / (4 * math.pi * lambda_s)) * math.log((x ** 2) + 1)
            return Rh
        
        Rh = insulance_of_the_heat_exchange_between_supply_and_return_pipe()
        print('Rh=', Rh)

        def calculate_heat_loss_coefficient():
            x = Rs + Ri
            U1 = (Rs + Ri) / ((x ** 2) - (Rh ** 2))
            U2 = Rh / ((x ** 2) - (Rh ** 2))
            return U1, U2
        
        U1, U2 = calculate_heat_loss_coefficient()
        print(f"U1= '{U1}', U2= '{U2}'")

        def calculate_heat_loss():
            Ff = (U1 * (t_f - t_s)) - (U2 * (t_r - t_s))
            Fr = (U1 * (t_r - t_s)) - (U2 * (t_f - t_s))
            return Ff, Fr
        
        Ff, Fr = calculate_heat_loss()
        print(f"Ff= '{Ff}', Fr= '{Fr}'")

        # def calculate_heat_loss_in_twin_pipes():
            # x = (r_i / (2 * D))
            # y = ((omega * 2 * r_i * (D ** 3)) / ((R0 ** 4) * (D ** 4)))
            # z = (2 * r_i * (R0 ** 2) * D) / ((R0 ** 4) - (D ** 4))
# 
            # Hs = ((2 * lambda_i) / lambda_g) * math.log((2 * H) / R0) + math.log((R0 ** 2) / (2 * D * r_i)) + omega * math.log((R0 ** 4) / ((R0 ** 4) - (D ** 4))) - ((x - y) ** 2) / (1 + (x ** 2) + omega * (z ** 2))
            # return Hs
        # 
        # Hs = calculate_heat_loss_in_twin_pipes()
        # print(f"Hs '{Hs}'")

        # Load layer
        line_layer = QgsProject.instance().mapLayersByName("Network")[0]
        if not line_layer:
            raise ValueError("Layer 'Network' not found.")
        
        if not line_layer.isValid():
            raise ValueError("Line layer is not valid")

        # Add new columns if they don't exist
        if line_layer.fields().indexFromName('Heat_Loss') == -1:
            line_layer.dataProvider().addAttributes([QgsField('Heat_Loss', QVariant.Double)])
            line_layer.updateFields()

        # Calculate and update heat loss for each line feature
        line_layer.startEditing()

        for feature in line_layer.getFeatures():
            feature['Heat_Loss'] = Ff  # Assuming Ff is the calculated heat loss for this example
            line_layer.updateFeature(feature)

        line_layer.commitChanges()

        # Repaint layer to update the view
        line_layer.updateFields()
        line_layer.triggerRepaint()
