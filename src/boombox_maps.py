"""
Map handling module for SDR-Boombox
Handles traffic map tiles and weather radar overlays from HD Radio
"""

import re
import time
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets
from urllib.request import urlopen
from urllib.error import URLError


class MapHandler(QtCore.QObject):
    """Handles traffic and weather map data from HD Radio broadcasts"""
    
    # Signals
    mapReady = QtCore.Signal(QtGui.QPixmap)  # generic map signal (deprecated)
    trafficMapReady = QtCore.Signal(QtGui.QPixmap)  # traffic map signal
    weatherMapReady = QtCore.Signal(QtGui.QPixmap)  # weather map signal
    
    def __init__(self, parent=None):
        super().__init__(parent)
        from .boombox_utils import LOT_FILES_DIR
        self.lot_dir = LOT_FILES_DIR
        
        # Traffic map state
        self.traffic_tiles = {}  # (row, col) -> file_path
        self.last_traffic_timestamp = ""
        self.combined_traffic_map = None
        
        # Weather overlay state
        self.weather_overlay_file = ""
        self.weather_location = None  # (lat, lon) from DWRI file
        
        # Cache for base map
        self._base_map_cache = None
        
    def handle_traffic_tile(self, tile_file: str, log_callback=None):
        """Handle a traffic map tile and assemble when complete"""
        def try_load_tile(attempts=0):
            try:
                tile_path = self.lot_dir / tile_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not tile_path.exists():
                    matching_files = list(self.lot_dir.glob(f"*_{tile_file}"))
                    if matching_files:
                        tile_path = matching_files[0]
                        if log_callback:
                            log_callback(f"[map] Found traffic tile with prefix: {tile_path.name}")
                
                if tile_path.exists():
                    # Check if file is stable
                    size1 = tile_path.stat().st_size
                    time.sleep(0.1)
                    if tile_path.exists():
                        size2 = tile_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            QtCore.QTimer.singleShot(200, lambda: try_load_tile(attempts + 1))
                            return
                    
                    # Remove any prefix for parsing
                    clean_name = tile_file
                    if '_TMT_' in tile_file:
                        tmt_index = tile_file.index('TMT_')
                        clean_name = tile_file[tmt_index:]
                    
                    # Parse tile info: TMT_03g9rc_2_1_20251031_1614_002e.png
                    parts = clean_name.split('_')
                    if len(parts) >= 6:
                        row = int(parts[2])
                        col = int(parts[3])
                        timestamp = f"{parts[4]}_{parts[5]}"
                        
                        # Check if this is a new set of tiles
                        if timestamp != self.last_traffic_timestamp and self.last_traffic_timestamp:
                            self.cleanup_old_traffic_tiles(timestamp)
                            self.traffic_tiles.clear()
                        
                        self.last_traffic_timestamp = timestamp
                        
                        # Store this tile
                        self.traffic_tiles[(row, col)] = str(tile_path)
                        if log_callback:
                            log_callback(f"[map] Traffic tile received: Row {row}, Col {col}")
                        
                        # Check if we have all 9 tiles (3x3 grid)
                        if len(self.traffic_tiles) == 9:
                            self.assemble_traffic_map(log_callback)
                    
                elif attempts < 5:
                    QtCore.QTimer.singleShot(500, lambda: try_load_tile(attempts + 1))
                else:
                    if log_callback:
                        log_callback(f"[map] Traffic tile never appeared: {tile_file}")
            except Exception as e:
                if log_callback:
                    log_callback(f"[map] Error handling traffic tile: {e}")
        
        try_load_tile()
    
    def assemble_traffic_map(self, log_callback=None):
        """Assemble the 3x3 traffic map tiles into one image"""
        try:
            # Load all tiles
            tiles = {}
            tile_size = None
            
            for (row, col), path in self.traffic_tiles.items():
                pm = QtGui.QPixmap(path)
                if not pm.isNull():
                    tiles[(row, col)] = pm
                    if tile_size is None:
                        tile_size = (pm.width(), pm.height())
            
            if len(tiles) == 9 and tile_size:
                # Create combined image (3x3 grid)
                combined_width = tile_size[0] * 3
                combined_height = tile_size[1] * 3
                combined = QtGui.QPixmap(combined_width, combined_height)
                combined.fill(QtCore.Qt.black)
                
                painter = QtGui.QPainter(combined)
                
                # Draw each tile in its position
                for row in range(1, 4):
                    for col in range(1, 4):
                        if (row, col) in tiles:
                            x = (col - 1) * tile_size[0]
                            y = (row - 1) * tile_size[1]
                            painter.drawPixmap(x, y, tiles[(row, col)])
                
                painter.end()
                
                # Store the combined traffic map
                self.combined_traffic_map = combined
                
                # Emit as traffic map (type = "traffic")
                self.trafficMapReady.emit(combined)
                
                if log_callback:
                    log_callback(f"[map] Traffic map assembled from 9 tiles")
                
        except Exception as e:
            if log_callback:
                log_callback(f"[map] Error assembling traffic map: {e}")
    
    def handle_weather_overlay(self, overlay_file: str, log_callback=None):
        """Handle weather radar overlay"""
        def try_load_overlay(attempts=0):
            try:
                overlay_path = self.lot_dir / overlay_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not overlay_path.exists():
                    matching_files = list(self.lot_dir.glob(f"*_{overlay_file}"))
                    if matching_files:
                        overlay_path = matching_files[0]
                        if log_callback:
                            log_callback(f"[weather] Found weather radar with prefix: {overlay_path.name}")
                
                if overlay_path.exists():
                    # Check if file is stable
                    size1 = overlay_path.stat().st_size
                    time.sleep(0.1)
                    if overlay_path.exists():
                        size2 = overlay_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            QtCore.QTimer.singleShot(200, lambda: try_load_overlay(attempts + 1))
                            return
                    
                    # Store the weather radar file
                    self.weather_overlay_file = str(overlay_path)
                    if log_callback:
                        log_callback(f"[weather] Weather radar map received: {overlay_file}")
                    
                    # Create composite map
                    self.create_composite_weather_map(log_callback)
                    
                elif attempts < 5:
                    QtCore.QTimer.singleShot(500, lambda: try_load_overlay(attempts + 1))
                else:
                    if log_callback:
                        log_callback(f"[weather] Weather radar never appeared: {overlay_file}")
            except Exception as e:
                if log_callback:
                    log_callback(f"[weather] Error handling weather radar: {e}")
        
        try_load_overlay()
    
    def create_composite_weather_map(self, log_callback=None):
        """Create composite map with geographical base and weather overlay"""
        try:
            # Create a proper geographical base map
            composite_map = self._create_base_map()
            
            if log_callback:
                log_callback(f"[weather] Created geographical base map")
            
            # Now overlay weather if available
            if self.weather_overlay_file:
                weather_pm = QtGui.QPixmap(self.weather_overlay_file)
                if not weather_pm.isNull():
                    if log_callback:
                        log_callback(f"[weather] Weather overlay size: {weather_pm.width()}x{weather_pm.height()} pixels")
                    
                    # Create painter to composite the images
                    painter = QtGui.QPainter(composite_map)
                    painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform)
                    
                    # Scale weather overlay to match base map size
                    scaled_weather = weather_pm.scaled(
                        composite_map.size(),
                        QtCore.Qt.KeepAspectRatio,
                        QtCore.Qt.SmoothTransformation
                    )
                    
                    # Draw weather with transparency so base map shows through
                    painter.setOpacity(0.7)  # 70% opacity for weather overlay
                    
                    # Center the weather overlay on the base map
                    x = (composite_map.width() - scaled_weather.width()) // 2
                    y = (composite_map.height() - scaled_weather.height()) // 2
                    painter.drawPixmap(x, y, scaled_weather)
                    
                    painter.end()
                    
                    if log_callback:
                        log_callback(f"[weather] Composite map created with weather overlay")
            
            # Emit the composite weather map
            self.weatherMapReady.emit(composite_map)
            
        except Exception as e:
            if log_callback:
                log_callback(f"[weather] Error creating composite map: {e}")
    
    def cleanup_old_traffic_tiles(self, new_timestamp: str):
        """Delete old traffic tiles when new ones arrive"""
        try:
            if not self.lot_dir.exists():
                return
            
            # Find all TMT files that don't match the new timestamp
            for file in self.lot_dir.glob("*TMT_*.png"):
                if new_timestamp not in file.name:
                    try:
                        file.unlink()
                    except:
                        pass
        except Exception:
            pass
    
    def load_existing_maps(self, log_callback=None):
        """Load existing map data from disk"""
        try:
            if not self.lot_dir.exists():
                return
            
            # Load existing traffic tiles
            tiles = list(self.lot_dir.glob("*TMT_*.png"))
            if tiles:
                # Group by timestamp
                tile_groups = {}
                for tile in tiles:
                    clean_name = tile.name
                    if '_TMT_' in tile.name:
                        tmt_index = tile.name.index('TMT_')
                        clean_name = tile.name[tmt_index:]
                    
                    parts = clean_name.split('_')
                    if len(parts) >= 6:
                        timestamp = f"{parts[4]}_{parts[5]}"
                        if timestamp not in tile_groups:
                            tile_groups[timestamp] = []
                        tile_groups[timestamp].append(tile)
                
                # Find the most recent complete set
                for timestamp in sorted(tile_groups.keys(), reverse=True):
                    if len(tile_groups[timestamp]) == 9:
                        # Load this set
                        self.traffic_tiles.clear()
                        self.last_traffic_timestamp = timestamp
                        
                        for tile in tile_groups[timestamp]:
                            clean_name = tile.name
                            if '_TMT_' in tile.name:
                                tmt_index = tile.name.index('TMT_')
                                clean_name = tile.name[tmt_index:]
                            
                            parts = clean_name.split('_')
                            row = int(parts[2])
                            col = int(parts[3])
                            self.traffic_tiles[(row, col)] = str(tile)
                        
                        self.assemble_traffic_map(log_callback)
                        if log_callback:
                            log_callback(f"[map] Loaded existing traffic map from {timestamp}")
                        break
            
            # Load most recent weather overlay
            weather_files = list(self.lot_dir.glob("*DWRO_*.png"))
            if weather_files:
                weather_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                self.weather_overlay_file = str(weather_files[0])
                if log_callback:
                    log_callback(f"[map] Loaded existing weather overlay: {weather_files[0].name}")
                self.create_composite_weather_map(log_callback)
                
        except Exception as e:
            if log_callback:
                log_callback(f"[map] Error loading existing map data: {e}")
    
    def clear_all_maps(self):
        """Clear all map data"""
        self.traffic_tiles.clear()
        self.last_traffic_timestamp = ""
        self.weather_overlay_file = ""
        self.combined_traffic_map = None
        self._base_map_cache = None
    
    def _create_base_map(self):
        """Create a geographical base map for weather overlay"""
        # Check if we have a cached base map
        if self._base_map_cache and not self._base_map_cache.isNull():
            return QtGui.QPixmap(self._base_map_cache)
        
        # Try to fetch a real map from OpenStreetMap
        try:
            # Use location from DWRI file if available, otherwise use default
            if self.weather_location:
                lat, lon = self.weather_location
            else:
                # Default to US center if no location data available
                lat, lon = 39.8283, -98.5795
            
            zoom = 8  # Regional view (~200 mile radius)
            
            # For a better map, we'll create a composite of multiple tiles
            base_map = self._fetch_osm_tiles(lat, lon, zoom, 600, 600)
            
            if base_map and not base_map.isNull():
                self._base_map_cache = QtGui.QPixmap(base_map)
                return base_map
        except Exception:
            pass
        
        # Fallback to a simple generated map
        base_map = QtGui.QPixmap(600, 600)
        base_map.fill(QtGui.QColor(20, 30, 40))  # Dark blue background (water)
        
        painter = QtGui.QPainter(base_map)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        
        # Draw a simple US map outline
        painter.setPen(QtGui.QPen(QtGui.QColor(80, 120, 80), 2))  # Green for land
        painter.setBrush(QtGui.QBrush(QtGui.QColor(60, 90, 60)))  # Darker green fill
        
        # Simplified US outline (very basic)
        us_outline = [
            QtCore.QPoint(100, 250), QtCore.QPoint(150, 200), QtCore.QPoint(250, 180),
            QtCore.QPoint(400, 170), QtCore.QPoint(500, 200), QtCore.QPoint(520, 250),
            QtCore.QPoint(500, 350), QtCore.QPoint(450, 400), QtCore.QPoint(350, 420),
            QtCore.QPoint(200, 400), QtCore.QPoint(100, 350), QtCore.QPoint(100, 250)
        ]
        painter.drawPolygon(us_outline)
        
        # Add state boundaries (simplified grid)
        painter.setPen(QtGui.QPen(QtGui.QColor(50, 70, 50), 1, QtCore.Qt.DotLine))
        for i in range(150, 500, 50):
            painter.drawLine(i, 180, i, 420)
        for i in range(200, 400, 40):
            painter.drawLine(100, i, 520, i)
        
        # Add text
        painter.setPen(QtGui.QColor(150, 150, 150))
        painter.setFont(QtGui.QFont("Arial", 10))
        painter.drawText(10, 590, "Base map for weather overlay")
        
        painter.end()
        
        self._base_map_cache = base_map
        return QtGui.QPixmap(base_map)
    
    def _fetch_osm_tiles(self, lat: float, lon: float, zoom: int, width: int, height: int):
        """Fetch and compose OpenStreetMap tiles for the given area"""
        try:
            import math
            from urllib.request import Request
            
            # Convert lat/lon to tile numbers
            def lat_lon_to_tile(lat, lon, zoom):
                lat_rad = math.radians(lat)
                n = 2.0 ** zoom
                x = int((lon + 180.0) / 360.0 * n)
                y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
                return x, y
            
            # Calculate center tile
            center_x, center_y = lat_lon_to_tile(lat, lon, zoom)
            
            # Create composite map (3x3 tiles for better coverage)
            tile_size = 256
            composite = QtGui.QPixmap(tile_size * 3, tile_size * 3)
            composite.fill(QtGui.QColor(20, 30, 40))
            
            painter = QtGui.QPainter(composite)
            
            # Fetch surrounding tiles
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    tile_x = center_x + dx
                    tile_y = center_y + dy
                    
                    try:
                        # Fetch tile from OSM
                        url = f"https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png"
                        req = Request(url, headers={
                            'User-Agent': 'SDR-Boombox/2.0 (https://github.com/sjhilt/SDR-Boombox)'
                        })
                        
                        with urlopen(req, timeout=2) as response:
                            data = response.read()
                        
                        # Load tile
                        tile_pm = QtGui.QPixmap()
                        tile_pm.loadFromData(data)
                        
                        if not tile_pm.isNull():
                            # Draw tile at correct position
                            x = (dx + 1) * tile_size
                            y = (dy + 1) * tile_size
                            painter.drawPixmap(x, y, tile_pm)
                    except:
                        # Skip failed tiles
                        pass
            
            painter.end()
            
            # Scale to desired size
            if composite and not composite.isNull():
                return composite.scaled(width, height, QtCore.Qt.KeepAspectRatio,
                                       QtCore.Qt.SmoothTransformation)
        except Exception:
            pass
        
        return None
    
    def parse_log_line(self, line: str):
        """Parse log lines for map data"""
        # Check for traffic tiles
        if "TMT_" in line and ".png" in line:
            match = re.search(r"name=(TMT_[^\s]+\.png)", line)
            if match:
                tile_file = match.group(1)
                self.handle_traffic_tile(tile_file)
        
        # Check for weather info file (contains location data)
        elif "DWRI_" in line:
            match = re.search(r"name=(DWRI_[^\s]+)", line)
            if match:
                info_file = match.group(1)
                self.handle_weather_info(info_file)
        
        # Check for weather overlay
        elif "DWRO_" in line and ".png" in line:
            match = re.search(r"name=(DWRO_[^\s]+\.png)", line)
            if match:
                overlay_file = match.group(1)
                self.handle_weather_overlay(overlay_file)
    
    def handle_weather_info(self, info_file: str, log_callback=None):
        """Handle weather info file (DWRI) that contains location data"""
        def try_load_info(attempts=0):
            try:
                info_path = self.lot_dir / info_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not info_path.exists():
                    matching_files = list(self.lot_dir.glob(f"*_{info_file}"))
                    if matching_files:
                        info_path = matching_files[0]
                        if log_callback:
                            log_callback(f"[weather] Found weather info with prefix: {info_path.name}")
                
                if info_path.exists():
                    # Try to parse the DWRI file for location information
                    try:
                        # Try reading as text first
                        with open(info_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        # Look for DWR_Area_ID pattern
                        area_match = re.search(r'DWR_Area_ID[=:\s]*"?([a-zA-Z0-9]+)"?', content)
                        if area_match:
                            area_id = area_match.group(1)
                            if log_callback:
                                log_callback(f"[weather] Found DWR_Area_ID: {area_id}")
                            
                            # Try to decode the area ID to location
                            location = self._decode_area_id(area_id)
                            if location:
                                self.weather_location = location
                                self._base_map_cache = None
                                if log_callback:
                                    log_callback(f"[weather] Location decoded from area ID: {location[0]:.4f}, {location[1]:.4f}")
                        
                        # Also look for explicit lat/lon patterns
                        lat_match = re.search(r'lat[itude]*[:\s]+(-?\d+\.?\d*)', content, re.IGNORECASE)
                        lon_match = re.search(r'lon[gitude]*[:\s]+(-?\d+\.?\d*)', content, re.IGNORECASE)
                        
                        if lat_match and lon_match:
                            lat = float(lat_match.group(1))
                            lon = float(lon_match.group(1))
                            self.weather_location = (lat, lon)
                            self._base_map_cache = None
                            if log_callback:
                                log_callback(f"[weather] Location extracted from DWRI: {lat:.4f}, {lon:.4f}")
                        
                        # If we still don't have location, check if area_id matches traffic tile pattern
                        if not self.weather_location and area_match:
                            # The area ID might match the traffic tiles (e.g., "03g9rc")
                            # Traffic tiles often encode regional information
                            # Check if we have matching traffic tiles
                            for (row, col), path in self.traffic_tiles.items():
                                if area_id in path:
                                    # Found matching traffic tiles - use their implied region
                                    # This is a heuristic - traffic and weather often cover same region
                                    if log_callback:
                                        log_callback(f"[weather] Area ID {area_id} matches traffic tiles - using regional default")
                                    # Use a reasonable default for the region (will be overridden if better data found)
                                    break
                    except Exception as e:
                        if log_callback:
                            log_callback(f"[weather] Error parsing DWRI file: {e}")
                            
                elif attempts < 3:
                    QtCore.QTimer.singleShot(500, lambda: try_load_info(attempts + 1))
                else:
                    if log_callback:
                        log_callback(f"[weather] Weather info file never appeared: {info_file}")
            except Exception as e:
                if log_callback:
                    log_callback(f"[weather] Error handling weather info: {e}")
        
        try_load_info()
    
    def _decode_area_id(self, area_id: str):
        """Attempt to decode an area ID to lat/lon coordinates"""
        # Common area IDs and their approximate locations
        # This is based on observed patterns in HD Radio broadcasts
        area_locations = {
            # Tennessee/Georgia region
            "03g9rc": (35.0456, -85.3097),  # Chattanooga area
            "03g9rb": (35.1495, -85.2327),  # Cleveland, TN area
            "03g9ra": (34.9873, -85.2552),  # North Georgia
            
            # Add more area codes as discovered
            # Format appears to be regional grid references
        }
        
        # Check if we have a known area code
        if area_id in area_locations:
            return area_locations[area_id]
        
        # Try to parse as a geohash-like encoding
        # The format seems to be: [region][grid][cell]
        # e.g., "03g9rc" might be region 03, grid g9, cell rc
        if len(area_id) >= 6:
            try:
                # Extract components
                region = area_id[:2]  # First 2 chars
                grid = area_id[2:4]    # Next 2 chars
                cell = area_id[4:6]    # Next 2 chars
                
                # Rough approximation based on pattern
                # This is a heuristic - actual encoding may differ
                if region == "03":  # Southeast US
                    # Base coordinates for region 03
                    base_lat, base_lon = 35.0, -85.0
                    
                    # Adjust based on grid (very rough approximation)
                    if 'g' in grid:
                        grid_offset = (ord(grid[0]) - ord('a')) * 0.5
                        base_lat += grid_offset * 0.1
                    if grid[1].isdigit():
                        base_lon -= int(grid[1]) * 0.1
                    
                    return (base_lat, base_lon)
            except:
                pass
        
        return None
    
    def reset(self):
        """Reset map handler state"""
        self.traffic_tiles.clear()
        self.last_traffic_timestamp = ""
        self.weather_overlay_file = ""
        self.weather_location = None
        self.combined_traffic_map = None
        self._base_map_cache = None


class MapWindow(QtWidgets.QWidget):
    """Separate window for displaying traffic and weather maps"""
    
    def __init__(self, map_handler=None, parent=None):
        super().__init__(parent)
        self.map_handler = map_handler
        self.setWindowTitle("Traffic & Weather Maps")
        self.setMinimumSize(900, 600)
        self.resize(1200, 700)
        
        # Create main layout
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        # Create tab widget for different map types
        self.tabs = QtWidgets.QTabWidget()
        
        # Traffic Map Tab
        traffic_tab = QtWidgets.QWidget()
        traffic_layout = QtWidgets.QVBoxLayout(traffic_tab)
        
        traffic_info = QtWidgets.QLabel(
            "Road map with traffic conditions - assembled from 3x3 grid of map tiles broadcast via HD Radio"
        )
        traffic_info.setStyleSheet("color: #aaa; padding: 5px;")
        traffic_layout.addWidget(traffic_info)
        
        self.traffic_map_widget = QtWidgets.QLabel()
        self.traffic_map_widget.setAlignment(QtCore.Qt.AlignCenter)
        self.traffic_map_widget.setStyleSheet(
            "background:#0f0f0f; border:2px solid #333; border-radius:8px;"
        )
        self.traffic_map_widget.setScaledContents(False)
        traffic_layout.addWidget(self.traffic_map_widget)
        
        # Weather Radar Tab
        weather_tab = QtWidgets.QWidget()
        weather_layout = QtWidgets.QVBoxLayout(weather_tab)
        
        weather_info = QtWidgets.QLabel(
            "Weather radar overlaid on base map - broadcast via HD Radio data services"
        )
        weather_info.setStyleSheet("color: #aaa; padding: 5px;")
        weather_layout.addWidget(weather_info)
        
        self.weather_map_widget = QtWidgets.QLabel()
        self.weather_map_widget.setAlignment(QtCore.Qt.AlignCenter)
        self.weather_map_widget.setStyleSheet(
            "background:#0f0f0f; border:2px solid #333; border-radius:8px;"
        )
        self.weather_map_widget.setScaledContents(False)
        weather_layout.addWidget(self.weather_map_widget)
        
        # Add tabs
        self.tabs.addTab(traffic_tab, "Traffic/Roads Map")
        self.tabs.addTab(weather_tab, "Weather + Map Overlay")
        
        main_layout.addWidget(self.tabs)
        
        # Add refresh button
        button_layout = QtWidgets.QHBoxLayout()
        self.refresh_btn = QtWidgets.QPushButton("Refresh Maps")
        button_layout.addStretch()
        button_layout.addWidget(self.refresh_btn)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        
        # Initialize with placeholder text
        self.traffic_map_widget.setText(
            "No traffic data available yet\n\nTraffic maps will appear here when broadcast"
        )
        self.weather_map_widget.setText(
            "No weather radar data available\n\n"
            "Weather maps come from HD Radio data services.\n"
            "To receive weather radar:\n\n"
            "• Tune to an HD Radio station that broadcasts weather data\n"
            "• News/talk stations are more likely to provide weather\n"
            "• Look for 'DWRO' files in the log when data is received\n"
            "• Weather data updates periodically (typically every 5-15 minutes)\n\n"
            "Note: Not all HD Radio stations broadcast weather radar."
        )
    
    def update_traffic_map(self, pixmap: QtGui.QPixmap):
        """Update the traffic map display"""
        if pixmap and not pixmap.isNull():
            pm_scaled = pixmap.scaled(600, 600, QtCore.Qt.KeepAspectRatio, 
                                     QtCore.Qt.SmoothTransformation)
            self.traffic_map_widget.setPixmap(pm_scaled)
            self.traffic_map_widget.setText("")
    
    def update_weather_map(self, pixmap: QtGui.QPixmap):
        """Update the weather map display"""
        if pixmap and not pixmap.isNull():
            pm_scaled = pixmap.scaled(600, 600, QtCore.Qt.KeepAspectRatio,
                                     QtCore.Qt.SmoothTransformation)
            self.weather_map_widget.setPixmap(pm_scaled)
            self.weather_map_widget.setText("")
            # Switch to weather tab when new data arrives
            self.tabs.setCurrentIndex(1)
    
    def update_map(self, pixmap: QtGui.QPixmap):
        """Legacy method - updates traffic map only"""
        self.update_traffic_map(pixmap)
