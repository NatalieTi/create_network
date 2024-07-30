import os.path
from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import QAction, QLabel, QVBoxLayout, QDialogButtonBox, QDialog
from qgis.core import QgsProject, QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsField, QgsSpatialIndex, QgsLineSymbol, QgsPoint, QgsFeatureRequest
from qgis.gui import QgsMapLayerComboBox, QgsMessageBar
import processing

# Initialize Qt resources from file resources.py
from .resources import *
# Import the code for the dialog
from .create_network_dialog import CreateNetworkDialog

class CreateNetwork:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        # Save reference to the QGIS interface
        self.iface = iface
        # initialize plugin directory
        self.plugin_dir = os.path.dirname(__file__)
        # initialize locale
        locale = str(QSettings().value('locale/userLocale'))[0:2]
        locale_path = os.path.join(
            self.plugin_dir,
            'i18n',
            'CreateNetwork_{}.qm'.format(locale))

        if os.path.exists(locale_path):
            self.translator = QTranslator()
            self.translator.load(locale_path)
            QCoreApplication.installTranslator(self.translator)

        # Declare instance attributes
        self.actions = []
        self.menu = self.tr(u'&Create Network')
        self.first_start = True

    # noinspection PyMethodMayBeStatic
    def tr(self, message):
        """Get the translation for a string using Qt translation API."""
        return QCoreApplication.translate('CreateNetwork', message)

    def add_action(
        self,
        icon_path,
        text,
        callback,
        enabled_flag=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
        parent=None):

        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)

        if status_tip is not None:
            action.setStatusTip(status_tip)

        if whats_this is not None:
            action.setWhatsThis(whats_this)

        if add_to_toolbar:
            self.iface.addToolBarIcon(action)

        if add_to_menu:
            self.iface.addPluginToVectorMenu(self.menu, action)

        self.actions.append(action)

        return action

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        icon_path = ':/plugins/create_network/icon.png'
        self.add_action(
            icon_path,
            text=self.tr(u'Create Network'),
            callback=self.run,
            parent=self.iface.mainWindow())

    def unload(self):
        """Removes the plugin menu item and icon from QGIS GUI."""
        for action in self.actions:
            self.iface.removePluginVectorMenu(self.tr(u'&Create Network'), action)
            self.iface.removeToolBarIcon(action)

    def run(self):
        """Run method that performs all the real work"""
        if self.first_start:
            self.first_start = False
            self.dlg = CreateNetworkDialog(self.iface)

            # Set the logo in the QLabel with the name 'logoLabel'
            logo_label = self.dlg.findChild(QLabel, 'logoLabel')
            if logo_label:
                logo_pixmap = QPixmap(':/plugins/create_network/logo.png')
                logo_label.setPixmap(logo_pixmap)
            else:
                print("Logo QLabel 'logoLabel' not found!")

            # Set the logo in the QLabel with the name 'logoLabel_2'
            logo_label_2 = self.dlg.findChild(QLabel, 'logoLabel_2')
            if logo_label_2:
                logo_pixmap_2 = QPixmap(':/plugins/create_network/logo.png')
                logo_label_2.setPixmap(logo_pixmap_2)
            else:
                print("Logo QLabel 'logoLabel_2' not found!")

            # Set the logo in the QLabel with the name 'logoLabel_3'
            logo_label_3 = self.dlg.findChild(QLabel, 'logoLabel_3')
            if logo_label_3:
                logo_pixmap_3 = QPixmap(':/plugins/create_network/logo.png')
                logo_label_3.setPixmap(logo_pixmap_3)
            else:
                print("Logo QLabel 'logoLabel_3' not found!")

        self.dlg.show()
        result = self.dlg.exec_()
        if result:
            pass
