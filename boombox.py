
"""
===============================================================
   SDR-Boombox
   HD Radio (NRSC-5) + Analog FM Receiver & Visual Interface
===============================================================

Author:     @sjhilt
Project:    SDR-Boombox (Software Defined Radio Tuner)
License:    MIT License
Website:    https://github.com/sjhilt/SDR-Boombox
Version:    1.0.5
Python:     3.10+

Description:
------------
SDR-Boombox is a modern GUI-driven radio tuner for Software Defined Radios
such as the RTL-SDR. It attempts HD Radio decoding first using `nrsc5`, and
automatically falls back to analog wideband FM when digital signals are not
available. The interface features live metadata, album art, scanning, presets,
and a small system tray icon.

"""

import os, sys, re, json, subprocess, threading, time, shutil, math
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import tempfile

from PySide6 import QtCore, QtGui, QtWidgets
import random

# Import stats module if available
try:
    from boombox_stats import StatsDatabase
    STATS_ENABLED = True
except ImportError:
    STATS_ENABLED = False

APP_NAME = "SDR-Boombox"
FALLBACK_TIMEOUT_S = 6.0
PRESETS_PATH = Path.home() / ".sdr_boombox_presets.json"
SETTINGS_PATH = Path.home() / ".sdr_boombox_settings.json"
LOT_FILES_DIR = Path.home() / ".sdr_boombox_data"

def which(cmd: str) -> str | None:
    p = shutil.which(cmd)
    if p: return p
    # Windows .exe quick check
    for d in os.getenv("PATH", "").split(os.pathsep):
        if not d: continue
        cand = Path(d) / (cmd + ".exe")
        if cand.exists(): return str(cand)
    return None

def emoji_pixmap(emoji: str, size: int = 256) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    font_family = "Apple Color Emoji" if sys.platform == "darwin" else "Segoe UI Emoji"
    painter.setFont(QtGui.QFont(font_family, int(size * 0.75)))
    painter.drawText(pm.rect(), QtCore.Qt.AlignCenter, emoji)
    painter.end()
    return pm


