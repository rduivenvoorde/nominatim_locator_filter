# -*- coding: utf-8 -*-

from qgis.core import Qgis, QgsMessageLog, QgsLocatorFilter, QgsLocatorResult, QgsRectangle, \
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsNetworkAccessManager, QgsNetworkReplyContent, QgsGeometry, QgsCoordinateTransform, QgsPointXY, QgsWkbTypes

from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import pyqtSignal, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtGui import QPixmap, QIcon, QColor, QPainter

import json
import os
from pathlib import Path
from osgeo import ogr, osr


class NominatimFilterPlugin:

    def __init__(self, iface):

        self.iface = iface
        self.iface.rb = QgsRubberBand(self.iface.mapCanvas())
        self.filter = NominatimLocatorFilter(self.iface)

        # THIS is not working?? As in show_problem never called
        self.filter.resultProblem.connect(self.show_problem)
        self.iface.registerLocatorFilter(self.filter)

    def show_problem(self, err):
        self.filter.info("showing problem???")  # never come here?
        self.iface.messageBar().pushWarning("NominatimLocatorFilter Error", '{}'.format(err))

    def initGui(self):
        pass

    def unload(self):
        self.reset_Rubberband()
        self.iface.deregisterLocatorFilter(self.filter)
        # self.filter.resultProblem.disconnect(self.show_problem)
        
    def reset_Rubberband(self):
        self.iface.rb.reset()


