
"""
===============================================================
   SDR-Boombox
   HD Radio (NRSC-5) + Analog FM Receiver & Visual Interface
===============================================================

Author:     @sjhilt
Project:    SDR-Boombox (Software Defined Radio Tuner)
License:    MIT License
Website:    https://github.com/sjhilt/SDR-Boombox
Version:    1.0.2
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

from PySide6 import QtCore, QtGui, QtWidgets
import random
import socket

APP_NAME = "SDR-Boombox"
FALLBACK_TIMEOUT_S = 6.0
PRESETS_PATH = Path.home() / ".sdr_boombox_presets.json"
SETTINGS_PATH = Path.home() / ".sdr_boombox_settings.json"

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
                        QtCore.Qt.AlignCenter, "♪ SPECTRUM ANALYZER ♪")
        
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
    gain: float | None = 28.0
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
        cmd = ["nrsc5"]
        if self.cfg.gain is not None: cmd += ["-g", str(self.cfg.gain)]
        if self.cfg.device_index is not None: cmd += ["-d", str(self.cfg.device_index)]
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
        self.cfg = Cfg(mhz=default_freq, gain=28.0, ppm=5)

        # LCD
        self.lcd = QtWidgets.QLabel("—.— MHz  ▶/⏸", objectName="lcd")
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
        self.btn_play = QtWidgets.QPushButton("▶ Play")
        self.btn_stop = QtWidgets.QPushButton("■ Stop")
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
        self.btn_toggle_log = QtWidgets.QPushButton("Log")
        self.btn_toggle_log.setCheckable(True)
        self.btn_toggle_log.setMaximumWidth(80)
        self.btn_toggle_log.clicked.connect(self._toggle_log_view)
        
        hd_row.addWidget(hd_label)
        hd_row.addWidget(self.hd_selector)
        hd_row.addStretch()
        hd_row.addWidget(self.btn_toggle_log)
        left.addLayout(hd_row)

        # log (initially hidden based on saved preference)
        self.log = QtWidgets.QTextEdit(readOnly=True)
        self.log.setFixedHeight(230)
        left.addWidget(self.log, 1)

        grid.addLayout(left, 1, 0)

        # right: art + metadata
        right = QtWidgets.QVBoxLayout()
        
        # Create a stacked widget to switch between album art and visualizer
        self.art_stack = QtWidgets.QStackedWidget()
        self.art_stack.setFixedSize(260, 260)
        
        # Album art label
        self.art = QtWidgets.QLabel(objectName="art")
        self.art.setFixedSize(260, 260)
        self.art.setAlignment(QtCore.Qt.AlignCenter)
        self.art.setPixmap(emoji_pixmap("📻", 220))
        
        # Visualizer widget
        self.visualizer = VisualizerWidget()
        
        # Add both to the stack
        self.art_stack.addWidget(self.art)
        self.art_stack.addWidget(self.visualizer)
        
        right.addWidget(self.art_stack)

        self.meta_card = QtWidgets.QFrame(objectName="metaCard")
        meta_layout = QtWidgets.QVBoxLayout(self.meta_card); meta_layout.setContentsMargins(12,10,12,10)
        self.meta_title = QtWidgets.QLabel(" ", objectName="metaTitle"); self.meta_title.setWordWrap(True)
        self.meta_sub   = QtWidgets.QLabel(" ", objectName="metaSubtitle"); self.meta_sub.setWordWrap(True)
        meta_layout.addWidget(self.meta_title); meta_layout.addWidget(self.meta_sub)
        right.addWidget(self.meta_card)
        right.addStretch(1)
        grid.addLayout(right, 1, 1)

        # tray 📻
        self._tray = QtWidgets.QSystemTrayIcon(self)
        self._tray.setIcon(QtGui.QIcon(emoji_pixmap("📻", 256)))
        self._tray.setToolTip(APP_NAME)
        tray_menu = QtWidgets.QMenu()
        act_show = tray_menu.addAction("Show"); act_hide = tray_menu.addAction("Hide")
        tray_menu.addSeparator()
        
        # Streaming menu
        stream_menu = tray_menu.addMenu("Stream to Speaker")
        self.act_scan_speakers = stream_menu.addAction("Scan for Speakers")
        self.act_scan_speakers.triggered.connect(self._scan_for_speakers)
        stream_menu.addSeparator()
        
        # Sonos submenu
        self.sonos_menu = stream_menu.addMenu("Sonos Speakers")
        self.sonos_menu.addAction("No Sonos speakers found").setEnabled(False)
        
        # Google/Chromecast submenu
        self.chromecast_menu = stream_menu.addMenu("Google/Chromecast")
        self.chromecast_menu.addAction("No Chromecast devices found").setEnabled(False)
        
        stream_menu.addSeparator()
        self.act_stop_streaming = stream_menu.addAction("⏹ Stop Streaming")
        self.act_stop_streaming.triggered.connect(self._stop_streaming)
        self.act_stop_streaming.setEnabled(False)
        
        tray_menu.addSeparator()
        act_quit = tray_menu.addAction("Quit")
        act_show.triggered.connect(self.showNormal); act_hide.triggered.connect(self.hide)
        act_quit.triggered.connect(QtWidgets.QApplication.instance().quit)
        self._tray.setContextMenu(tray_menu); self._tray.show()
        
        # Streaming state
        self._streaming_to = None  # Track what we're streaming to
        self._sonos_devices = []
        self._chromecast_devices = []

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
        self._current_art_key = ""   # to avoid flicker
        self._has_album_art = False  # Track if we have real album art
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
        show_log = self.settings.get("show_log", True)
        self.log.setVisible(show_log)
        self.btn_toggle_log.setChecked(show_log)
        
        # Adjust window size based on log visibility
        if not show_log:
            # Make window smaller when log is hidden
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
        """Toggle the visibility of the log view"""
        show_log = self.btn_toggle_log.isChecked()
        self.log.setVisible(show_log)
        
        # Save preference
        self.settings["show_log"] = show_log
        self._save_settings()
        
        # Adjust window minimum size
        if show_log:
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
        self.lcd.setText(f"{self._mhz():.1f} MHz{hd_text}  ▶/⏸")
    
    def _on_hd_program_changed(self, index: int):
        """Handle HD program selection change"""
        self.cfg.hd_program = index
        self._update_lcd()
        # If currently playing HD, restart with new program
        if hasattr(self, 'worker') and self.worker._mode == "hd":
            self._append_log(f"[hd] Switching to HD{index + 1}")
            self._play_clicked()

    def _append_log(self, s: str):
        # Only append to log if it exists and is visible
        if hasattr(self, 'log'):
            self.log.append(s)
            # If log is hidden but we're getting important messages, show indicator
            if not self.log.isVisible() and any(keyword in s.lower() for keyword in ["error", "warning", "fail"]):
                # Update button text to show there's something important
                self.btn_toggle_log.setText(" Log ⚠")

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
        self.lcd.setText(self.lcd.text().replace("⏸", "▶", 1))
        # Start visualizer animation
        self.visualizer.set_playing(True)

    def _on_stopped(self, rc: int, mode: str):
        self._append_log(f"[audio] stopped rc={rc} ({mode})")
        self.lcd.setText(self.lcd.text().replace("▶", "⏸", 1))
        self.btn_play.setEnabled(True)
        # Stop visualizer animation
        self.visualizer.set_playing(False)

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

        # Station name
        m = self._station_re.search(line)
        if m:
            self._station_name = m.group(1).strip()
            if not self._has_song_meta:
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
                self._has_song_meta = False
                self.meta_title.setText(t)
                self.meta_sub.setText("")
                self._meta_debounce.start()

        # Artist
        m = self._artist_re.search(line)
        if m:
            a = m.group(1).strip()
            if a and a != self._last_artist:
                self._last_artist = a
                # if we already have a title, it's a real song tuple
                if self._last_title:
                    self._has_song_meta = True
                    self.meta_sub.setText(a)
                self._meta_debounce.start()

        # Album (optional)
        m = self._album_re.search(line)
        if m and self._has_song_meta:
            self._last_album = m.group(1).strip()
            artist = self._last_artist or ""
            self.meta_sub.setText(f"{artist} • {self._last_album}" if artist else self._last_album)

    # ----- heuristics + art fetch -----
    @staticmethod
    def _looks_like_station(text: str) -> bool:
        if not text: return False
        t = text.lower()
        bad = ["fm", "am", "radio", "station", "kiss", "rock", "country", "hits", "classic", "news", "talk", "hd1", "hd2"]
        # loose heuristic: if contains obvious station-y words or a frequency pattern
        if any(w in t for w in bad): return True
        if re.search(r"\b\d{2,3}\.\d\b", t): return True
        return False

    def _maybe_fetch_art(self):
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
                except Exception:
                    pass

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

    # ----- streaming methods -----
    def _scan_for_speakers(self):
        """Scan for available Sonos and Chromecast devices"""
        self._append_log("[stream] Scanning for speakers...")
        
        # Clear existing menus
        self.sonos_menu.clear()
        self.chromecast_menu.clear()
        self._sonos_devices = []
        self._chromecast_devices = []
        
        # Scan for Sonos speakers
        try:
            import soco
            discovered = soco.discover(timeout=3)
            if discovered:
                for device in discovered:
                    self._sonos_devices.append(device)
                    action = self.sonos_menu.addAction(f"🔊 {device.player_name}")
                    action.triggered.connect(lambda checked, d=device: self._stream_to_sonos(d))
                self._append_log(f"[stream] Found {len(self._sonos_devices)} Sonos speaker(s)")
            else:
                self.sonos_menu.addAction("No Sonos speakers found").setEnabled(False)
                self._append_log("[stream] No Sonos speakers found")
        except ImportError:
            self.sonos_menu.addAction("Install 'soco' library for Sonos support").setEnabled(False)
            self._append_log("[stream] Sonos support requires 'soco' library: pip install soco")
        except Exception as e:
            self.sonos_menu.addAction("Error scanning for Sonos").setEnabled(False)
            self._append_log(f"[stream] Sonos scan error: {e}")
        
        # Scan for Chromecast devices
        try:
            import pychromecast
            services, browser = pychromecast.discovery.discover_chromecasts(timeout=3)
            pychromecast.discovery.stop_discovery(browser)
            
            if services:
                for service in services:
                    # Get the actual Chromecast device
                    cast = pychromecast.get_chromecast_from_service(service, browser.zc)
                    self._chromecast_devices.append(cast)
                    action = self.chromecast_menu.addAction(f"📱 {cast.name}")
                    action.triggered.connect(lambda checked, c=cast: self._stream_to_chromecast(c))
                self._append_log(f"[stream] Found {len(self._chromecast_devices)} Chromecast device(s)")
            else:
                self.chromecast_menu.addAction("No Chromecast devices found").setEnabled(False)
                self._append_log("[stream] No Chromecast devices found")
        except ImportError:
            self.chromecast_menu.addAction("Install 'pychromecast' for Chromecast support").setEnabled(False)
            self._append_log("[stream] Chromecast support requires 'pychromecast' library: pip install pychromecast")
        except Exception as e:
            self.chromecast_menu.addAction("Error scanning for Chromecast").setEnabled(False)
            self._append_log(f"[stream] Chromecast scan error: {e}")
        
        self._append_log("[stream] Speaker scan complete")
    
    def _stream_to_sonos(self, device):
        """Stream audio to a Sonos speaker"""
        try:
            self._append_log(f"[stream] Starting stream to Sonos: {device.player_name}")
            
            # Get the current machine's IP address
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            
            # Create a simple HTTP server to serve the audio stream
            # This is a simplified approach - in production you'd want a more robust solution
            stream_url = f"http://{local_ip}:8080/stream.mp3"
            
            # For now, we'll use the radio station's stream URL if available
            # In a full implementation, you'd capture and re-stream the audio
            if self.worker._mode == "fm":
                # For analog FM, we'd need to set up a local streaming server
                self._append_log("[stream] Direct FM streaming to Sonos not yet implemented")
                self._append_log("[stream] This would require setting up a local audio streaming server")
                return
            else:
                # For HD radio, try to use the station's online stream if available
                # This is a placeholder - you'd need to look up the station's streaming URL
                station_freq = self._mhz()
                self._append_log(f"[stream] Would stream {station_freq} MHz to {device.player_name}")
                self._append_log("[stream] Note: Direct HD radio streaming requires additional implementation")
            
            self._streaming_to = f"Sonos: {device.player_name}"
            self.act_stop_streaming.setEnabled(True)
            
        except Exception as e:
            self._append_log(f"[stream] Error streaming to Sonos: {e}")
    
    def _stream_to_chromecast(self, cast):
        """Stream audio to a Chromecast device"""
        try:
            self._append_log(f"[stream] Starting stream to Chromecast: {cast.name}")
            
            # Wait for cast device to be ready
            cast.wait()
            
            # Get media controller
            mc = cast.media_controller
            
            # Similar to Sonos, we'd need to provide a stream URL
            # This is a simplified example
            if self.worker._mode == "fm":
                self._append_log("[stream] Direct FM streaming to Chromecast not yet implemented")
                self._append_log("[stream] This would require setting up a local audio streaming server")
                return
            else:
                station_freq = self._mhz()
                self._append_log(f"[stream] Would stream {station_freq} MHz to {cast.name}")
                self._append_log("[stream] Note: Direct HD radio streaming requires additional implementation")
            
            self._streaming_to = f"Chromecast: {cast.name}"
            self.act_stop_streaming.setEnabled(True)
            
        except Exception as e:
            self._append_log(f"[stream] Error streaming to Chromecast: {e}")
    
    def _stop_streaming(self):
        """Stop streaming to external speakers"""
        if self._streaming_to:
            self._append_log(f"[stream] Stopping stream to {self._streaming_to}")
            self._streaming_to = None
            self.act_stop_streaming.setEnabled(False)
            # Additional cleanup code would go here
    
    # ----- lifecycle -----
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._stop_streaming()  # Stop any active streaming
            self._fallback_timer.stop()
            QtCore.QMetaObject.invokeMethod(self.worker, "stop")
            if hasattr(self, 'thread') and self.thread.isRunning():
                self.thread.quit(); self.thread.wait(1500)
            self._tray.hide()
        except Exception:
            pass
        super().closeEvent(event)

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = SDRBoombox(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
