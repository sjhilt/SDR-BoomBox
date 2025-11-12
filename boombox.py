"""
===============================================================
   SDR-Boombox
   HD Radio (NRSC-5) + Analog FM Receiver & Visual Interface
===============================================================

Author:     @sjhilt
Project:    SDR-Boombox (Software Defined Radio Tuner)
License:    MIT License
Website:    https://github.com/sjhilt/SDR-Boombox
Version:    2.0.0
Python:     3.10+

Description:
------------
SDR-Boombox is a modern GUI-driven radio tuner for Software Defined Radios
such as the RTL-SDR. It attempts HD Radio decoding first using `nrsc5`, and
automatically falls back to analog wideband FM when digital signals are not
available. The interface features live metadata, album art, scanning, presets,
and a small system tray icon.

Version 2.0: Modularized architecture with separate components for visualization,
worker threads, map handling, metadata processing, and utilities.
"""

import sys
import json
import signal
from pathlib import Path
from PySide6 import QtCore, QtGui, QtWidgets

# Import modular components from src folder
from src.boombox_utils import (
    APP_NAME, FALLBACK_TIMEOUT_S, PRESETS_PATH, SETTINGS_PATH, 
    LOT_FILES_DIR, MAX_LOG_LINES, which, emoji_pixmap, SleepPreventer
)
from src.boombox_worker import Worker, Cfg
from src.boombox_visualizer import VisualizerWidget
from src.boombox_metadata import MetadataHandler
from src.boombox_maps import MapHandler, MapWindow

# Import stats module if available
try:
    from boombox_stats import StatsDatabase
    STATS_ENABLED = True
except ImportError:
    STATS_ENABLED = False


