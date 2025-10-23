#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================
   SDR-Boombox
   HD Radio (NRSC-5) + Analog FM Receiver & Visual Interface
===============================================================

Author:     @sjhilt
Project:    SDR-Boombox (Software Defined Radio Tuner)
License:    MIT License
Website:    https://github.com/sjhilt/SDR-Boombox
Version:    1.0.0
Python:     3.10+

Description:
------------
SDR-Boombox is a modern GUI-driven radio tuner for Software Defined Radios
such as the RTL-SDR. It attempts HD Radio decoding first using `nrsc5`, and
automatically falls back to analog wideband FM when digital signals are not
available. The interface features live metadata, album art, scanning, presets,
and cross-platform support.

Key Features:
-------------
• HD Radio decoding via nrsc5
• Automatic analog FM fallback (rtl_fm)
• Live metadata display (station, song info, slogans)
• Real-time album art and station icons
• Manual tuning + preset buttons (JSON saved)
• Spectrum scan with automatic station detection
• System tray integration with 📻 icon
• Clean, retro-inspired "boombox" aesthetic

Dependencies:
-------------
• nrsc5              For HD Radio decoding
• rtl_fm, rtl_power  From rtl-sdr (for analog FM + scan)
• ffplay             From FFmpeg (for audio playback)
• Python PySide6     For the graphical interface