class VisualizerWidget(QtWidgets.QWidget):
    """Winamp-style spectrum analyzer visualization"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(260, 260)
        
        # Visualization parameters
        self.num_bars = 20
        self.bar_heights = [0.0] * self.num_bars
        self.target_heights = [0.0] * self.num_bars
        self.peak_heights = [0.0] * self.num_bars
        self.peak_hold = [0] * self.num_bars
        
        # Colors for gradient effect (classic Winamp green-yellow-red)
        self.gradient_colors = [
            QtGui.QColor(0, 255, 0),    # Green
            QtGui.QColor(128, 255, 0),  # Yellow-green
            QtGui.QColor(255, 255, 0),   # Yellow
            QtGui.QColor(255, 128, 0),   # Orange
            QtGui.QColor(255, 0, 0),     # Red
        ]
        
        # Animation timer
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_visualization)
        self.timer.start(50)  # 20 FPS
        
        # Background
        self.setStyleSheet("background: #000000; border: 1px solid #1a1a1a; border-radius: 12px;")
        
        self.is_playing = False
        
    def set_playing(self, playing: bool):
        """Set whether audio is playing to animate the visualization"""
        self.is_playing = playing
        
    def update_visualization(self):
        """Update the visualization bars"""
        if self.is_playing:
            # Simulated visualization
            for i in range(self.num_bars):
                # Create a frequency response curve (higher in bass/mid, lower in treble)
                freq_factor = 1.0 - (i / self.num_bars) * 0.5
                base_height = random.uniform(0.2, 1.0) * freq_factor
                
                # Add some rhythm simulation (occasional beats)
                if random.random() < 0.15:  # 15% chance of a "beat"
                    base_height = min(1.0, base_height + random.uniform(0.3, 0.5))
                
                self.target_heights[i] = base_height
        else:
            # Gradually decrease to zero when not playing
            self.target_heights = [0.0] * self.num_bars
        
        # Smooth animation towards target heights
        for i in range(self.num_bars):
            diff = self.target_heights[i] - self.bar_heights[i]
            self.bar_heights[i] += diff * 0.3  # Smoothing factor
            
            # Update peaks
            if self.bar_heights[i] > self.peak_heights[i]:
                self.peak_heights[i] = self.bar_heights[i]
                self.peak_hold[i] = 20  # Hold peak for 20 frames
            elif self.peak_hold[i] > 0:
                self.peak_hold[i] -= 1
            else:
                # Peak falls slowly
                self.peak_heights[i] *= 0.95
        
        self.update()
    
    def paintEvent(self, event):
        """Paint the spectrum analyzer"""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        
        # Draw background
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0))
        
        # Calculate bar dimensions
        width = self.width()
        height = self.height()
        bar_width = (width - 40) / self.num_bars  # Leave margins
        bar_spacing = bar_width * 0.2
        actual_bar_width = bar_width - bar_spacing
        
        # Draw title
        painter.setPen(QtGui.QColor(100, 255, 100))
        font = QtGui.QFont("Arial", 10)
        painter.setFont(font)
        painter.drawText(QtCore.QRect(0, 5, width, 20), 
                        QtCore.Qt.AlignCenter, "SPECTRUM ANALYZER")
        
        # Draw bars
        for i in range(self.num_bars):
            x = 20 + i * bar_width
            bar_height = self.bar_heights[i] * (height - 60)
            y = height - bar_height - 20
            
            if bar_height > 0:
                # Create gradient for the bar
                gradient = QtGui.QLinearGradient(x, y + bar_height, x, y)
                
                # Color based on height
                for j, color in enumerate(self.gradient_colors):
                    position = j / (len(self.gradient_colors) - 1)
                    gradient.setColorAt(position, color)
                
                painter.fillRect(QtCore.QRectF(x, y, actual_bar_width, bar_height), gradient)
                
                # Draw peak indicator
                if self.peak_heights[i] > 0:
                    peak_y = height - (self.peak_heights[i] * (height - 60)) - 20
                    painter.fillRect(QtCore.QRectF(x, peak_y - 2, actual_bar_width, 3),
                                   QtGui.QColor(255, 255, 255))
        
        # Draw reflection effect (dimmer bars below)
        painter.setOpacity(0.2)
        for i in range(self.num_bars):
            x = 20 + i * bar_width
            bar_height = self.bar_heights[i] * (height - 60) * 0.3  # Smaller reflection
            y = height - 20
            
            if bar_height > 0:
                gradient = QtGui.QLinearGradient(x, y, x, y + bar_height)
                gradient.setColorAt(0, QtGui.QColor(0, 100, 0))
                gradient.setColorAt(1, QtGui.QColor(0, 0, 0))
                painter.fillRect(QtCore.QRectF(x, y, actual_bar_width, bar_height), gradient)

@dataclass
class Cfg:
    mhz: float = 105.5    # your workflow target
    gain: float | None = 40.0
    device_index: int | None = None
    volume: float = 1.0
    ppm: int = 5          # +5 sounded best for you
    hd_program: int = 0   # 0 for HD1, 1 for HD2, etc.

class Worker(QtCore.QObject):
    started = QtCore.Signal(str)       # "hd" | "fm"
    stopped = QtCore.Signal(int, str)  # rc, mode
    logLine = QtCore.Signal(str)
    hdSynced = QtCore.Signal()

    def __init__(self, cfg: Cfg):
        super().__init__()
        self.cfg = cfg
        self._mode: str | None = None
        self._nrsc5: subprocess.Popen | None = None
        self._fm: subprocess.Popen | None = None
        self._ffplay: subprocess.Popen | None = None
        self._stop_evt = threading.Event()

    # ---------- command builders ----------
    def ffplay_cmd(self, is_fm: bool) -> list[str]:
        # Match your working CLI exactly for analog; HD stays raw pipe to ffplay
        base = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "warning"]
        if is_fm:
            base += ["-f", "s16le", "-ar", "48000", "-i", "-"]
        else:
            base += ["-i", "-"]
        return base

    def nrsc5_cmd(self) -> list[str]:
        # Ensure the LOT files directory exists
        LOT_FILES_DIR.mkdir(exist_ok=True)
        
        cmd = ["nrsc5"]
        if self.cfg.gain is not None: cmd += ["-g", str(self.cfg.gain)]
        if self.cfg.device_index is not None: cmd += ["-d", str(self.cfg.device_index)]
        # --dump-aas-files saves LOT files (album art and data services) to hidden directory
        cmd += ["--dump-aas-files", str(LOT_FILES_DIR)]
        # -o - pipes audio to stdout for ffplay, use configured HD program (0=HD1, 1=HD2, etc.)
        cmd += ["-o", "-", f"{self.cfg.mhz}", str(self.cfg.hd_program)]
        return cmd

    def rtl_fm_cmd(self) -> list[str]:
        # EXACT analog command shape that works for you:
        # rtl_fm -M wbfm -f {MHz}M -s 200k -r 48k -E deemp=75 -g 28 -p +5 | ffplay -nodisp -autoexit -loglevel warning -f s16le -ar 48000 -
        ppm_signed = f"{int(self.cfg.ppm):+d}" if self.cfg.ppm is not None else "+0"
        gain_val = str(float(self.cfg.gain)) if self.cfg.gain is not None else "0"
        cmd = [
            "rtl_fm",
            "-M", "wbfm",
            "-f", f"{self.cfg.mhz}M",
            "-s", "200k",
            "-r", "48k",
            "-E", "deemp=75",
            "-g", gain_val,
            "-p", ppm_signed
        ]
        return cmd

    # ---------- helpers ----------
    def _pipe_forward(self, src, dst):
        def run():
            try:
                while not self._stop_evt.is_set():
                    chunk = src.read(8192)
                    if not chunk: break
                    if dst:
                        dst.write(chunk); dst.flush()
            except Exception:
                pass
        threading.Thread(target=run, daemon=True, name="pipe-forward").start()

    def _stderr_reader(self, proc, prefix="", on_line=None):
        def run():
            try:
                for line in iter(proc.stderr.readline, b""):
                    if self._stop_evt.is_set(): break
                    s = line.decode("utf-8", "ignore").rstrip()
                    self.logLine.emit(prefix + s)
                    if on_line: on_line(s)
            except Exception:
                pass
        threading.Thread(target=run, daemon=True, name="stderr-reader").start()

    def _terminate(self, p: subprocess.Popen | None):
        if not p: return
        try:
            if p.poll() is None:
                p.terminate()
                try: p.wait(timeout=1.25)
                except subprocess.TimeoutExpired: p.kill()
        except Exception:
            pass

    # ---------- slots ----------
    @QtCore.Slot()
    def stop(self):
        self._stop_evt.set()
        self._terminate(self._nrsc5); self._nrsc5 = None
        self._terminate(self._fm); self._fm = None
        if self._ffplay:
            try:
                if self._ffplay.stdin:
                    try: self._ffplay.stdin.close()
                    except Exception: pass
                if self._ffplay.poll() is None:
                    self._ffplay.terminate()
                    try: self._ffplay.wait(timeout=1.0)
                    except subprocess.TimeoutExpired: self._ffplay.kill()
            except Exception: pass
        rc = self._ffplay.returncode if self._ffplay else 0
        mode = self._mode or ""
        self._ffplay = None
        self._mode = None
        self.stopped.emit(rc or 0, mode)

    @QtCore.Slot()
    def start_hd(self):
        self.stop()
        self._stop_evt.clear()
        self._mode = "hd"
        try:
            self._ffplay = subprocess.Popen(self.ffplay_cmd(is_fm=False), stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._nrsc5 = subprocess.Popen(self.nrsc5_cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            self._pipe_forward(self._nrsc5.stdout, self._ffplay.stdin)

            def parse(line: str):
                if ("Synchronized" in line) or ("Audio program" in line) or ("SIG Service:" in line):
                    self.hdSynced.emit()

            self._stderr_reader(self._nrsc5, "", parse)
            self.started.emit("hd")
        except FileNotFoundError as e:
            self.logLine.emit(f"Missing executable: {e}")
            self.stop()

    @QtCore.Slot()
    def start_fm(self):
        self.stop()
        self._stop_evt.clear()
        self._mode = "fm"
        try:
            self._ffplay = subprocess.Popen(self.ffplay_cmd(is_fm=True), stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._fm = subprocess.Popen(self.rtl_fm_cmd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            self._pipe_forward(self._fm.stdout, self._ffplay.stdin)
            self._stderr_reader(self._fm, "[rtl_fm] ")
            self.started.emit("fm")
        except FileNotFoundError as e:
            self.logLine.emit(f"Missing executable: {e}")
            self.stop()


class SDRBoombox(QtWidgets.QMainWindow):
    # regex
    _ts_re      = re.compile(r"^\s*\d{2}:\d{2}:\d{2}\s+")
    _title_re   = re.compile(r"\bTitle:\s*(.+)", re.IGNORECASE)
    _artist_re  = re.compile(r"\bArtist:\s*(.+)", re.IGNORECASE)
    _album_re   = re.compile(r"\bAlbum:\s*(.+)", re.IGNORECASE)
    _slogan_re  = re.compile(r"\bSlogan:\s*(.+)", re.IGNORECASE)
    _station_re = re.compile(r"\bStation name:\s*(.+)", re.IGNORECASE)

    artReady = QtCore.Signal(QtGui.QPixmap)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDR-Boombox – HD Radio (NRSC-5)")
        # Initial size will be adjusted based on log visibility in _load_settings
        self.setMinimumSize(1020, 350)
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

        # root
        root = QtWidgets.QFrame(objectName="root"); self.setCentralWidget(root)
        grid = QtWidgets.QGridLayout(root); grid.setContentsMargins(16,16,16,16); grid.setHorizontalSpacing(14)

        # Load presets early to check for P0
        self.presets: dict[str,float] = {}
        if PRESETS_PATH.exists():
            try:
                self.presets = json.loads(PRESETS_PATH.read_text())
            except Exception:
                self.presets = {}
        
        # Use P0 if it exists, otherwise default to 98.7
        default_freq = self.presets.get("P0", 98.7)
        
        # state (cfg before slider!)
        self.cfg = Cfg(mhz=default_freq, gain=40.0, ppm=5)

        # LCD
        self.lcd = QtWidgets.QLabel("—.— MHz", objectName="lcd")
        f = self.lcd.font(); f.setPointSize(22); self.lcd.setFont(f)
        self.lcd.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(self.lcd, 0, 0, 1, 2)

        # left controls
        left = QtWidgets.QVBoxLayout()

        self.freq_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.freq_slider.setRange(880, 1080)
        self.freq_slider.setValue(int(round(self.cfg.mhz * 10)))
        self.freq_slider.valueChanged.connect(self._update_lcd)
        left.addWidget(self.freq_slider)

        # presets P0..P3
        pres_row = QtWidgets.QHBoxLayout()
        self.preset_buttons: list[QtWidgets.QPushButton] = []
        for i in range(4):
            b = QtWidgets.QPushButton(f"P{i}")
            b.setCheckable(False)
            b.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, idx=i: self._preset_menu(idx, pos))
            b.clicked.connect(lambda _=False, idx=i: self._preset_load(idx))
            self.preset_buttons.append(b)
            pres_row.addWidget(b)
        left.addLayout(pres_row)

        # play/stop + fallback
        row2 = QtWidgets.QHBoxLayout()
        self.btn_play = QtWidgets.QPushButton("Play")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.chk_fallback = QtWidgets.QCheckBox("Auto analog fallback")
        self.chk_fallback.setChecked(True)
        row2.addWidget(self.btn_play); row2.addWidget(self.btn_stop); row2.addWidget(self.chk_fallback)
        left.addLayout(row2)
        
        # HD program selector (HD1, HD2, etc.) and log toggle
        hd_row = QtWidgets.QHBoxLayout()
        hd_label = QtWidgets.QLabel("HD Channel:")
        hd_label.setStyleSheet("color: #eee;")
        self.hd_selector = QtWidgets.QComboBox()
        self.hd_selector.addItems(["HD1", "HD2", "HD3", "HD4"])
        self.hd_selector.setCurrentIndex(0)
        self.hd_selector.currentIndexChanged.connect(self._on_hd_program_changed)
        
        # Add log toggle button
        self.btn_toggle_log = QtWidgets.QPushButton("Hide Log")
        self.btn_toggle_log.setCheckable(True)
        self.btn_toggle_log.setMaximumWidth(80)
        self.btn_toggle_log.clicked.connect(self._toggle_log_view)
        
        # Map button (opens separate window)
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
        self.tabs.addTab(self.log, "Log")
        
        # Map window (initially None, created on demand)
        self.map_window = None
        self.map_widget = None
        
        # Initially show/hide based on saved preference
        left.addWidget(self.tabs, 1)

        grid.addLayout(left, 1, 0)

        # right: art + metadata
        right = QtWidgets.QVBoxLayout()
        
        # Create container for art/visualizer with logo watermark
        art_container = QtWidgets.QWidget()
        art_container.setFixedSize(260, 260)
        art_container_layout = QtWidgets.QGridLayout(art_container)
        art_container_layout.setContentsMargins(0, 0, 0, 0)
        
        # Create a stacked widget to switch between album art and visualizer
        self.art_stack = QtWidgets.QStackedWidget()
        self.art_stack.setFixedSize(260, 260)
        
        # Album art label
        self.art = QtWidgets.QLabel(objectName="art")
        self.art.setFixedSize(260, 260)
        self.art.setAlignment(QtCore.Qt.AlignCenter)
        # Default radio icon - create a simple text placeholder
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
        art_container_layout.addWidget(self.station_logo, 0, 0, QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight)
        
        right.addWidget(art_container)

        self.meta_card = QtWidgets.QFrame(objectName="metaCard")
        meta_layout = QtWidgets.QVBoxLayout(self.meta_card); meta_layout.setContentsMargins(12,10,12,10)
        
        self.meta_title = QtWidgets.QLabel(" ", objectName="metaTitle"); self.meta_title.setWordWrap(True)
        self.meta_sub   = QtWidgets.QLabel(" ", objectName="metaSubtitle"); self.meta_sub.setWordWrap(True)
        meta_layout.addWidget(self.meta_title); meta_layout.addWidget(self.meta_sub)
        right.addWidget(self.meta_card)
        right.addStretch(1)
        grid.addLayout(right, 1, 1)

        # tray icon - use radio emoji
        self._tray = QtWidgets.QSystemTrayIcon(self)
        self._tray.setIcon(QtGui.QIcon(emoji_pixmap("📻")))
        self._tray.setToolTip(APP_NAME)
        tray_menu = QtWidgets.QMenu()
        act_show = tray_menu.addAction("Show"); act_hide = tray_menu.addAction("Hide")
        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("Quit")
        act_show.triggered.connect(self.showNormal); act_hide.triggered.connect(self.hide)
        act_quit.triggered.connect(QtWidgets.QApplication.instance().quit)
        self._tray.setContextMenu(tray_menu); self._tray.show()
        
        # Sleep prevention timer (macOS caffeinate)
        self._caffeinate_process = None

        # runtime objects
        self._load_presets()
        self._load_settings()  # Load settings including log visibility
        self.worker = Worker(self.cfg)
        self.thread = QtCore.QThread(self); self.worker.moveToThread(self.thread); self.thread.start()

        self._hd_synced = False
        self._fallback_timer = QtCore.QTimer(self); self._fallback_timer.setSingleShot(True)
        self._fallback_timer.timeout.connect(self._maybe_fallback_to_fm)

        # metadata and art state
        self._station_name = ""
        self._last_title = ""
        self._last_artist = ""
        self._last_album = ""
        self._has_song_meta = False
        self._last_logged_song = ""  # Track last logged song to avoid duplicates
        self._current_art_key = ""   # to avoid flicker
        self._has_album_art = False  # Track if we have real album art
        self._has_lot_art = False    # Track if LOT art is available in stream
        self._station_logo_file = ""  # Track current station logo file
        self._pending_lot_art = ""  # Store LOT art that arrives before metadata
        self._traffic_tiles = {}  # Store traffic map tiles
        self._last_traffic_timestamp = ""  # Track traffic map timestamp
        self._weather_overlay_file = ""  # Store current weather overlay file
        self._combined_map = None  # Store the combined traffic map
        self._map_has_data = False  # Track if map has data to show
        self._song_change_count = 0  # Track song changes for cleanup
        self._meta_debounce = QtCore.QTimer(self); self._meta_debounce.setSingleShot(True)
        self._meta_debounce.setInterval(350)  # ms
        self._meta_debounce.timeout.connect(self._maybe_fetch_art)

        # signals
        self.btn_play.clicked.connect(self._play_clicked)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.worker.logLine.connect(self._handle_log_line)
        self.worker.started.connect(self._on_started)
        self.worker.stopped.connect(self._on_stopped)
        self.worker.hdSynced.connect(self._on_hd_synced)
        self.artReady.connect(self._set_album_art)

        self._update_lcd()

        # sanity
        if not which("nrsc5"): self._append_log("WARNING: nrsc5 not found in PATH.")
        if not which("ffplay"): self._append_log("WARNING: ffplay not found in PATH.")
        if not which("rtl_fm"): self._append_log("Note: rtl_fm not found; analog FM fallback unavailable.")

    # ----- settings & presets -----
    def _load_settings(self):
        """Load application settings"""
        self.settings = {"show_log": True}  # Default settings
        if SETTINGS_PATH.exists():
            try:
                saved_settings = json.loads(SETTINGS_PATH.read_text())
                self.settings.update(saved_settings)
            except Exception:
                pass
        
        # Apply settings
        show_tabs = self.settings.get("show_log", True)
        self.tabs.setVisible(show_tabs)
        self.btn_toggle_log.setChecked(show_tabs)
        # Set initial button text
        if show_tabs:
            self.btn_toggle_log.setText("Hide Log")
        else:
            self.btn_toggle_log.setText("Show Log")
        
        # Adjust window size based on tabs visibility
        if not show_tabs:
            # Make window smaller when tabs are hidden
            self.setMinimumSize(1020, 350)
            if self.height() > 400:
                self.resize(self.width(), 400)
    
    def _save_settings(self):
        """Save application settings"""
        try:
            SETTINGS_PATH.write_text(json.dumps(self.settings, indent=2))
        except Exception:
            pass
    
    def _toggle_log_view(self):
        """Toggle the visibility of the tabs (log and data services)"""
        show_tabs = self.btn_toggle_log.isChecked()
        self.tabs.setVisible(show_tabs)
        
        # Update button text based on state
        if show_tabs:
            self.btn_toggle_log.setText("Hide Log")
        else:
            self.btn_toggle_log.setText("Show Log")
        
        # Save preference
        self.settings["show_log"] = show_tabs
        self._save_settings()
        
        # Adjust window minimum size
        if show_tabs:
            self.setMinimumSize(1020, 580)
        else:
            self.setMinimumSize(1020, 350)
            # Optionally resize window to be smaller
            if self.height() > 400:
                self.resize(self.width(), 400)
    
    def _load_presets(self):
        self.presets: dict[str,float] = {}
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
        # Save both frequency and HD program selection
        self.presets[f"P{idx}"] = round(mhz, 1)
        self.presets[f"P{idx}_hd"] = self.cfg.hd_program
        try:
            PRESETS_PATH.write_text(json.dumps(self.presets, indent=2))
        except Exception:
            pass
        self._load_presets()

    def _clear_preset(self, idx: int):
        self.presets.pop(f"P{idx}", None)
        self.presets.pop(f"P{idx}_hd", None)
        try:
            PRESETS_PATH.write_text(json.dumps(self.presets, indent=2))
        except Exception:
            pass
        self._load_presets()

    def _preset_menu(self, idx: int, pos: QtCore.QPoint):
        b = self.preset_buttons[idx]
        m = QtWidgets.QMenu(b)
        hd_text = f" HD{self.cfg.hd_program + 1}" if self.cfg.hd_program > 0 else ""
        m.addAction(f"Save current ({self._mhz():.1f} MHz{hd_text}) to P{idx}",
                    lambda: self._save_preset(idx, self._mhz()))
        if f"P{idx}" in self.presets:
            m.addAction("Clear preset", lambda: self._clear_preset(idx))
        m.exec(b.mapToGlobal(pos))

    def _preset_load(self, idx: int):
        key = f"P{idx}"
        if key not in self.presets:
            self._append_log(f"[preset] P{idx} is empty — right-click to save current frequency.")
            return
        mhz = self.presets[key]
        # Load HD program if saved
        hd_prog = self.presets.get(f"P{idx}_hd", 0)
        self.cfg.hd_program = hd_prog
        self.hd_selector.setCurrentIndex(hd_prog)
        self.freq_slider.setValue(int(round(mhz * 10)))
        self._update_lcd()
        if self.btn_play.isEnabled() is False:
            self._play_clicked()

    # ----- UI helpers -----
    def _mhz(self) -> float: return round(self.freq_slider.value() / 10.0, 1)

    def _update_lcd(self):
        hd_text = f" HD{self.cfg.hd_program + 1}" if hasattr(self, 'cfg') else ""
        self.lcd.setText(f"{self._mhz():.1f} MHz{hd_text}")
    
    def _on_hd_program_changed(self, index: int):
        """Handle HD program selection change"""
        self.cfg.hd_program = index
        self._update_lcd()
        # If currently playing HD, restart with new program
        if hasattr(self, 'worker') and self.worker._mode == "hd":
            self._append_log(f"[hd] Switching to HD{index + 1}")
            self._play_clicked()

    def _append_log(self, s: str):
        # Only append to log if it exists
        if hasattr(self, 'log'):
            self.log.append(s)

    # ----- playback buttons -----
    def _play_clicked(self):
        self.cfg.mhz = self._mhz()
        self._hd_synced = False
        
        # Reset metadata and album art when starting new station
        self._station_name = ""
        self._last_title = ""
        self._last_artist = ""
        self._last_album = ""
        self._has_song_meta = False
        self._current_art_key = ""
        self._has_lot_art = False
        self._station_logo_file = ""
        self._pending_lot_art = ""  # Reset pending art
        self._song_change_count = 0  # Reset song counter
        self._weather_overlay_file = ""  # Reset weather overlay
        self._combined_map = None  # Reset combined map
        self._map_has_data = False  # Reset map data flag
        self.station_logo.hide()  # Hide station logo when changing stations
        
        # Clean up old LOT files (optional - keep last 100 files)
        self._cleanup_lot_files()
        
        # Check for existing traffic map tiles and weather overlay
        self._load_existing_map_data()
        
        # Reset UI displays
        self.meta_title.setText(f"{self._mhz():.1f} MHz")
        self.meta_sub.setText("Tuning...")
        # Start with visualizer while tuning
        self.art_stack.setCurrentWidget(self.visualizer)
        self._has_album_art = False
        
        self.btn_play.setEnabled(False)
        QtCore.QMetaObject.invokeMethod(self.worker, "start_hd")
        if self.chk_fallback.isChecked() and which("rtl_fm"):
            self._fallback_timer.start(int(FALLBACK_TIMEOUT_S * 1000))
        else:
            self._fallback_timer.stop()
        self._update_lcd()

    def _stop_clicked(self):
        self._fallback_timer.stop()
        QtCore.QMetaObject.invokeMethod(self.worker, "stop")
        self.btn_play.setEnabled(True)
        self._update_lcd()

    # ----- worker callbacks -----
    def _on_started(self, mode: str):
        self._append_log(f"[audio] started ({mode})")
        # Update LCD to show playing status
        current_text = self.lcd.text()
        if " [PLAYING]" not in current_text:
            self.lcd.setText(current_text + " [PLAYING]")
        # Start visualizer animation
        self.visualizer.set_playing(True)
        # Prevent sleep while playing
        self._prevent_sleep(True)

    def _on_stopped(self, rc: int, mode: str):
        self._append_log(f"[audio] stopped rc={rc} ({mode})")
        # Remove playing status from LCD
        self.lcd.setText(self.lcd.text().replace(" [PLAYING]", ""))
        self.btn_play.setEnabled(True)
        # Stop visualizer animation
        self.visualizer.set_playing(False)
        # Allow sleep when stopped
        self._prevent_sleep(False)

    def _on_hd_synced(self):
        self._hd_synced = True
        if self._fallback_timer.isActive():
            self._fallback_timer.stop()
        self._append_log("[hd] synchronized; staying on digital")

    def _maybe_fallback_to_fm(self):
        if self._hd_synced:
            return
        self._append_log(f"[fallback] no HD sync in {FALLBACK_TIMEOUT_S:.0f}s, switching to analog FM")
        self.meta_title.setText("SDR-Boombox (Analog FM Mode)")
        self.meta_sub.setText("by @sjhilt")
        QtCore.QMetaObject.invokeMethod(self.worker, "start_fm")

    # ----- metadata/log parsing -----
    def _handle_log_line(self, s: str):
        self._append_log(s)
        line = self._ts_re.sub("", s).strip()
        
        # Check for LOT (album art) file writes from nrsc5
        # nrsc5 outputs: "LOT file: port=0810 lot=136 name=TMT_03g9rc_2_1_20251031_1434_0088.png size=5131 mime=4F328CA0"
        if "LOT file:" in line:
            # Extract filename and port from the line
            lot_match = re.search(r"port=(\d+).*?name=([^\s]+)", line)
            if lot_match:
                port = lot_match.group(1)
                lot_file = lot_match.group(2).strip()
                
                # Check file type and identify what it is
                if lot_file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                    # Handle traffic map tiles for assembly
                    if 'TMT_' in lot_file:
                        self._handle_traffic_tile(lot_file)
                        return
                    # Handle weather overlay
                    elif 'DWRO_' in lot_file:
                        self._handle_weather_overlay(lot_file)
                        return
                    # Skip DWRI text info files
                    elif 'DWRI_' in lot_file:
                        return
                    else:
                        # Not a traffic/weather file, check if it's album art or station logo
                        # Check for station logo patterns
                        # Station logos often have patterns like: SLWRXR$$, or persist with same LOT ID
                        is_likely_logo = ('$$' in lot_file or 'SLWRXR' in lot_file or 
                                         '_logo' in lot_file.lower() or
                                         (lot_file.startswith('4655_') and '$$' in lot_file))
                        
                        if is_likely_logo:
                            self._append_log(f"[art] Station logo detected: {lot_file}")
                            self._handle_station_logo(lot_file)
                        else:
                            # Regular album art - check if it matches XHDR pattern or is generic album art
                            # Files like "7269_SD0037672425_1728995.jpg" are album art
                            self._append_log(f"[art] Album art detected in HD Radio stream (LOT): {lot_file}")
                            
                            # Always try to load album art if we have song metadata
                            if self._last_title and self._last_artist and not (
                                self._looks_like_station(self._last_title) or 
                                self._looks_like_station(self._last_artist)):
                                # It's a song, so this LOT art is valid
                                self._has_lot_art = True
                                # Cancel any pending iTunes fetch since we have LOT art
                                self._meta_debounce.stop()
                                # Try to load the file if it exists
                                self._handle_lot_art(lot_file)
                            else:
                                # Could be station art or we're waiting for metadata
                                # Store it temporarily in case metadata comes after the art
                                self._append_log(f"[art] Storing art file for potential use: {lot_file}")
                                self._pending_lot_art = lot_file
                                # Set multiple timers to check for metadata
                                QtCore.QTimer.singleShot(500, lambda: self._check_pending_art())
                                QtCore.QTimer.singleShot(1500, lambda: self._check_pending_art())
                                QtCore.QTimer.singleShot(3000, lambda: self._check_pending_art())
                elif lot_file.lower().endswith('.txt'):
                    # Skip text files - we're focusing on visual maps only
                    if 'TMI_' in lot_file:
                        self._append_log(f"[data] Traffic info text detected (skipping): {lot_file}")
                    elif 'DWRI_' in lot_file:
                        self._append_log(f"[data] Weather info text detected (skipping): {lot_file}")
        # Also check for older format "Writing LOT file 'filename'"
        elif "Writing LOT file" in line:
            lot_match = re.search(r"Writing LOT file ['\"](.*?)['\"]", line)
            if lot_match:
                lot_file = lot_match.group(1).strip()
                if lot_file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                    self._handle_lot_art(lot_file)

        # Station name
        m = self._station_re.search(line)
        if m:
            self._station_name = m.group(1).strip()
            # Only show station name if we don't have a song title
            if not self._last_title or self._looks_like_station(self._last_title):
                self.meta_title.setText(self._station_name)

        # Slogan
        m = self._slogan_re.search(line)
        if m and not self._has_song_meta:
            self.meta_sub.setText(m.group(1).strip())

        # Title
        m = self._title_re.search(line)
        if m:
            t = m.group(1).strip()
            if t and t != self._last_title:
                self._last_title = t
                self._has_lot_art = False  # Reset LOT art flag for new song
                # Always display the title when we get it
                self.meta_title.setText(t)
                # Clear subtitle until we get artist info
                if not self._last_artist:
                    self.meta_sub.setText("")
                
                # Check if this looks like a station ID or non-song content
                if self._looks_like_station(t):
                    # Switch to visualizer for station content
                    self._has_album_art = False
                    self.art_stack.setCurrentWidget(self.visualizer)
                    self._current_art_key = ""  # Reset key to allow new fetches
                else:
                    # It's likely a song, increment counter and check for cleanup
                    self._song_change_count += 1
                    self._append_log(f"[cleanup] Song change detected ({self._song_change_count}/3): {t[:30]}...")
                    if self._song_change_count >= 3:
                        self._song_change_count = 0
                        self._smart_cleanup()
                    # Reset art key to allow new fetch for this song
                    self._current_art_key = ""
                    # Try to fetch art
                    self._meta_debounce.start()

        # Artist
        m = self._artist_re.search(line)
        if m:
            a = m.group(1).strip()
            if a and a != self._last_artist:
                self._last_artist = a
                # Update subtitle with artist
                if self._last_title:
                    self._has_song_meta = True
                    self.meta_sub.setText(a)
                    # Make sure title is still showing the song, not station
                    if self._last_title and not self._looks_like_station(self._last_title):
                        self.meta_title.setText(self._last_title)
                    
                    # Check if this looks like station content
                    if self._looks_like_station(a) or self._looks_like_station(self._last_title):
                        # Switch to visualizer for station content
                        self._has_album_art = False
                        self._has_lot_art = False  # Reset LOT art flag
                        self.art_stack.setCurrentWidget(self.visualizer)
                        self._current_art_key = ""  # Reset key
                    else:
                        # It's a real song, reset art flags for new song
                        self._has_lot_art = False
                        self._current_art_key = ""
                        # Try to fetch art
                        self._meta_debounce.start()
                        
                        # Log song to stats database
                        self._log_song_to_stats()

        # Album (optional)
        m = self._album_re.search(line)
        if m and self._has_song_meta:
            self._last_album = m.group(1).strip()
            artist = self._last_artist or ""
            self.meta_sub.setText(f"{artist} • {self._last_album}" if artist else self._last_album)
    
    def _handle_weather_overlay(self, overlay_file: str):
        """Handle weather radar overlay for the map"""
        def try_load_overlay(attempts=0):
            try:
                overlay_path = LOT_FILES_DIR / overlay_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not overlay_path.exists():
                    # Look for files that end with the overlay_file name
                    matching_files = list(LOT_FILES_DIR.glob(f"*_{overlay_file}"))
                    if matching_files:
                        overlay_path = matching_files[0]
                        self._append_log(f"[map] Found weather overlay with prefix: {overlay_path.name}")
                
                if overlay_path.exists():
                    # Check if file is stable
                    size1 = overlay_path.stat().st_size
                    time.sleep(0.1)
                    if overlay_path.exists():
                        size2 = overlay_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            QtCore.QTimer.singleShot(200, lambda: try_load_overlay(attempts + 1))
                            return
                    
                    # Store the weather overlay file
                    self._weather_overlay_file = str(overlay_path)
                    self._append_log(f"[map] Weather radar overlay received: {overlay_file}")
                    
                    # If we have a traffic map, update it with the overlay
                    if self._combined_map:
                        self._apply_weather_overlay()
                    
                elif attempts < 5:
                    QtCore.QTimer.singleShot(500, lambda: try_load_overlay(attempts + 1))
                else:
                    self._append_log(f"[map] Weather overlay never appeared: {overlay_file}")
            except Exception as e:
                self._append_log(f"[map] Error handling weather overlay: {e}")
        
        try_load_overlay()
    
    def _handle_traffic_tile(self, tile_file: str):
        """Handle traffic map tiles and assemble them"""
        def try_load_tile(attempts=0):
            try:
                tile_path = LOT_FILES_DIR / tile_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not tile_path.exists():
                    # Look for files that end with the tile_file name
                    matching_files = list(LOT_FILES_DIR.glob(f"*_{tile_file}"))
                    if matching_files:
                        tile_path = matching_files[0]
                        self._append_log(f"[map] Found traffic tile with prefix: {tile_path.name}")
                
                if tile_path.exists():
                    # Check if file is stable
                    size1 = tile_path.stat().st_size
                    time.sleep(0.1)
                    if tile_path.exists():
                        size2 = tile_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            QtCore.QTimer.singleShot(200, lambda: try_load_tile(attempts + 1))
                            return
                    
                    # Remove any prefix like ##_ from the filename for parsing
                    clean_name = tile_file
                    if '_TMT_' in tile_file:
                        # Find where TMT starts and use from there
                        tmt_index = tile_file.index('TMT_')
                        clean_name = tile_file[tmt_index:]
                    
                    # Parse tile info from filename: TMT_03g9rc_2_1_20251031_1614_002e.png
                    parts = clean_name.split('_')
                    if len(parts) >= 6:
                        row = int(parts[2])
                        col = int(parts[3])
                        timestamp = f"{parts[4]}_{parts[5]}"
                        
                        # Check if this is a new set of tiles
                        if timestamp != self._last_traffic_timestamp and self._last_traffic_timestamp:
                            # Delete old tiles
                            self._cleanup_old_traffic_tiles(timestamp)
                            self._traffic_tiles.clear()
                        
                        self._last_traffic_timestamp = timestamp
                        
                        # Store this tile with the actual path
                        self._traffic_tiles[(row, col)] = str(tile_path)
                        self._append_log(f"[map] Traffic tile received: Row {row}, Col {col}")
                        
                        # Check if we have all 9 tiles (3x3 grid)
                        if len(self._traffic_tiles) == 9:
                            self._assemble_traffic_map()
                    
                elif attempts < 5:
                    QtCore.QTimer.singleShot(500, lambda: try_load_tile(attempts + 1))
                else:
                    self._append_log(f"[map] Traffic tile never appeared: {tile_file}")
            except Exception as e:
                self._append_log(f"[map] Error handling traffic tile: {e}")
        
        try_load_tile()
    
    def _cleanup_old_traffic_tiles(self, new_timestamp: str):
        """Delete old traffic tiles when new ones arrive"""
        try:
            if not LOT_FILES_DIR.exists():
                return
            
            # Find all TMT files that don't match the new timestamp (including prefixed ones)
            for file in LOT_FILES_DIR.glob("*TMT_*.png"):
                if new_timestamp not in file.name:
                    try:
                        file.unlink()
                        self._append_log(f"[cleanup] Deleted old traffic tile: {file.name}")
                    except:
                        pass
        except Exception as e:
            self._append_log(f"[cleanup] Error removing old traffic tiles: {e}")
    
    def _assemble_traffic_map(self):
        """Assemble the 3x3 traffic map tiles into one image"""
        try:
            # Load all tiles
            tiles = {}
            tile_size = None
            
            for (row, col), path in self._traffic_tiles.items():
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
                self._combined_map = combined
                
                # Apply weather overlay if we have one
                if self._weather_overlay_file:
                    self._apply_weather_overlay()
                else:
                    # Update map if window is open
                    self._update_map_display(combined)
                
                self._append_log(f"[map] Traffic map assembled from 9 tiles")
                
                # Flash the map button to indicate update
                self.btn_open_map.setText("Map •")
                QtCore.QTimer.singleShot(3000, lambda: self.btn_open_map.setText("Map"))
                
        except Exception as e:
            self._append_log(f"[map] Error assembling traffic map: {e}")
    
    def _apply_weather_overlay(self):
        """Apply weather radar overlay on top of traffic map"""
        try:
            if not self._combined_map or not self._weather_overlay_file:
                return
            
            # Load weather overlay
            weather_pm = QtGui.QPixmap(self._weather_overlay_file)
            if weather_pm.isNull():
                return
            
            # Create a copy of the traffic map to overlay on
            final_map = QtGui.QPixmap(self._combined_map)
            
            # Scale weather overlay to match traffic map size
            scaled_weather = weather_pm.scaled(
                final_map.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )
            
            # Paint weather overlay on top with transparency
            painter = QtGui.QPainter(final_map)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            painter.setOpacity(0.6)  # 60% opacity for weather overlay
            
            # Center the weather overlay on the traffic map
            x = (final_map.width() - scaled_weather.width()) // 2
            y = (final_map.height() - scaled_weather.height()) // 2
            painter.drawPixmap(x, y, scaled_weather)
            
            painter.end()
            
            # Update map display
            self._update_map_display(final_map)
            
            self._append_log("[map] Weather radar overlay applied to traffic map")
            
        except Exception as e:
            self._append_log(f"[map] Error applying weather overlay: {e}")
    
    def _handle_station_logo(self, logo_file: str):
        """Handle station logo display as watermark"""
        def try_load_logo(attempts=0):
            try:
                # Try to load the logo file from our hidden directory
                logo_path = LOT_FILES_DIR / logo_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not logo_path.exists():
                    # Look for files that end with the logo_file name
                    matching_files = list(LOT_FILES_DIR.glob(f"*_{logo_file}"))
                    if matching_files:
                        logo_path = matching_files[0]
                        self._append_log(f"[art] Found logo file with prefix: {logo_path.name}")
                
                if logo_path.exists():
                    # Check if file size is stable
                    size1 = logo_path.stat().st_size
                    time.sleep(0.1)
                    if logo_path.exists():
                        size2 = logo_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            # File is still being written, retry
                            QtCore.QTimer.singleShot(200, lambda: try_load_logo(attempts + 1))
                            return
                    
                    pm = QtGui.QPixmap(str(logo_path))
                    if not pm.isNull():
                        # Scale the logo to fit while maintaining aspect ratio (smaller for watermark)
                        scaled_pm = pm.scaled(40, 40, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                        self.station_logo.setPixmap(scaled_pm)
                        self.station_logo.show()
                        self.station_logo.raise_()  # Ensure it's on top
                        self._station_logo_file = logo_file
                        self._append_log(f"[art] Station logo watermark displayed: {logo_file}")
                    else:
                        self._append_log(f"[art] Logo file exists but couldn't load as image: {logo_file}")
                elif attempts < 5:
                    # File doesn't exist yet, retry
                    self._append_log(f"[art] Waiting for logo file: {logo_file} (attempt {attempts + 1})")
                    QtCore.QTimer.singleShot(500, lambda: try_load_logo(attempts + 1))
                else:
                    self._append_log(f"[art] Logo file never appeared: {logo_file}")
            except Exception as e:
                self._append_log(f"[art] Error handling logo file {logo_file}: {e}")
        
        # Only update if it's a different logo
        if logo_file != self._station_logo_file:
            try_load_logo()
    
    def _handle_lot_art(self, lot_file: str):
        """Handle album art from LOT (NRSC-5 HD Radio)"""
        def try_load_art(attempts=0):
            try:
                # Try to load the LOT file from our hidden directory
                lot_path = LOT_FILES_DIR / lot_file
                
                # If the exact file doesn't exist, try to find it with a prefix
                if not lot_path.exists():
                    # Look for files that end with the lot_file name
                    # (nrsc5 adds a prefix like "7276_" to the filename)
                    matching_files = list(LOT_FILES_DIR.glob(f"*_{lot_file}"))
                    if matching_files:
                        lot_path = matching_files[0]  # Use the first match
                        self._append_log(f"[art] Found LOT file with prefix: {lot_path.name}")
                
                if lot_path.exists():
                    # Check if file size is stable (not still being written)
                    size1 = lot_path.stat().st_size
                    time.sleep(0.1)  # Small delay
                    if lot_path.exists():
                        size2 = lot_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            # File is still being written, retry
                            QtCore.QTimer.singleShot(200, lambda: try_load_art(attempts + 1))
                            return
                    
                    pm = QtGui.QPixmap(str(lot_path))
                    if not pm.isNull():
                        self._append_log(f"[art] Album art loaded from LOT file: {lot_file}")
                        self._has_album_art = True
                        self._current_art_key = f"LOT||{lot_file}"
                        # Switch to album art immediately
                        self.art.setPixmap(pm.scaled(self.art.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                        self.art_stack.setCurrentWidget(self.art)
                    else:
                        self._append_log(f"[art] LOT file exists but couldn't load as image: {lot_file}")
                elif attempts < 5:
                    # File doesn't exist yet, retry after a short delay
                    self._append_log(f"[art] Waiting for LOT file to be written: {lot_file} (attempt {attempts + 1})")
                    QtCore.QTimer.singleShot(500, lambda: try_load_art(attempts + 1))
                else:
                    self._append_log(f"[art] LOT file never appeared: {lot_file}")
            except Exception as e:
                self._append_log(f"[art] Error handling LOT file {lot_file}: {e}")
        
        # Start trying to load the art
        try_load_art()
    
    def _load_existing_map_data(self):
        """Load existing traffic tiles and weather overlay"""
        try:
            if not LOT_FILES_DIR.exists():
                return
            
            # Load traffic tiles
            self._load_existing_traffic_tiles()
            
            # Load most recent weather overlay
            weather_files = list(LOT_FILES_DIR.glob("*DWRO_*.png"))
            if weather_files:
                # Sort by modification time, get most recent
                weather_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                self._weather_overlay_file = str(weather_files[0])
                self._append_log(f"[map] Loaded existing weather overlay: {weather_files[0].name}")
                
                # Apply overlay if we have a traffic map
                if self._combined_map:
                    self._apply_weather_overlay()
                    
        except Exception as e:
            self._append_log(f"[map] Error loading existing map data: {e}")
    

    # ----- heuristics + art fetch -----
    @staticmethod
    def _looks_like_station(text: str) -> bool:
        if not text: return False
        t = text.lower()
        # More specific patterns for actual station IDs and promos
        bad_phrases = ["commercial", "advertisement", "promo", "jingle", "weather", "traffic",
                      "coming up", "you're listening", "stay tuned", "call us", "text us", "win", 
                      "contest", "hd1", "hd2", "station id", "station identification", "#1"]
        
        # Check for exact phrase matches
        for phrase in bad_phrases:
            if phrase in t:
                return True
        
        # Check if it's JUST a station name/slogan (no actual song info)
        station_only_patterns = [
            r"^(kiss|rock|country|hits|classic|news|talk)\s*(fm|am)?$",  # Just "KISS FM" etc
            r"^\d{2,3}\.\d\s*(fm|am)?$",  # Just frequency like "103.7"
            r"chattanooga'?s?\s+(rock|country|hits|classic)\s+station",  # Station slogans
            r"^(rock|kiss|country|hits|classic)\s+\d{2,3}\.\d$"  # "Rock 103.7" pattern
        ]
        
        for pattern in station_only_patterns:
            if re.search(pattern, t):
                return True
        
        # Don't filter out legitimate short artist names (removed the very short check)
        return False

    def _maybe_fetch_art(self):
        # Don't fetch from iTunes if we already have LOT art from HD Radio
        if self._has_lot_art:
            self._append_log("[art] Skipping iTunes fetch - LOT art already available from HD Radio")
            return
            
        # Decide whether we're in "song" or "station" mode
        has_song = bool(self._last_title) and bool(self._last_artist)
        song_title = (self._last_title or "").strip()
        song_artist = (self._last_artist or "").strip()
        st = (self._station_name or "").strip()

        if has_song and not (self._looks_like_station(song_title) or self._looks_like_station(song_artist)):
            # Track mode
            key = f"TRACK||{song_artist}||{song_title}"
            self._fetch_art_async(key, song_artist, song_title, station=None)
        else:
            # Station mode
            label = st if st else f"{self._mhz():.1f} MHz"
            key = f"STATION||{label}"
            self.meta_title.setText(label)
            if not st:
                self.meta_sub.setText("SDR-Boombox")
            self._fetch_art_async(key, artist=None, title=None, station=label)

    def _fetch_art_async(self, key: str, artist: str | None, title: str | None, station: str | None):
        # avoid duplicate/loop flicker
        if key == self._current_art_key:
            return
        self._current_art_key = key

        def job():
            pm = QtGui.QPixmap()
            found_art = False
            
            # Try to fetch track art via iTunes public API when we have artist+title.
            if artist and title:
                try:
                    self._append_log(f"[art] Fetching album art from iTunes API for: {artist} - {title}")
                    q = quote_plus(f"{artist} {title}")
                    req = Request(f"https://itunes.apple.com/search?term={q}&entity=song&limit=1",
                                  headers={"User-Agent": "SDR-Boombox"})
                    with urlopen(req, timeout=5) as r:
                        data = r.read().decode("utf-8", "ignore")
                    # very light parse to find artworkUrl100
                    m = re.search(r'"artworkUrl100"\s*:\s*"([^"]+)"', data)
                    if m:
                        url = m.group(1).replace("100x100bb.jpg", "300x300bb.jpg")
                        with urlopen(Request(url, headers={"User-Agent": "SDR-Boombox"}), timeout=5) as r2:
                            raw = r2.read()
                        pm.loadFromData(raw)
                        found_art = not pm.isNull()
                        if found_art:
                            self._append_log(f"[art] Album art retrieved from iTunes API successfully")
                        else:
                            self._append_log(f"[art] iTunes API returned invalid image data")
                    else:
                        self._append_log(f"[art] No album art found in iTunes API for: {artist} - {title}")
                except Exception as e:
                    self._append_log(f"[art] iTunes API fetch failed: {e}")

            # Store whether we found real album art
            self._has_album_art = found_art
            
            # If we have art, emit it; otherwise the visualizer will be shown
            if found_art:
                self.artReady.emit(pm)
            else:
                # Switch to visualizer instead of showing emoji
                QtCore.QMetaObject.invokeMethod(self, "_show_visualizer", QtCore.Qt.QueuedConnection)

        threading.Thread(target=job, daemon=True).start()

    @QtCore.Slot(QtGui.QPixmap)
    def _set_album_art(self, pm: QtGui.QPixmap):
        if pm and not pm.isNull():
            self.art.setPixmap(pm.scaled(self.art.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            # Switch to album art view
            self.art_stack.setCurrentWidget(self.art)
    
    @QtCore.Slot()
    def _show_visualizer(self):
        """Switch to visualizer when no album art is available"""
        # Switch to visualizer view
        self.art_stack.setCurrentWidget(self.visualizer)
    
    def _check_pending_art(self):
        """Check if we now have metadata for pending album art"""
        if self._pending_lot_art and self._last_title and self._last_artist:
            # We now have metadata, check if it's a song
            if not (self._looks_like_station(self._last_title) or 
                    self._looks_like_station(self._last_artist)):
                # It's a song, load the pending art
                self._append_log(f"[art] Loading pending album art now that metadata is available")
                self._has_lot_art = True
                self._meta_debounce.stop()  # Cancel any iTunes fetch
                self._handle_lot_art(self._pending_lot_art)
                self._pending_lot_art = ""  # Clear pending
            else:
                # It's station content, clear pending
                self._pending_lot_art = ""

    def _open_map_window(self):
        """Open or focus the map window"""
        if self.map_window is None or not self.map_window.isVisible():
            # Create new map window
            self.map_window = QtWidgets.QWidget()
            self.map_window.setWindowTitle("Traffic & Weather Map")
            self.map_window.setMinimumSize(600, 600)
            self.map_window.resize(800, 800)
            
            # Create layout
            layout = QtWidgets.QVBoxLayout(self.map_window)
            layout.setContentsMargins(10, 10, 10, 10)
            
            # Create map widget
            self.map_widget = QtWidgets.QLabel()
            self.map_widget.setAlignment(QtCore.Qt.AlignCenter)
            self.map_widget.setStyleSheet("background:#0f0f0f; border:2px solid #333; border-radius:8px;")
            self.map_widget.setScaledContents(True)
            
            # Add to layout
            layout.addWidget(self.map_widget)
            
            # Add refresh button
            refresh_btn = QtWidgets.QPushButton("Refresh")
            refresh_btn.clicked.connect(self._refresh_map)
            layout.addWidget(refresh_btn)
            
            # Show current map if available
            if self._combined_map:
                if self._weather_overlay_file:
                    # Re-apply weather overlay
                    self._apply_weather_overlay()
                else:
                    self.map_widget.setPixmap(self._combined_map)
                    self.map_widget.setText("")
            else:
                self.map_widget.setText("No map data available yet\n\nMaps will appear here when broadcast by the station")
            
            # Show the window
            self.map_window.show()
        else:
            # Window exists, just bring it to front
            self.map_window.raise_()
            self.map_window.activateWindow()
    
    def _update_map_display(self, pixmap):
        """Update the map display in the window if it's open"""
        self._combined_map = pixmap
        self._map_has_data = True
        
        if self.map_window and self.map_window.isVisible() and self.map_widget:
            self.map_widget.setPixmap(pixmap)
            self.map_widget.setText("")
    
    def _refresh_map(self):
        """Refresh the map display"""
        if self._combined_map:
            if self._weather_overlay_file:
                self._apply_weather_overlay()
            else:
                self.map_widget.setPixmap(self._combined_map)
        else:
            self.map_widget.setText("No map data available yet\n\nMaps will appear here when broadcast by the station")
    
    def _prevent_sleep(self, prevent: bool):
        """Prevent or allow system sleep (but allow screen saver)"""
        if sys.platform == "darwin":  # macOS
            if prevent:
                if not self._caffeinate_process:
                    try:
                        # Use caffeinate with -i flag to prevent idle sleep only
                        # This allows screen saver but prevents system sleep
                        self._caffeinate_process = subprocess.Popen(
                            ["caffeinate", "-i"],  # -i prevents idle sleep only
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        self._append_log("[system] Sleep prevention enabled (screen saver allowed)")
                    except Exception as e:
                        self._append_log(f"[system] Could not prevent sleep: {e}")
            else:
                if self._caffeinate_process:
                    try:
                        self._caffeinate_process.terminate()
                        self._caffeinate_process.wait(timeout=1)
                    except:
                        try:
                            self._caffeinate_process.kill()
                        except:
                            pass
                    self._caffeinate_process = None
                    self._append_log("[system] Sleep prevention disabled")
        elif sys.platform == "win32":  # Windows
            import ctypes
            if prevent:
                # Prevent sleep on Windows (but allow screen saver)
                # ES_CONTINUOUS | ES_SYSTEM_REQUIRED (no ES_DISPLAY_REQUIRED)
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED only
                self._append_log("[system] Sleep prevention enabled (screen saver allowed)")
            else:
                # Allow sleep on Windows
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS
                self._append_log("[system] Sleep prevention disabled")
        elif sys.platform.startswith("linux"):  # Linux
            if prevent:
                try:
                    # Try using systemd-inhibit - only inhibit sleep, not idle (allows screen saver)
                    self._caffeinate_process = subprocess.Popen(
                        ["systemd-inhibit", "--what=sleep", "--who=SDR-Boombox", 
                         "--why=Playing radio", "sleep", "infinity"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    self._append_log("[system] Sleep prevention enabled (screen saver allowed)")
                except Exception:
                    self._append_log("[system] Could not prevent sleep (systemd-inhibit not available)")
            else:
                if self._caffeinate_process:
                    try:
                        self._caffeinate_process.terminate()
                        self._caffeinate_process.wait(timeout=1)
                    except:
                        try:
                            self._caffeinate_process.kill()
                        except:
                            pass
                    self._caffeinate_process = None
                    self._append_log("[system] Sleep prevention disabled")
    
    # ----- lifecycle -----
    def _load_existing_traffic_tiles(self):
        """Load existing traffic map tiles from the LOT directory"""
        try:
            if not LOT_FILES_DIR.exists():
                return
            
            # Find all TMT tiles (including those with prefixes like ##_TMT_)
            tiles = list(LOT_FILES_DIR.glob("*TMT_*.png"))
            if not tiles:
                return
            
            # Group by timestamp
            tile_groups = {}
            for tile in tiles:
                # Clean the filename to remove prefix
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
                    self._traffic_tiles.clear()
                    self._last_traffic_timestamp = timestamp
                    
                    for tile in tile_groups[timestamp]:
                        # Clean the filename to remove prefix
                        clean_name = tile.name
                        if '_TMT_' in tile.name:
                            tmt_index = tile.name.index('TMT_')
                            clean_name = tile.name[tmt_index:]
                        
                        parts = clean_name.split('_')
                        row = int(parts[2])
                        col = int(parts[3])
                        self._traffic_tiles[(row, col)] = str(tile)
                    
                    self._assemble_traffic_map()
                    self._append_log(f"[map] Loaded existing traffic map from {timestamp}")
                    break
                    
        except Exception as e:
            self._append_log(f"[map] Error loading existing traffic tiles: {e}")
    
    def _load_existing_data_files(self):
        """Load existing weather/traffic data files from the LOT directory"""
        try:
            if not LOT_FILES_DIR.exists():
                return
            
            # Look for recent weather/traffic files (from the last 24 hours)
            import datetime
            cutoff_time = time.time() - (24 * 60 * 60)  # 24 hours ago
            
            # Find all relevant TEXT data files only (no maps)
            data_files = []
            for pattern in ['*TMI*.txt', '*DWRI*.txt']:
                data_files.extend(LOT_FILES_DIR.glob(pattern))
            
            # Sort by modification time (newest first)
            data_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            
            # Load the most recent files of each type
            loaded_types = set()
            for file_path in data_files[:10]:  # Load up to 10 most recent files
                # Skip old files
                if file_path.stat().st_mtime < cutoff_time:
                    continue
                
                filename = file_path.name
                data_type = None
                
                if 'TMI_' in filename:
                    data_type = "Traffic Info"
                elif 'DWRI_' in filename:
                    data_type = "Weather Info"
                
                # Only load one of each type
                if data_type and data_type not in loaded_types:
                    loaded_types.add(data_type)
                    self._append_log(f"[data] Loading existing {data_type} from: {filename}")
                    
                    # Clear placeholder on first file
                    if len(loaded_types) == 1:
                        self.data_services.clear()
                    
                    # Read and display the file
                    self._display_existing_data_file(filename, data_type)
            
            if loaded_types:
                self._append_log(f"[data] Loaded {len(loaded_types)} existing data file(s)")
        except Exception as e:
            self._append_log(f"[data] Error loading existing files: {e}")
    
    def _display_existing_data_file(self, filename: str, data_type: str):
        """Display an existing data file that's already on disk"""
        try:
            file_path = LOT_FILES_DIR / filename
            
            if file_path.exists():
                # Read the file content
                try:
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                except:
                    content = file_path.read_bytes().decode('utf-8', errors='ignore')
                
                content = content.strip()
                if content:
                    # Update the Data Services tab with actual content
                    timestamp = time.strftime("%H:%M:%S")
                    
                    # Add separator if there's existing content
                    if self.data_services.toPlainText().strip():
                        self.data_services.append("\n" + "="*60 + "\n")
                    
                    # Add header with timestamp and type
                    self.data_services.append(f"[{timestamp}] {data_type} (from cache)")
                    self.data_services.append("-" * 40)
                    
                    # Parse and format content based on type
                    if "Traffic" in data_type and 'TMI_' in filename:
                        # Parse traffic map protocol data
                        self.data_services.append("TRAFFIC MAP CONFIGURATION:")
                        self.data_services.append("")
                        lines = content.split('\n')
                        for line in lines:
                            if '=' in line:
                                key, value = line.split('=', 1)
                                key = key.strip()
                                value = value.strip('"').replace('";"', ', ')
                                
                                if key == 'StationList':
                                    # Parse station list
                                    stations = []
                                    for s in value.split(','):
                                        if 'FM' in s:
                                            stations.append(s.strip('()'))
                                    self.data_services.append(f"  Broadcasting Stations: {', '.join(stations)}")
                                    self.data_services.append(f"    (These FM stations are transmitting this traffic data)")
                                elif key == 'NumRows':
                                    self.data_services.append(f"\n  Map Grid Size: {value} rows")
                                elif key == 'NumColumns':
                                    self.data_services.append(f"                 {value} columns")
                                elif key == 'NumTransmittedTiles':
                                    self.data_services.append(f"                 {value} total tiles")
                                    self.data_services.append(f"    (The traffic map is divided into a {value}-tile grid)")
                                elif 'CoordinatesRow' in key:
                                    if key == 'CoordinatesRow1':
                                        self.data_services.append(f"\n  Geographic Coverage (GPS Coordinates):")
                                    # Parse coordinates
                                    coords = value.replace('(', '').replace(')', '').split(',')
                                    if len(coords) >= 2:
                                        lat, lon = coords[0], coords[1]
                                        self.data_services.append(f"    {key}: Latitude {lat}, Longitude {lon}")
                                elif key == 'CopyrightNotice':
                                    self.data_services.append(f"\n  {value}")
                    elif "Weather" in data_type and 'DWRI_' in filename:
                        # Parse weather radar protocol data
                        self.data_services.append("WEATHER RADAR CONFIGURATION:")
                        self.data_services.append("")
                        self.data_services.append("WHAT THIS DATA MEANS:")
                        self.data_services.append("  - Coverage Area: GPS boundaries of the weather radar map")
                        self.data_services.append("  - Color Legends: RGB color codes for precipitation intensity")
                        self.data_services.append("    (Lower numbers = lighter precipitation, Higher = heavier)")
                        self.data_services.append("")
                    else:
                        # Generic data - just indent it
                        lines = content.split('\n')
                        for line in lines:
                            if line.strip():
                                self.data_services.append(f"  {line.strip()}")
                    
                    # Auto-scroll to bottom
                    cursor = self.data_services.textCursor()
                    cursor.movePosition(QtGui.QTextCursor.End)
                    self.data_services.setTextCursor(cursor)
        except Exception as e:
            self._append_log(f"[data] Error displaying existing file: {e}")
    
    def _cleanup_lot_files(self, keep_count: int = 100):
        """Clean up old LOT files, keeping only the most recent ones"""
        try:
            if not LOT_FILES_DIR.exists():
                return
            
            # Get all files in the LOT directory
            files = list(LOT_FILES_DIR.glob("*"))
            
            # Sort by modification time (oldest first)
            files.sort(key=lambda f: f.stat().st_mtime)
            
            # If we have more than keep_count files, delete the oldest
            if len(files) > keep_count:
                removed_count = 0
                for f in files[:-keep_count]:
                    try:
                        f.unlink()
                        removed_count += 1
                    except:
                        pass
                if removed_count > 0:
                    self._append_log(f"[cleanup] Removed {removed_count} old LOT files")
        except Exception:
            pass  # Silent cleanup
    
    def _smart_cleanup(self):
        """Smart cleanup that runs after every 3rd song change"""
        self._append_log("[cleanup] ========== SMART CLEANUP TRIGGERED (3 songs played) ==========")
        self._periodic_cleanup()  # Use the same cleanup logic
    
    def _periodic_cleanup(self):
        """Periodic cleanup logic used by smart cleanup - more aggressive with map data"""
        try:
            if not LOT_FILES_DIR.exists():
                self._append_log("[cleanup] LOT directory doesn't exist, skipping cleanup")
                return
            
            # Get current time
            current_time = time.time()
            
            # Count files before cleanup
            all_files = list(LOT_FILES_DIR.glob("*"))
            initial_count = len(all_files)
            self._append_log(f"[cleanup] Starting cleanup - {initial_count} files in LOT directory")
            
            removed_count = 0
            removed_files = []
            preserved_files = set()
            
            # Identify files to preserve (only the most recent album art for current songs)
            # Keep only the last 3 album art files
            album_art_files = []
            for file in all_files:
                fname = file.name
                # Skip map-related files
                if any(x in fname for x in ['TMT_', 'TMI_', 'DWRO_', 'DWRI_']):
                    continue
                # Skip station logos
                if '$$' in fname or 'SLWRXR' in fname:
                    continue
                # This is likely album art
                if fname.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                    album_art_files.append(file)
            
            # Sort album art by modification time and keep only the 3 most recent
            album_art_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            for art_file in album_art_files[:3]:
                preserved_files.add(art_file.name)
                self._append_log(f"[cleanup] Preserving recent album art: {art_file.name}")
            
            # Preserve current station logo if set
            if self._station_logo_file:
                preserved_files.add(self._station_logo_file)
                self._append_log(f"[cleanup] Preserving station logo: {self._station_logo_file}")
            
            # Clean up ALL map data files (traffic tiles, weather overlays, info files)
            map_patterns = ['*TMT_*.png', '*TMI_*.txt', '*DWRO_*.png', '*DWRI_*.txt']
            map_removed = 0
            
            for pattern in map_patterns:
                for file in LOT_FILES_DIR.glob(pattern):
                    try:
                        file.unlink()
                        map_removed += 1
                        removed_files.append(f"{file.name} (map data)")
                    except Exception as e:
                        self._append_log(f"[cleanup] Error removing map file {file.name}: {e}")
            
            if map_removed > 0:
                self._append_log(f"[cleanup] Removed {map_removed} map data files")
            
            # Clear map-related state variables
            self._traffic_tiles.clear()
            self._last_traffic_timestamp = ""
            self._weather_overlay_file = ""
            self._combined_map = None
            self._map_has_data = False
            
            # Remove old album art files (keep only the 3 most recent)
            for art_file in album_art_files[3:]:
                if art_file.name not in preserved_files:
                    try:
                        art_file.unlink()
                        removed_count += 1
                        removed_files.append(f"{art_file.name} (old album art)")
                    except Exception as e:
                        self._append_log(f"[cleanup] Error removing {art_file.name}: {e}")
            
            # Final count
            final_count = len(list(LOT_FILES_DIR.glob("*")))
            total_removed = removed_count + map_removed
            
            if total_removed > 0:
                self._append_log(f"[cleanup] Total files removed: {total_removed}")
                self._append_log(f"[cleanup] Files remaining: {final_count} (was {initial_count})")
            else:
                self._append_log(f"[cleanup] No files removed, {final_count} files in directory")
                
        except Exception as e:
            self._append_log(f"[cleanup] Error during periodic cleanup: {e}")
    
    def _log_song_to_stats(self):
        """Log current song to statistics database"""
        if not STATS_ENABLED:
            return
        
        # Only log if we have valid song metadata
        if not self._last_title or not self._last_artist:
            return
        
        # Don't log station content
        if self._looks_like_station(self._last_title) or self._looks_like_station(self._last_artist):
            return
        
        # Create unique key for this song play
        song_key = f"{self._last_artist}|{self._last_title}|{self._station_name}|{self._mhz()}"
        
        # Don't log the same song twice in a row (avoid duplicates from metadata updates)
        if song_key == self._last_logged_song:
            return
        
        self._last_logged_song = song_key
        
        try:
            # Create stats database instance
            stats_db = StatsDatabase()
            
            # Add song to database
            stats_db.add_song(
                title=self._last_title,
                artist=self._last_artist,
                station=self._station_name or f"{self._mhz():.1f} MHz",
                frequency=self._mhz(),
                album=self._last_album,
                hd_channel=self.cfg.hd_program
            )
            
            self._append_log(f"[stats] Logged song: {self._last_artist} - {self._last_title}")
        except Exception as e:
            self._append_log(f"[stats] Error logging song: {e}")
    
    
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            # Close map window if open
            if self.map_window and self.map_window.isVisible():
                self.map_window.close()
            self._prevent_sleep(False)  # Re-enable sleep
            self._fallback_timer.stop()
            QtCore.QMetaObject.invokeMethod(self.worker, "stop")
            if hasattr(self, 'thread') and self.thread.isRunning():
                self.thread.quit(); self.thread.wait(1500)
            self._tray.hide()
        except Exception:
            pass
        super().closeEvent(event)

def main():
    # Check for --stats flag
    if "--stats" in sys.argv:
        # Import and run stats viewer
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