class SDRBoombox(QtWidgets.QMainWindow):
    """Main application window for SDR-Boombox"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDR-Boombox – HD Radio (NRSC-5)")
        self.setMinimumSize(1020, 350)
        
        # Initialize components
        self._setup_ui()
        self._setup_components()
        self._load_settings()
        self._check_dependencies()
        
    def _setup_ui(self):
        """Set up the user interface"""
        self.setStyleSheet("""
            QMainWindow { background: #151515; }
            QLabel#lcd {
                font-family: 'DS-Digital', monospace;
                color: #7CFC00; background:#0a0a0a;
                padding: 12px 18px; border-radius: 12px; letter-spacing: 1px;
            }
            QFrame#root { border: 3px solid #333; border-radius: 20px; background:#171717; }
            QPushButton { background:#2a2a2a; color:#eee; border:1px solid #444; border-radius: 10px; padding:8px 12px; }
            QPushButton:hover { background:#333; } QPushButton:pressed { background:#222; }
            QTextEdit { background:#0f0f0f; color:#ccc; border:1px solid #333; }
            QLabel#art { background:#0c0c0c; border:1px solid #2c2c2c; border-radius:12px; }
            QFrame#metaCard { background: rgba(0,0,0,0.55); border: 1px solid #202020; border-radius: 12px; }
            QLabel#metaTitle { color: #f2f2f2; font-size: 16px; font-weight: 600; }
            QLabel#metaSubtitle { color: #b9b9b9; font-size: 13px; font-weight: 400; }
            QComboBox { background:#222; color:#eee; border:1px solid #444; border-radius:8px; padding:4px 8px;}
            QCheckBox { color: #ffffff; }
        """)
        
        # Root widget
        root = QtWidgets.QFrame(objectName="root")
        self.setCentralWidget(root)
        grid = QtWidgets.QGridLayout(root)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(14)
        
        # Load presets early to check for P0
        self.presets = {}
        if PRESETS_PATH.exists():
            try:
                self.presets = json.loads(PRESETS_PATH.read_text())
            except Exception:
                pass
        
        # Use P0 if it exists, otherwise default to 88.0
        default_freq = self.presets.get("P0", 88.0)
        
        # Configuration
        self.cfg = Cfg(mhz=default_freq, gain=40.0, ppm=5)
        
        # Create display area
        self._create_display_area(grid)
        
        # Create controls area
        self._create_controls_area(grid)
        
        # Create art/metadata area
        self._create_art_area(grid)
        
        # Create system tray
        self._create_tray_icon()
        
    def _create_display_area(self, grid):
        """Create the frequency and station display area"""
        display_widget = QtWidgets.QWidget()
        display_widget.setStyleSheet("""
            QWidget { 
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #0f0f0f, stop:1 #0a0a0a);
                border: 2px solid #2a2a2a;
                border-radius: 15px;
            }
        """)
        
        display_stack = QtWidgets.QStackedLayout(display_widget)
        display_stack.setContentsMargins(15, 10, 15, 10)
        
        main_display = QtWidgets.QWidget()
        display_layout = QtWidgets.QVBoxLayout(main_display)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.setSpacing(5)
        
        # LCD container with overlay for bitrate
        lcd_container = QtWidgets.QWidget()
        lcd_container_layout = QtWidgets.QGridLayout(lcd_container)
        lcd_container_layout.setContentsMargins(0, 0, 0, 0)
        
        # Frequency display
        self.lcd = QtWidgets.QLabel("—.— MHz", objectName="lcd")
        lcd_font = QtGui.QFont('DS-Digital', 32)
        lcd_font.setWeight(QtGui.QFont.Bold)
        self.lcd.setFont(lcd_font)
        self.lcd.setAlignment(QtCore.Qt.AlignCenter)
        self.lcd.setStyleSheet("""
            QLabel {
                font-family: 'DS-Digital', monospace;
                color: #7CFC00;
                background: transparent;
                padding: 5px;
                letter-spacing: 3px;
            }
        """)
        lcd_container_layout.addWidget(self.lcd, 0, 0, 1, 1)
        
        # Bitrate display
        self.bitrate_display = QtWidgets.QLabel("0.0 kbps", objectName="bitrate_display")
        bitrate_font = QtGui.QFont('DS-Digital', 12)
        self.bitrate_display.setFont(bitrate_font)
        self.bitrate_display.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        self.bitrate_display.setStyleSheet("""
            QLabel {
                font-family: 'DS-Digital', monospace;
                color: #5CCC00;
                background: transparent;
                padding: 2px 8px;
            }
        """)
        lcd_container_layout.addWidget(self.bitrate_display, 0, 0, 
                                      QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        
        display_layout.addWidget(lcd_container)
        
        # Station info display with cycling
        self.station_display = QtWidgets.QLabel("Tuning...", objectName="station_display")
        station_font = self.station_display.font()
        station_font.setPointSize(16)
        self.station_display.setFont(station_font)
        self.station_display.setAlignment(QtCore.Qt.AlignCenter)
        self.station_display.setStyleSheet("""
            QLabel {
                color: #b9b9b9;
                background: transparent;
                padding: 2px;
                font-weight: 500;
            }
        """)
        display_layout.addWidget(self.station_display)
        
        display_stack.addWidget(main_display)
        grid.addWidget(display_widget, 0, 0, 1, 2)
        
        # Station info cycling timer
        self.station_info_timer = QtCore.QTimer()
        self.station_info_timer.timeout.connect(self._cycle_station_info)
        self.station_info_timer.setInterval(4000)  # Cycle every 4 seconds
        self.station_info_items = []
        self.station_info_index = 0
        
    def _create_controls_area(self, grid):
        """Create the control buttons and sliders area"""
        left = QtWidgets.QVBoxLayout()
        
        # Frequency slider
        self.freq_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.freq_slider.setRange(880, 1080)
        self.freq_slider.setValue(int(round(self.cfg.mhz * 10)))
        self.freq_slider.valueChanged.connect(self._update_lcd)
        left.addWidget(self.freq_slider)
        
        # Presets P0..P3
        pres_row = QtWidgets.QHBoxLayout()
        self.preset_buttons = []
        for i in range(4):
            b = QtWidgets.QPushButton(f"P{i}")
            b.setCheckable(False)
            b.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, idx=i: self._preset_menu(idx, pos))
            b.clicked.connect(lambda _=False, idx=i: self._preset_load(idx))
            self.preset_buttons.append(b)
            pres_row.addWidget(b)
        left.addLayout(pres_row)
        
        # Control buttons row
        row2 = QtWidgets.QHBoxLayout()
        self.btn_play = QtWidgets.QPushButton("Play")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        
        # Mute button
        self.btn_mute = QtWidgets.QPushButton("🔊")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setMaximumWidth(40)
        self.btn_mute.setToolTip("Mute/Unmute audio (receiver keeps running)")
        
        # Auto-fallback checkbox
        self.chk_fallback = QtWidgets.QCheckBox("Auto analog fallback")
        self.chk_fallback.setChecked(True)
        
        row2.addWidget(self.btn_play)
        row2.addWidget(self.btn_stop)
        row2.addWidget(self.btn_mute)
        row2.addWidget(self.chk_fallback)
        left.addLayout(row2)
        
        # HD program selector and log toggle
        hd_row = QtWidgets.QHBoxLayout()
        hd_label = QtWidgets.QLabel("HD Channel:")
        hd_label.setStyleSheet("color: #eee;")
        self.hd_selector = QtWidgets.QComboBox()
        self.hd_selector.addItems(["HD1", "HD2", "HD3", "HD4"])
        self.hd_selector.setCurrentIndex(0)
        self.hd_selector.currentIndexChanged.connect(self._on_hd_program_changed)
        
        # Log toggle button
        self.btn_toggle_log = QtWidgets.QPushButton("Hide Log")
        self.btn_toggle_log.setCheckable(True)
        self.btn_toggle_log.setMinimumWidth(90)
        self.btn_toggle_log.clicked.connect(self._toggle_log_view)
        
        # Map button
        self.btn_open_map = QtWidgets.QPushButton("Map")
        self.btn_open_map.setToolTip("Open traffic & weather map in new window")
        self.btn_open_map.clicked.connect(self._open_map_window)
        
        hd_row.addWidget(hd_label)
        hd_row.addWidget(self.hd_selector)
        hd_row.addStretch()
        hd_row.addWidget(self.btn_open_map)
        hd_row.addWidget(self.btn_toggle_log)
        left.addLayout(hd_row)
        
        # Create tab widget for log
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setFixedHeight(230)
        
        # Log tab
        self.log = QtWidgets.QTextEdit(readOnly=True)
        self.log_line_count = 0
        self.tabs.addTab(self.log, "Log")
        
        left.addWidget(self.tabs, 1)
        grid.addLayout(left, 1, 0)
        
        # Connect button signals
        self.btn_play.clicked.connect(self._play_clicked)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.btn_mute.clicked.connect(self._toggle_mute)
        
    def _create_art_area(self, grid):
        """Create the album art and metadata display area"""
        right = QtWidgets.QVBoxLayout()
        
        # Container for art/visualizer with logo watermark
        art_container = QtWidgets.QWidget()
        art_container.setFixedSize(260, 260)
        art_container_layout = QtWidgets.QGridLayout(art_container)
        art_container_layout.setContentsMargins(0, 0, 0, 0)
        
        # Stacked widget to switch between album art and visualizer
        self.art_stack = QtWidgets.QStackedWidget()
        self.art_stack.setFixedSize(260, 260)
        
        # Album art label
        self.art = QtWidgets.QLabel(objectName="art")
        self.art.setFixedSize(260, 260)
        self.art.setAlignment(QtCore.Qt.AlignCenter)
        
        # Default radio icon
        default_pm = QtGui.QPixmap(260, 260)
        default_pm.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(default_pm)
        painter.setPen(QtGui.QColor(150, 150, 150))
        painter.setFont(QtGui.QFont("Arial", 48))
        painter.drawText(default_pm.rect(), QtCore.Qt.AlignCenter, "RADIO")
        painter.end()
        self.art.setPixmap(default_pm)
        
        # Visualizer widget
        self.visualizer = VisualizerWidget()
        
        # Add both to the stack
        self.art_stack.addWidget(self.art)
        self.art_stack.addWidget(self.visualizer)
        
        # Add art stack to container
        art_container_layout.addWidget(self.art_stack, 0, 0)
        
        # Station logo watermark (bottom-right corner)
        self.station_logo = QtWidgets.QLabel()
        self.station_logo.setFixedSize(48, 48)
        self.station_logo.setAlignment(QtCore.Qt.AlignCenter)
        self.station_logo.setStyleSheet("""
            background: rgba(0, 0, 0, 0.5); 
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            padding: 4px;
        """)
        self.station_logo.hide()  # Initially hidden
        art_container_layout.addWidget(self.station_logo, 0, 0, 
                                      QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        
        # Logo rotation timer - rotate every 60 seconds
        self.logo_rotation_timer = QtCore.QTimer()
        self.logo_rotation_timer.timeout.connect(self._rotate_station_logo)
        self.logo_rotation_timer.setInterval(60000)  # 60 seconds
        
        right.addWidget(art_container)
        
        # Metadata card
        self.meta_card = QtWidgets.QFrame(objectName="metaCard")
        meta_layout = QtWidgets.QVBoxLayout(self.meta_card)
        meta_layout.setContentsMargins(12, 10, 12, 10)
        
        self.meta_title = QtWidgets.QLabel(" ", objectName="metaTitle")
        self.meta_title.setWordWrap(True)
        self.meta_sub = QtWidgets.QLabel(" ", objectName="metaSubtitle")
        self.meta_sub.setWordWrap(True)
        
        meta_layout.addWidget(self.meta_title)
        meta_layout.addWidget(self.meta_sub)
        right.addWidget(self.meta_card)
        right.addStretch(1)
        
        grid.addLayout(right, 1, 1)
        
    def _create_tray_icon(self):
        """Create system tray icon"""
        self._tray = QtWidgets.QSystemTrayIcon(self)
        self._tray.setIcon(QtGui.QIcon(emoji_pixmap("📻")))
        self._tray.setToolTip(APP_NAME)
        
        tray_menu = QtWidgets.QMenu()
        act_show = tray_menu.addAction("Show")
        act_hide = tray_menu.addAction("Hide")
        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("Quit")
        
        act_show.triggered.connect(self.showNormal)
        act_hide.triggered.connect(self.hide)
        act_quit.triggered.connect(QtWidgets.QApplication.instance().quit)
        
        self._tray.setContextMenu(tray_menu)
        self._tray.show()
        
    def _setup_components(self):
        """Set up application components"""
        # Initialize handlers
        self.metadata_handler = MetadataHandler(self)
        self.map_handler = MapHandler(self)
        self.sleep_preventer = SleepPreventer()
        
        # Load presets to update button labels
        self._load_presets()
        
        # Initialize worker with LOT directory
        self.worker = Worker(self.cfg, LOT_FILES_DIR)
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.start()
        
        # Connect signals
        self.worker.logLine.connect(self._handle_log_line)
        self.worker.started.connect(self._on_started)
        self.worker.stopped.connect(self._on_stopped)
        self.worker.hdSynced.connect(self._on_hd_synced)
        self.worker.bitrateUpdate.connect(self._update_bitrate)
        
        # Metadata handler signals
        self.metadata_handler.artReady.connect(self._set_album_art)
        self.metadata_handler.artClear.connect(self._clear_album_art)
        self.metadata_handler.stationInfoUpdate.connect(self._update_station_info)
        self.metadata_handler.metadataUpdate.connect(self._update_metadata_display)
        
        # Map handler signals
        self.map_handler.mapReady.connect(self._on_map_ready)
        
        # Initialize state
        self._hd_synced = False
        self._fallback_timer = QtCore.QTimer(self)
        self._fallback_timer.setSingleShot(True)
        self._fallback_timer.timeout.connect(self._maybe_fallback_to_fm)
        
        self.map_window = None
        self._update_lcd()
        
    def _load_settings(self):
        """Load application settings"""
        self.settings = {"show_log": True}
        if SETTINGS_PATH.exists():
            try:
                saved_settings = json.loads(SETTINGS_PATH.read_text())
                self.settings.update(saved_settings)
            except Exception:
                pass
        
        # Apply settings
        show_tabs = self.settings.get("show_log", True)
        self.tabs.setVisible(show_tabs)
        self.btn_toggle_log.setChecked(not show_tabs)
        self.btn_toggle_log.setText("Show Log" if not show_tabs else "Hide Log")
        
        # Adjust window size
        if not show_tabs:
            self.setMinimumSize(1020, 350)
            if self.height() > 400:
                self.resize(self.width(), 400)
                
    def _save_settings(self):
        """Save application settings"""
        try:
            SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2))
        except Exception:
            pass
            
    def _check_dependencies(self):
        """Check for required executables"""
        if not which("nrsc5"):
            self._append_log("WARNING: nrsc5 not found in PATH.")
        if not which("ffplay"):
            self._append_log("WARNING: ffplay not found in PATH.")
        if not which("rtl_fm"):
            self._append_log("Note: rtl_fm not found; analog FM fallback unavailable.")
            
    def _load_presets(self):
        """Load preset stations"""
        self.presets = {}
        if PRESETS_PATH.exists():
            try:
                self.presets = json.loads(PRESETS_PATH.read_text())
            except Exception:
                self.presets = {}
        
        for i, b in enumerate(self.preset_buttons):
            key = f"P{i}"
            if key in self.presets:
                b.setText(f"{self.presets[key]:.1f}")
            else:
                b.setText(f"P{i}")
                
    def _save_preset(self, idx: int, mhz: float):
        """Save a preset station"""
        self.presets[f"P{idx}"] = round(mhz, 1)
        self.presets[f"P{idx}_hd"] = self.cfg.hd_program
        try:
            PRESETS_PATH.write_text(json.dumps(self.presets, indent=2))
        except Exception:
            pass
        self._load_presets()
        
    def _clear_preset(self, idx: int):
        """Clear a preset station"""
        self.presets.pop(f"P{idx}", None)
        self.presets.pop(f"P{idx}_hd", None)
        try:
            PRESETS_PATH.write_text(json.dumps(self.presets, indent=2))
        except Exception:
            pass
        self._load_presets()
        
    def _preset_menu(self, idx: int, pos: QtCore.QPoint):
        """Show preset context menu"""
        b = self.preset_buttons[idx]
        m = QtWidgets.QMenu(b)
        hd_text = f" HD{self.cfg.hd_program + 1}" if self.cfg.hd_program > 0 else ""
        m.addAction(f"Save current ({self._mhz():.1f} MHz{hd_text}) to P{idx}",
                   lambda: self._save_preset(idx, self._mhz()))
        if f"P{idx}" in self.presets:
            m.addAction("Clear preset", lambda: self._clear_preset(idx))
        m.exec(b.mapToGlobal(pos))
        
    def _preset_load(self, idx: int):
        """Load a preset station"""
        key = f"P{idx}"
        if key not in self.presets:
            self._append_log(f"[preset] P{idx} is empty — right-click to save current frequency.")
            return
        
        mhz = self.presets[key]
        hd_prog = self.presets.get(f"P{idx}_hd", 0)
        self.cfg.hd_program = hd_prog
        self.hd_selector.setCurrentIndex(hd_prog)
        self.freq_slider.setValue(int(round(mhz * 10)))
        self._update_lcd()
        
        if not self.btn_play.isEnabled():
            self._play_clicked()
            
    def _mhz(self) -> float:
        """Get current frequency in MHz"""
        return round(self.freq_slider.value() / 10.0, 1)
        
    def _update_lcd(self):
        """Update frequency display"""
        hd_text = f" HD{self.cfg.hd_program + 1}" if hasattr(self, 'cfg') else ""
        self.lcd.setText(f"{self._mhz():.1f} MHz{hd_text}")
        
    def _update_bitrate(self, bitrate: str):
        """Update bitrate display"""
        # Display exactly what we receive from the worker (e.g., "46.7 kbps")
        self.bitrate_display.setText(bitrate)
        
    def _cycle_station_info(self):
        """Cycle through station information items"""
        if not self.station_info_items:
            return
            
        self.station_info_index = (self.station_info_index + 1) % len(self.station_info_items)
        info = self.station_info_items[self.station_info_index]
        
        self.station_display.setText(info['text'])
        self.station_display.setStyleSheet(f"""
            QLabel {{
                color: {info['color']};
                background: transparent;
                padding: 2px;
                font-weight: {info['weight']};
                font-style: {info.get('style', 'normal')};
            }}
        """)
        
    def _update_station_info(self, items: list):
        """Update station information items for cycling"""
        self.station_info_items = items
        self.station_info_index = 0
        
        if items:
            # Display first item immediately
            info = items[0]
            self.station_display.setText(info['text'])
            self.station_display.setStyleSheet(f"""
                QLabel {{
                    color: {info['color']};
                    background: transparent;
                    padding: 2px;
                    font-weight: {info['weight']};
                    font-style: {info.get('style', 'normal')};
                }}
            """)
            
            # Start cycling if multiple items
            if len(items) > 1:
                self.station_info_timer.start()
            else:
                self.station_info_timer.stop()
        else:
            self.station_info_timer.stop()
            
    def _update_metadata_display(self, title: str, subtitle: str):
        """Update metadata display"""
        self.meta_title.setText(title)
        self.meta_sub.setText(subtitle)
        
    def _on_hd_program_changed(self, index: int):
        """Handle HD program selection change"""
        self.cfg.hd_program = index
        self._update_lcd()
        
        if hasattr(self, 'worker') and self.worker._mode == "hd":
            self._append_log(f"[hd] Switching to HD{index + 1}")
            self._play_clicked()
            
    def _toggle_log_view(self):
        """Toggle log visibility"""
        show_tabs = not self.btn_toggle_log.isChecked()
        self.tabs.setVisible(show_tabs)
        self.btn_toggle_log.setText("Show Log" if not show_tabs else "Hide Log")
        
        self.settings["show_log"] = show_tabs
        self._save_settings()
        
        if show_tabs:
            self.setMinimumSize(1020, 580)
        else:
            self.setMinimumSize(1020, 350)
            if self.height() > 400:
                self.resize(self.width(), 400)
                
    def _toggle_mute(self):
        """Toggle mute state"""
        is_muted = self.btn_mute.isChecked()
        self.btn_mute.setText("🔇" if is_muted else "🔊")
        self._append_log(f"[audio] {'Muted' if is_muted else 'Unmuted'}")
        
        if hasattr(self, 'worker') and self.worker._mode:
            current_mode = self.worker._mode
            if current_mode == "hd":
                QtCore.QMetaObject.invokeMethod(self.worker, "start_hd", 
                                               QtCore.Qt.QueuedConnection,
                                               QtCore.Q_ARG(bool, is_muted))
            elif current_mode == "fm":
                QtCore.QMetaObject.invokeMethod(self.worker, "start_fm", 
                                               QtCore.Qt.QueuedConnection,
                                               QtCore.Q_ARG(bool, is_muted))
                                               
    def _play_clicked(self):
        """Start playing"""
        self.cfg.mhz = self._mhz()
        self._hd_synced = False
        
        # Reset metadata and set frequency
        self.metadata_handler.reset()
        self.metadata_handler.set_frequency(self.cfg.mhz)
        self.map_handler.reset()
        
        # Reset station logo
        self.station_logo.hide()
        self.logo_rotation_timer.stop()
        
        # Update UI
        self.station_display.setText("Tuning HD...")
        self.meta_title.setText(f"{self._mhz():.1f} MHz")
        self.meta_sub.setText("Tuning...")
        self.art_stack.setCurrentWidget(self.visualizer)
        
        self.btn_play.setEnabled(False)
        
        # Start HD radio
        is_muted = self.btn_mute.isChecked()
        QtCore.QMetaObject.invokeMethod(self.worker, "start_hd", 
                                       QtCore.Qt.QueuedConnection,
                                       QtCore.Q_ARG(bool, is_muted))
        
        # Start fallback timer if enabled
        if self.chk_fallback.isChecked() and which("rtl_fm"):
            self._fallback_timer.start(int(FALLBACK_TIMEOUT_S * 1000))
        else:
            self._fallback_timer.stop()
            
        self._update_lcd()
        
    def _stop_clicked(self):
        """Stop playing"""
        self._fallback_timer.stop()
        QtCore.QMetaObject.invokeMethod(self.worker, "stop")
        self.btn_play.setEnabled(True)
        self._update_lcd()
        
    def _on_started(self, mode: str):
        """Handle playback started"""
        self._append_log(f"[audio] started ({mode})")
        self.visualizer.set_playing(True)
        self.sleep_preventer.prevent_sleep(True)
        
        if mode == "hd":
            self.station_display.setText("Tuning HD..." if not self._hd_synced else "HD Radio")
        else:
            self.station_display.setText("Analog FM")
            
    def _on_stopped(self, rc: int, mode: str):
        """Handle playback stopped"""
        self._append_log(f"[audio] stopped rc={rc} ({mode})")
        self.station_display.setText("Stopped")
        self.btn_play.setEnabled(True)
        self.visualizer.set_playing(False)
        self.sleep_preventer.prevent_sleep(False)
        
    def _on_hd_synced(self):
        """Handle HD sync achieved"""
        self._hd_synced = True
        if self._fallback_timer.isActive():
            self._fallback_timer.stop()
        self._append_log("[hd] synchronized; staying on digital")
        
        if not self.metadata_handler._station_name:
            self.station_display.setText("HD Radio")
            self.station_display.setStyleSheet("""
                QLabel {
                    color: #7CFC00;
                    background: transparent;
                    padding: 2px;
                    font-weight: 600;
                }
            """)
            
    def _maybe_fallback_to_fm(self):
        """Fallback to analog FM if HD sync fails"""
        if self._hd_synced:
            return
            
        self._append_log(f"[fallback] no HD sync in {FALLBACK_TIMEOUT_S:.0f}s, switching to analog FM")
        self.station_display.setText("Analog FM")
        self.station_display.setStyleSheet("""
            QLabel {
                color: #ffaa00;
                background: transparent;
                padding: 2px;
                font-weight: 500;
            }
        """)
        
        self.meta_title.setText("SDR-Boombox (Analog FM Mode)")
        self.meta_sub.setText("by @sjhilt")
        
        is_muted = self.btn_mute.isChecked()
        QtCore.QMetaObject.invokeMethod(self.worker, "start_fm", 
                                       QtCore.Qt.QueuedConnection,
                                       QtCore.Q_ARG(bool, is_muted))
                                       
    @QtCore.Slot(str)
    def _handle_log_line(self, s: str):
        """Handle log line from worker"""
        if len(s) > 5000:
            s = s[:5000] + "... [truncated]"
        
        self._append_log(s)
        
        # Pass to metadata handler for parsing
        self.metadata_handler.parse_log_line(s, self.cfg.hd_program, self._append_log)
        
        # Pass to map handler for map data
        self.map_handler.parse_log_line(s)
        
    def _append_log(self, s: str):
        """Append text to log widget"""
        try:
            if not hasattr(self, 'log') or not self.log:
                return
                
            if len(s) > 5000:
                s = s[:5000] + "... [truncated]"
            
            QtCore.QMetaObject.invokeMethod(self.log, "append", 
                                           QtCore.Qt.QueuedConnection,
                                           QtCore.Q_ARG(str, s))
            
            if hasattr(self, 'log_line_count'):
                self.log_line_count += 1
                if self.log_line_count > MAX_LOG_LINES:
                    QtCore.QMetaObject.invokeMethod(self, "_trim_log", 
                                                   QtCore.Qt.QueuedConnection)
        except RuntimeError:
            pass
        except Exception:
            pass
            
    @QtCore.Slot()
    def _trim_log(self):
        """Trim log to prevent memory issues"""
        try:
            if hasattr(self, 'log') and self.log:
                text = self.log.toPlainText()
                lines = text.split('\n')
                if len(lines) > MAX_LOG_LINES:
                    self.log.setPlainText('\n'.join(lines[-MAX_LOG_LINES:]))
                    self.log_line_count = MAX_LOG_LINES
                cursor = self.log.textCursor()
                cursor.movePosition(QtGui.QTextCursor.End)
                self.log.setTextCursor(cursor)
        except:
            pass
            
    @QtCore.Slot(QtGui.QPixmap)
    def _set_album_art(self, pm: QtGui.QPixmap):
        """Set album art or station logo"""
        if pm and not pm.isNull():
            # Check if this is a station logo (smaller size typically)
            if pm.width() <= 100 and pm.height() <= 100:
                # This is likely a station logo
                self._display_station_logo(pm)
            else:
                # Regular album art
                self.art.setPixmap(pm.scaled(self.art.size(), 
                                            QtCore.Qt.KeepAspectRatio, 
                                            QtCore.Qt.SmoothTransformation))
                self.art_stack.setCurrentWidget(self.art)
                
                # Also check if we should display a station logo
                if hasattr(self.metadata_handler, 'station_logos') and self.metadata_handler.station_logos:
                    # Display the first logo
                    first_logo = self.metadata_handler.station_logos[0]
                    self._display_station_logo(first_logo['pixmap'])
        else:
            self.art_stack.setCurrentWidget(self.visualizer)
    
    @QtCore.Slot()
    def _clear_album_art(self):
        """Clear album art and show visualizer"""
        self.art_stack.setCurrentWidget(self.visualizer)
        # Keep station logo if it exists
        if hasattr(self.metadata_handler, 'station_logos') and self.metadata_handler.station_logos:
            # Keep displaying the station logo watermark
            pass
            
    def _on_map_ready(self, pixmap: QtGui.QPixmap):
        """Handle map ready from map handler"""
        if self.map_window and hasattr(self.map_window, 'update_map'):
            self.map_window.update_map(pixmap)
            
        # Flash map button to indicate update
        self.btn_open_map.setText("Map •")
        QtCore.QTimer.singleShot(3000, lambda: self.btn_open_map.setText("Map"))
        
    def _rotate_station_logo(self):
        """Rotate to the next station logo in the collection"""
        if hasattr(self.metadata_handler, 'station_logos') and len(self.metadata_handler.station_logos) > 1:
            # Get next logo from metadata handler
            next_logo_pm, logo_info = self.metadata_handler.get_next_logo()
            if next_logo_pm:
                # Scale and display the logo
                scaled_logo = next_logo_pm.scaled(40, 40, QtCore.Qt.KeepAspectRatio, 
                                                 QtCore.Qt.SmoothTransformation)
                self.station_logo.setPixmap(scaled_logo)
                self.station_logo.show()
                self.station_logo.raise_()  # Ensure it's on top
                
                # Update tooltip
                port_info = f" (port {logo_info['port']})" if logo_info.get('port') else ""
                total_logos = len(self.metadata_handler.station_logos)
                current_index = self.metadata_handler.current_logo_index
                self.station_logo.setToolTip(f"Logo {current_index + 1}/{total_logos}{port_info}")
                self._append_log(f"[art] Rotating to logo {current_index + 1}/{total_logos}")
    
    def _display_station_logo(self, pixmap: QtGui.QPixmap):
        """Display a station logo in the watermark area"""
        if pixmap and not pixmap.isNull():
            scaled_logo = pixmap.scaled(40, 40, QtCore.Qt.KeepAspectRatio, 
                                       QtCore.Qt.SmoothTransformation)
            self.station_logo.setPixmap(scaled_logo)
            self.station_logo.show()
            self.station_logo.raise_()  # Ensure it's on top
            
            # Start rotation timer if we have multiple logos
            if hasattr(self.metadata_handler, 'station_logos') and len(self.metadata_handler.station_logos) > 1:
                if not self.logo_rotation_timer.isActive():
                    self.logo_rotation_timer.start()
    
    def _open_map_window(self):
        """Open or focus the map window"""
        if self.map_window is None or not self.map_window.isVisible():
            self.map_window = MapWindow(self.map_handler)
            self.map_window.show()
        else:
            self.map_window.raise_()
            self.map_window.activateWindow()
            
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """Handle application close"""
        try:
            if self.map_window and self.map_window.isVisible():
                self.map_window.close()
            self.sleep_preventer.prevent_sleep(False)
            self._fallback_timer.stop()
            QtCore.QMetaObject.invokeMethod(self.worker, "stop")
            if hasattr(self, 'thread') and self.thread.isRunning():
                self.thread.quit()
                self.thread.wait(1500)
            self._tray.hide()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    """Main entry point"""
    # Handle Ctrl+C gracefully
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    
    # Check for --stats flag
    if "--stats" in sys.argv:
        try:
            from boombox_stats import StatsViewer
            app = QtWidgets.QApplication(sys.argv)
            viewer = StatsViewer()
            viewer.show()
            sys.exit(app.exec())
        except ImportError:
            print("Error: boombox_stats.py not found. Please ensure it's in the same directory.")
            sys.exit(1)
    else:
        # Normal boombox operation
        app = QtWidgets.QApplication(sys.argv)
        w = SDRBoombox()
        w.show()
        sys.exit(app.exec())


if __name__ == "__main__":
    main()