# SEE: https://github.com/qgis/QGIS/blob/master/src/core/locator/qgslocatorfilter.h
#      for all attributes/members/functions to be implemented
class NominatimLocatorFilter(QgsLocatorFilter):

    USER_AGENT = 'Mozilla/5.0 QGIS NominatimLocatorFilter'

    # SEARCH_URL = 'https://nominatim.openstreetmap.org/search?format=json&q='
    # test url to be able to force errors
    # SEARCH_URL = 'http://duif.net/cgi-bin/qlocatorcheck.cgi?q='
    SEARCH_URL = 'https://nominatim.openstreetmap.org/search?polygon_geojson=1&format=json&polygon_threshold=0&limit=30&q='
    
    # some magic numbers to be able to zoom to more or less defined levels
    ADDRESS = 1000
    STREET = 1500
    ZIP = 3000
    PLACE = 30000
    CITY = 120000
    ISLAND = 250000
    COUNTRY = 4000000

    resultProblem = pyqtSignal(str)

    def __init__(self, iface):
        # you REALLY have to save the handle to iface, else segfaults!!
        self.iface = iface
        self.rb = iface.rb
        super(QgsLocatorFilter, self).__init__()

        self.icon_dir = Path(os.path.dirname(__file__)) / 'icons'
        # map icons are sourced from https://github.com/osm-search/nominatim-ui/blob/master/src/components/MapIcon.svelte
        # which says: equivalent to PHP Nominatim::ClassTypes::getIcon
        # covers 83 of 214 available icon filenames, e.g. transport_roundabout_anticlockwise
        # transport_rental_bicycle or place_of_worship_christian would need more data from the place
        # store icon mapping in MAP_ICONS dictionary
        with open(self.icon_dir / 'mapicons.json') as f:
            data = f.read()
        self.MAP_ICONS = json.loads(data)

        # onetime: we retrieve/cache all used icons in the plugin:
        # nam2 = QgsNetworkAccessManager.instance()
        # pixmap = QPixmap()
        # for key, icon_name in self.MAP_ICONS.items():
        #     self.info(f"{key} -> {icon_name}")
        #     png_name = f"{icon_name}.p.20.png"
        #     req = QNetworkRequest(QUrl(f'https://nominatim.openstreetmap.org/ui/mapicons/{png_name}'))
        #     reply = nam2.blockingGet(req)
        #     data = reply.content().data()
        #     pixmap.loadFromData(data)
        #     pixmap.save(str(self.icon_dir / png_name))

    def name(self):
        return self.__class__.__name__

    def clone(self):
        return NominatimLocatorFilter(self.iface)

    def displayName(self):
        return 'Nominatim Plugin (end with space to search, use &<txt> for local search)'

    def prefix(self):
        return 'osm'

    def fetchResults(self, search, context, feedback):
        self.rb.reset()
        if len(search) < 2:
            return

        # see https://operations.osmfoundation.org/policies/nominatim/
        # "Auto-complete search This is not yet supported by Nominatim and you must not implement such a service on the client side using the API."
        # so end with a space to trigger a search:
        if search[-1] != ' ':
            return

        if search[0] == '&':
            transform = QgsCoordinateTransform(QgsProject.instance().crs(),QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            ext = transform.transform(self.iface.mapCanvas().extent())
            search = search[1:] + '&bounded=1&viewbox=' + str(ext.xMinimum()) + ',' + str(ext.yMinimum()) + ',' + str(ext.xMaximum()) + ',' + str(ext.yMaximum())

        url = '{}{}'.format(self.SEARCH_URL, search)
        #self.info('Search url {}'.format(url))
        try:
            nam = QgsNetworkAccessManager.instance()
            request = QNetworkRequest(QUrl(url))
            # see https://operations.osmfoundation.org/policies/nominatim/
            # "Provide a valid HTTP Referer or User-Agent identifying the application (QGIS geocoder)"
            request.setHeader(QNetworkRequest.UserAgentHeader, self.USER_AGENT)
            reply: QgsNetworkReplyContent = nam.blockingGet(request)
            if reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) == 200:  # other codes are handled by NetworkAccessManager
                content_string = reply.content().data().decode('utf8')
                locations = json.loads(content_string)
                pixmap = QPixmap()
                nam2 = QgsNetworkAccessManager.instance()
                for loc in locations:
                    result = QgsLocatorResult()
                    result.filter = self
                    result.displayString = '{} ({})'.format(loc['display_name'], loc['type'])
                    # use the json full item as userData, so all info is in it:
                    result.userData = loc

                    icon = QIcon()
                    icon_class = loc.get('class')
                    icon_type = loc.get('type')
                    icon_key = f'{icon_class}:{icon_type}'
                    if icon_key in self.MAP_ICONS:
                        icon_path = self.icon_dir / (self.MAP_ICONS[icon_key] + '.p.20.png')
                        if icon_path.is_file():
                            pixmap = QPixmap(str(icon_path))
                    else:
                        # get geom type from geojson if no icon is provided, but type still is (note: lower() needed for case sensitive filesystems/linux)
                        icon_path = self.icon_dir / (loc['geojson']['type'] + '.png').lower()
                        pixmap = QPixmap(str(icon_path))

                    # change pixmap background color to white to support dark mode!
                    painter = QPainter(pixmap)
                    if painter.isActive():
                        painter.setCompositionMode(QPainter.CompositionMode_DestinationOver)
                        painter.fillRect(pixmap.rect(), QColor("white"))
                        painter.end()
                    icon.addPixmap(pixmap, QIcon.Normal, QIcon.Off)
                    result.icon = icon
                    self.resultFetched.emit(result)

        except Exception as err:
            # Handle exception, only this one seems to work
            self.info(err)

    def triggerResult(self, result):
        self.rb.reset()
        # Newer Version of PyQT does not expose the .userData (Leading to core dump)
        # Try via get Function, otherwise access attribute
        try:
            doc = result.getUserData()
        except:
            doc = result.userData

        geojson = doc.get('geojson')
        if geojson:
            feature = ogr.CreateGeometryFromJson(str(geojson))
            wkt = feature.ExportToWkt()
        else:
            wkt = 'POINT(%s %s)' % (doc.get('lon'), doc.get('lat'))

        target_srs = self.iface.mapCanvas().mapSettings().destinationCrs().authid()
        self.showRubberBand(wkt, QgsCoordinateReferenceSystem("EPSG:4326"), QgsCoordinateReferenceSystem(target_srs))

    def info(self, msg=""):
        QgsMessageLog.logMessage('{} {}'.format(self.__class__.__name__, msg), 'NominatimLocatorFilter', Qgis.Info)

    def showRubberBand(self, wkt, src_srs, dest_srs):
        self.rb.reset()
        xform = QgsCoordinateTransform(src_srs, dest_srs, QgsProject.instance())
        geom = QgsGeometry.fromWkt(wkt)
        geom.transform(xform)
        self.rb.setToGeometry(geom,None)
        self.rb.setColor(QColor(0, 0, 255, 60))
        self.rb.setWidth(3)
        
        box = geom.boundingBox()
        self.iface.mapCanvas().setExtent(box,False)
        # sometimes Nominatim has result with very tiny bounding boxes, let's set a minimum
        if ('POINT' in wkt.upper()) or self.iface.mapCanvas().scale() < 1000:
            self.iface.mapCanvas().zoomScale(1000)
        else:
            self.iface.mapCanvas().zoomOut()
                
        self.iface.mapCanvas().refresh()