===============================================================
"""

import os, sys, re, json, subprocess, threading, time, math, shutil
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

APP_NAME = "SDR-Boombox"
FALLBACK_TIMEOUT_S = 6.0                       # time to wait for HD sync before analog fallback
PRESETS_PATH = Path.home() / ".sdr_boombox_presets.json"

def which(cmd: str) -> str | None:
    p = shutil.which(cmd)
    if p: return p
    # quick manual PATH walk for .exe on Windows
    for d in os.getenv("PATH", "").split(os.pathsep):
        if not d: continue
        cand = Path(d) / (cmd + ".exe")
        if cand.exists(): return str(cand)
    return None

def _emoji_icon_pixmap(emoji: str, size: int = 256) -> QtGui.QPixmap:
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    font_family = "Apple Color Emoji" if sys.platform == "darwin" else "Segoe UI Emoji"
    painter.setFont(QtGui.QFont(font_family, int(size * 0.75)))
    painter.drawText(pm.rect(), QtCore.Qt.AlignCenter, emoji)
    painter.end()
    return pm

@dataclass
class Cfg:
    mhz: float = 99.5
    hd_prog: int = 0            # HD subchannel (0..3)
    gain: float | None = None
    device_index: int | None = None
    volume: float = 1.0
    ppm: int = 0                # PPM correction for analog FM (like you set in GQRX)

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
        base = ["ffplay", "-autoexit", "-nodisp", "-hide_banner", "-loglevel", "error"]
        vol = max(0.01, min(self.cfg.volume, 3.0))
        base += ["-af", f"volume={vol:.2f}"]
        if is_fm:
            base += ["-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "-"]
        else:
            base += ["-i", "-"]
        return base

    def nrsc5_cmd(self) -> list[str]:
        cmd = ["nrsc5"]
        if self.cfg.gain is not None: cmd += ["-g", str(self.cfg.gain)]
        if self.cfg.device_index is not None: cmd += ["-d", str(self.cfg.device_index)]
        cmd += ["-o", "-", f"{self.cfg.mhz}", f"{self.cfg.hd_prog}"]
        return cmd

    def rtl_fm_cmd(self) -> list[str]:
        # Wideband FM with deemphasis, no squelch, resample to 48k for ffplay
        cmd = [
            "rtl_fm",
            "-M", "wbfm",
            "-f", f"{self.cfg.mhz}M",
            "-s", "200k",          # wider than 170k, helps many stations
            "-r", "48k",
            "-E", "deemp=75",      # US deemphasis explicit
            "-l", "0",             # squelch off
            "-g", "0",             # auto-gain
            "-A", "fast",          # faster AFC
        ]
        if self.cfg.ppm:
            cmd += ["-p", str(int(self.cfg.ppm))]
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
                try: p.wait(timeout=1.5)
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

    # ---------- scanning (rtl_power) ----------
    def scan_fm(self, mhz_start=88.0, mhz_end=108.0, step_khz=200) -> list[tuple[float, float]]:
        """
        Returns list of (freq_mhz, power_db) candidates, strongest first.
        Requires rtl_power.
        """
        if not which("rtl_power"):
            raise RuntimeError("rtl_power not found in PATH")

        # rtl_power CSV single sweep
        f_arg = f"{mhz_start}M:{mhz_end}M:{step_khz}k"
        cmd = ["rtl_power", "-f", f_arg, "-g", "0", "-1", "-c", "50%"]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=60)

        # Parse last CSV line (bins)
        # CSV columns end with bin powers after the header columns
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if not lines:
            return []

        last = lines[-1].split(",")
        # rtl_power format: date,time, hz_low, hz_high, hz_step, num_samples, dbm1,dbm2,...
        if len(last) < 7:
            return []

        hz_low = float(last[2]); hz_high = float(last[3]); hz_step = float(last[4])
        powers = [float(x) for x in last[6:]]
        freqs_hz = [hz_low + i * hz_step for i in range(len(powers))]

        # Smooth and peak-pick
        def moving_avg(a, n=5):
            out = []
            for i in range(len(a)):
                lo = max(0, i-n//2)
                hi = min(len(a), i+n//2+1)
                out.append(sum(a[lo:hi]) / (hi-lo))
            return out
        sm = moving_avg(powers, 7)

        # find local maxima above threshold
        threshold = max(sm) - 6.0  # 6 dB below max
        cands = []
        for i in range(1, len(sm)-1):
            if sm[i] > sm[i-1] and sm[i] > sm[i+1] and sm[i] >= threshold:
                mhz = freqs_hz[i] / 1e6
                # snap to standard 200 kHz grid (.1,.3,.5,.7,.9)
                snapped = round(mhz * 5) / 5.0
                cands.append((snapped, sm[i]))

        # unique by freq, keep strongest
        best: dict[float,float] = {}
        for f,p in cands:
            if f not in best or p > best[f]:
                best[f] = p
        out_list = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return out_list

class SDRBoombox(QtWidgets.QMainWindow):
    # --- regex helpers (class-level) ---
    _ts_re      = re.compile(r"^\s*\d{2}:\d{2}:\d{2}\s+")
    _title_re   = re.compile(r"\bTitle:\s*(.+)", re.IGNORECASE)
    _artist_re  = re.compile(r"\bArtist:\s*(.+)", re.IGNORECASE)
    _album_re   = re.compile(r"\bAlbum:\s*(.+)", re.IGNORECASE)
    _slogan_re  = re.compile(r"\bSlogan:\s*(.+)", re.IGNORECASE)
    _station_re = re.compile(r"\bStation name:\s*(.+)", re.IGNORECASE)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDR-Boombox – HD Radio (NRSC-5)")
        self.setMinimumSize(1020, 580)
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

        # --- root layout ---
        root = QtWidgets.QFrame(objectName="root"); self.setCentralWidget(root)
        grid = QtWidgets.QGridLayout(root); grid.setContentsMargins(16,16,16,16); grid.setHorizontalSpacing(14)

        # --- LCD ---
        self.lcd = QtWidgets.QLabel("—.— MHz  ▶/⏸", objectName="lcd")
        f = self.lcd.font(); f.setPointSize(22); self.lcd.setFont(f)
        self.lcd.setAlignment(QtCore.Qt.AlignCenter)
        grid.addWidget(self.lcd, 0, 0, 1, 2)

        # --- left controls ---
        left = QtWidgets.QVBoxLayout()

        # freq slider 88.0..108.0 (×10)
        self.freq_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.freq_slider.setRange(880, 1080)
        self.freq_slider.setValue(995)
        self.freq_slider.valueChanged.connect(self._update_lcd)
        left.addWidget(self.freq_slider)

        # preset row (P0..P3)
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

        # actions row
        row2 = QtWidgets.QHBoxLayout()
        self.btn_play = QtWidgets.QPushButton("▶ Play")
        self.btn_stop = QtWidgets.QPushButton("■ Stop")
        self.chk_fallback = QtWidgets.QCheckBox("Auto analog fallback")
        self.chk_fallback.setChecked(True)
        row2.addWidget(self.btn_play); row2.addWidget(self.btn_stop); row2.addWidget(self.chk_fallback)
        left.addLayout(row2)

        # hd program chooser
        hdrow = QtWidgets.QHBoxLayout()
        hdrow.addWidget(QtWidgets.QLabel("HD Program:"))
        self.hd_combo = QtWidgets.QComboBox(); self.hd_combo.addItems(["0","1","2","3"])
        self.hd_combo.currentIndexChanged.connect(self._hd_prog_changed)
        hdrow.addWidget(self.hd_combo); hdrow.addStretch(1)
        left.addLayout(hdrow)

        # Scan
        self.btn_scan = QtWidgets.QPushButton("🔎 Scan (88–108 MHz)")
        self.btn_scan.clicked.connect(self._scan_click)
        left.addWidget(self.btn_scan)

        # log
        self.log = QtWidgets.QTextEdit(readOnly=True); self.log.setFixedHeight(230)
        left.addWidget(self.log, 1)

        grid.addLayout(left, 1, 0)

        # --- right: art + metadata ---
        right = QtWidgets.QVBoxLayout()
        self.art = QtWidgets.QLabel(objectName="art"); self.art.setFixedSize(260,260)
        self.art.setAlignment(QtCore.Qt.AlignCenter); self.art.setText("No Art")
        right.addWidget(self.art)

        self.meta_card = QtWidgets.QFrame(objectName="metaCard")
        meta_layout = QtWidgets.QVBoxLayout(self.meta_card); meta_layout.setContentsMargins(12,10,12,10)
        self.meta_title = QtWidgets.QLabel(" ", objectName="metaTitle"); self.meta_title.setWordWrap(True)
        self.meta_sub   = QtWidgets.QLabel(" ", objectName="metaSubtitle"); self.meta_sub.setWordWrap(True)
        meta_layout.addWidget(self.meta_title); meta_layout.addWidget(self.meta_sub)
        right.addWidget(self.meta_card)
        right.addStretch(1)
        grid.addLayout(right, 1, 1)

        # --- tray icon 📻 ---
        self._tray = QtWidgets.QSystemTrayIcon(self)
        self._tray.setIcon(QtGui.QIcon(_emoji_icon_pixmap("📻", 256)))
        self._tray.setToolTip("SDR-Boombox")
        tray_menu = QtWidgets.QMenu()
        act_show = tray_menu.addAction("Show"); act_hide = tray_menu.addAction("Hide")
        tray_menu.addSeparator(); act_quit = tray_menu.addAction("Quit")
        act_show.triggered.connect(self.showNormal); act_hide.triggered.connect(self.hide)
        act_quit.triggered.connect(QtWidgets.QApplication.instance().quit)
        self._tray.setContextMenu(tray_menu); self._tray.show()

        # --- state ---
        self.cfg = Cfg()
        self._load_presets()
        self.worker = Worker(self.cfg)
        self.thread = QtCore.QThread(self); self.worker.moveToThread(self.thread); self.thread.start()

        self._hd_synced = False
        self._fallback_timer = QtCore.QTimer(self); self._fallback_timer.setSingleShot(True)
        self._fallback_timer.timeout.connect(self._maybe_fallback_to_fm)

        # metadata state
        self._last_title = None
        self._last_artist = None
        self._has_song_meta = False

        # --- signals ---
        self.btn_play.clicked.connect(self._play_clicked)
        self.btn_stop.clicked.connect(self._stop_clicked)
        self.worker.logLine.connect(self._handle_log_line)
        self.worker.started.connect(self._on_started)
        self.worker.stopped.connect(self._on_stopped)
        self.worker.hdSynced.connect(self._on_hd_synced)

        self._update_lcd()

        # sanity
        if not which("nrsc5"): self._append_log("WARNING: nrsc5 not found in PATH.")
        if not which("ffplay"): self._append_log("WARNING: ffplay not found in PATH.")
        if not which("rtl_fm"): self._append_log("Note: rtl_fm not found; analog FM fallback unavailable.")
        if not which("rtl_power"): self._append_log("Note: rtl_power not found; Scan will be disabled.")

    # ----- presets -----
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
        self.presets[f"P{idx}"] = round(mhz, 1)
        try:
            PRESETS_PATH.write_text(json.dumps(self.presets, indent=2))
        except Exception:
            pass
        self._load_presets()

    def _clear_preset(self, idx: int):
        self.presets.pop(f"P{idx}", None)
        try:
            PRESETS_PATH.write_text(json.dumps(self.presets, indent=2))
        except Exception:
            pass
        self._load_presets()

    def _preset_menu(self, idx: int, pos: QtCore.QPoint):
        b = self.preset_buttons[idx]
        m = QtWidgets.QMenu(b)
        m.addAction(f"Save current ({self._mhz():.1f} MHz) to P{idx}",
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
        self.freq_slider.setValue(int(round(mhz * 10)))
        self._update_lcd()
        # if already playing, retune on click
        if self.btn_play.isEnabled() is False:
            self._play_clicked()

    # ----- UI helpers -----
    def _mhz(self) -> float: return round(self.freq_slider.value() / 10.0, 1)

    def _update_lcd(self):
        self.lcd.setText(f"{self._mhz():.1f} MHz  ▶/⏸")

    def _append_log(self, s: str):
        self.log.append(s)

    # ----- UI slots -----
    def _play_clicked(self):
        self.cfg.mhz = self._mhz()
        self._hd_synced = False
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

    def _hd_prog_changed(self, idx: int):
        self.cfg.hd_prog = idx

    # ----- worker callbacks -----
    def _on_started(self, mode: str):
        self._append_log(f"[audio] started ({mode})")
        self.lcd.setText(self.lcd.text().replace("⏸", "▶", 1))

    def _on_stopped(self, rc: int, mode: str):
        self._append_log(f"[audio] stopped rc={rc} ({mode})")
        self.lcd.setText(self.lcd.text().replace("▶", "⏸", 1))
        self.btn_play.setEnabled(True)

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

        # Station/Slogan only when not showing a current song
        m = self._station_re.search(line)
        if m and not self._has_song_meta:
            self.meta_title.setText(m.group(1).strip())

        m = self._slogan_re.search(line)
        if m and not self._has_song_meta:
            self.meta_sub.setText(m.group(1).strip())

        # Title/Artist de-dup
        m = self._title_re.search(line)
        if m:
            t = m.group(1).strip()
            if t and t != self._last_title:
                self._last_title = t
                self._has_song_meta = False
                self.meta_title.setText(t)
                self.meta_sub.setText("")

        m = self._artist_re.search(line)
        if m:
            a = m.group(1).strip()
            if a and a != self._last_artist:
                self._last_artist = a
                self.meta_sub.setText(a)
                if self._last_title:
                    self._has_song_meta = True

        m = self._album_re.search(line)
        if m and self._has_song_meta:
            al = m.group(1).strip()
            artist = self._last_artist or ""
            self.meta_sub.setText(f"{artist} • {al}" if artist else al)

    # ----- scanning -----
    def _scan_click(self):
        if not which("rtl_power"):
            QtWidgets.QMessageBox.warning(self, "Scan",
                "rtl_power not found in PATH. Install rtl-sdr tools to use Scan.")
            return

        self._append_log("[scan] Running rtl_power sweep 88–108 MHz …")
        self.btn_scan.setEnabled(False)

        def do_scan():
            try:
                stations = self.worker.scan_fm(88.0, 108.0, 200)
            except Exception as e:
                stations = []
                self._append_log(f"[scan] error: {e}")
            QtCore.QMetaObject.invokeMethod(self, "_scan_done",
                                            QtCore.Qt.QueuedConnection,
                                            QtCore.Q_ARG(list, stations))
        threading.Thread(target=do_scan, daemon=True).start()

    @QtCore.Slot(list)
    def _scan_done(self, stations: list):
        self.btn_scan.setEnabled(True)
        if not stations:
            QtWidgets.QMessageBox.information(self, "Scan", "No strong stations detected.")
            return

        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Scan Results")
        v = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel("Click a station to tune. Right-click a preset to save it later.")
        v.addWidget(info)
        listw = QtWidgets.QListWidget()
        for f, p in stations:
            listw.addItem(f"{f:.1f} MHz    ({p:.1f} dB)")
        v.addWidget(listw)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        v.addWidget(btns)
        btns.rejected.connect(dlg.reject)

        def tune_current(item: QtWidgets.QListWidgetItem):
            txt = item.text().split()[0]
            mhz = float(txt)
            self.freq_slider.setValue(int(round(mhz * 10)))
            self._play_clicked()
            dlg.accept()

        listw.itemDoubleClicked.connect(tune_current)
        dlg.resize(360, 420)
        dlg.exec()

    # ----- lifecycle -----
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
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
    # (User asked for tray only earlier; we can set a global icon here too later if desired)
    w = SDRBoombox(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()