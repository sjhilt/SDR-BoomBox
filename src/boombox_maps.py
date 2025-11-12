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
    mapReady = QtCore.Signal(QtGui.QPixmap)  # pixmap for map display
    
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
                
                # Store and emit the combined traffic map
                self.combined_traffic_map = combined
                self.mapReady.emit(combined)
                
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
            self.mapReady.emit(composite_map)
            
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
        
        # If we have traffic tiles, use them as the base map
        if self.combined_traffic_map and not self.combined_traffic_map.isNull():
            self._base_map_cache = QtGui.QPixmap(self.combined_traffic_map)
            return QtGui.QPixmap(self._base_map_cache)
        
        # Otherwise create a simple placeholder map
        base_map = QtGui.QPixmap(600, 600)
        base_map.fill(QtGui.QColor(20, 20, 30))  # Dark background
        
        painter = QtGui.QPainter(base_map)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        
        # Draw a simple grid
        painter.setPen(QtGui.QPen(QtGui.QColor(40, 40, 50), 1, QtCore.Qt.DotLine))
        for i in range(0, 601, 60):
            painter.drawLine(i, 0, i, 600)
            painter.drawLine(0, i, 600, i)
        
        # Add text indicating this is a placeholder
        painter.setPen(QtGui.QColor(100, 100, 100))
        painter.setFont(QtGui.QFont("Arial", 14))
        painter.drawText(base_map.rect(), QtCore.Qt.AlignCenter, 
                        "Waiting for map data from HD Radio broadcast")
        
        painter.end()
        
        # Don't cache the placeholder
        return base_map
    
    def parse_log_line(self, line: str):
        """Parse log lines for map data"""
        # Check for traffic tiles
        if "TMT_" in line and ".png" in line:
            match = re.search(r"name=(TMT_[^\s]+\.png)", line)
            if match:
                tile_file = match.group(1)
                self.handle_traffic_tile(tile_file)
        
        # Check for weather overlay
        elif "DWRO_" in line and ".png" in line:
            match = re.search(r"name=(DWRO_[^\s]+\.png)", line)
            if match:
                overlay_file = match.group(1)
                self.handle_weather_overlay(overlay_file)
    
    def reset(self):
        """Reset map handler state"""
        self.traffic_tiles.clear()
        self.last_traffic_timestamp = ""
        self.weather_overlay_file = ""
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
        """Update the appropriate map display based on current content"""
        if pixmap and not pixmap.isNull():
            # Update both tabs with the same map for now
            # In the future, we could differentiate between traffic and weather
            pm_scaled = pixmap.scaled(600, 600, QtCore.Qt.KeepAspectRatio,
                                     QtCore.Qt.SmoothTransformation)
            self.traffic_map_widget.setPixmap(pm_scaled)
            self.traffic_map_widget.setText("")
            self.weather_map_widget.setPixmap(pm_scaled)
            self.weather_map_widget.setText("")
