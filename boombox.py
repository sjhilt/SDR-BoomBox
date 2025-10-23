#!/usr/bin/env python3
"""
boombox5: a boombox‑style Python GUI that wraps theori‑io/nrsc5

Requirements (install these first):
  • Python 3.9+
  • PySide6  (pip install PySide6)
  • requests  (pip install requests)
  • A working nrsc5 binary in PATH (see https://github.com/theori-io/nrsc5)
  • FFmpeg (for ffplay) in PATH (https://ffmpeg.org)
  • An RTL‑SDR (or use -r file capture) attached

This app launches nrsc5 and streams its WAV audio to ffplay. The GUI
looks like a retro boombox with big play/stop, a tuner dial, program buttons,
VU meters, and a faux LCD. It also shows basic log/metadata lines.

If the station doesn’t provide embedded AAS art, we do a background
lookup for album art using the iTunes Search API based on artist/title.

Notes:
  • Audio is handled by ffplay reading from stdin so we don’t have to parse WAV.
  • We forward stdout from nrsc5 -> stdin of ffplay on a background thread.
  • On Windows, make sure libnrsc5.dll and nrsc5.exe are reachable; see README.
  • On macOS/Linux, ensure permissions for your RTL-SDR and that ffplay exists.
"""

import os
import sys
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

import json
import urllib.parse
import requests

APP_NAME = "boombox5"


# ---------- Utility ----------

def which(cmd: str) -> bool:
    return any(
        (Path(p) / cmd).exists() or (Path(p) / (cmd + ".exe")).exists()
        for p in os.getenv("PATH", "").split(os.pathsep)
    )


@dataclass
class NRSC5Config:
    frequency_mhz: float = 99.5
    program: int = 0  # 0..3
    gain: float | None = None
    device_index: int | None = None


# ---------- Worker that pipes nrsc5 -> ffplay ----------

class RadioWorker(QtCore.QObject):
    started = QtCore.Signal()
    stopped = QtCore.Signal(int)  # return code
    logLine = QtCore.Signal(str)

    def __init__(self, cfg: NRSC5Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._nrsc5: subprocess.Popen | None = None
        self._ffplay: subprocess.Popen | None = None
        self._forward_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

    def build_nrsc5_cmd(self) -> list[str]:
        cmd = ["nrsc5"]
        if self.cfg.gain is not None:
            cmd += ["-g", f"{self.cfg.gain}"]
        if self.cfg.device_index is not None:
            cmd += ["-d", str(self.cfg.device_index)]
        # Output WAV to stdout:
        cmd += ["-o", "-", f"{self.cfg.frequency_mhz}", str(self.cfg.program)]
        return cmd

    def build_ffplay_cmd(self) -> list[str]:
        # Read WAV from stdin, no UI window
        return [
            "ffplay", "-autoexit", "-nodisp", "-hide_banner", "-loglevel", "error", "-i", "-",
        ]

    @QtCore.Slot()
    def start(self):
        self.stop()  # ensure clean
        self._stop_evt.clear()
        try:
            nrsc5_cmd = self.build_nrsc5_cmd()
            ffplay_cmd = self.build_ffplay_cmd()

            # Spawn ffplay first (waiting for stdin)
            self._ffplay = subprocess.Popen(
                ffplay_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Spawn nrsc5 with stdout pipe and stderr for logs
            self._nrsc5 = subprocess.Popen(
                nrsc5_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            # Thread to forward audio
            def forward():
                assert self._nrsc5 and self._ffplay
                src = self._nrsc5.stdout
                dst = self._ffplay.stdin
                if not src or not dst:
                    return
                try:
                    while not self._stop_evt.is_set():
                        chunk = src.read(8192)
                        if not chunk:
                            break
                        dst.write(chunk)
                        dst.flush()
                except Exception:
                    pass

            # Thread to read logs/metadata from stderr
            def read_logs():
                assert self._nrsc5
                err = self._nrsc5.stderr
                if not err:
                    return
                for line in iter(err.readline, b""):
                    if self._stop_evt.is_set():
                        break
                    try:
                        self.logLine.emit(line.decode("utf-8", errors="ignore").rstrip())
                    except Exception:
                        pass

            self._forward_thread = threading.Thread(target=forward, daemon=True, name="audio-forward")
            self._forward_thread.start()
            threading.Thread(target=read_logs, daemon=True, name="stderr-reader").start()

            self.started.emit()
        except FileNotFoundError as e:
            self.logLine.emit(f"Executable not found: {e}")
            self.stop()

    @QtCore.Slot()
    def stop(self):
        self._stop_evt.set()
        # Terminate nrsc5 first so ffplay drains and exits
        if self._nrsc5 and self._nrsc5.poll() is None:
            self._nrsc5.terminate()
            try:
                self._nrsc5.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._nrsc5.kill()
        self._nrsc5 = None
        if self._ffplay and self._ffplay.poll() is None:
            try:
                # Closing stdin signals EOF -> exit
                if self._ffplay.stdin:
                    try:
                        self._ffplay.stdin.close()
                    except Exception:
                        pass
                self._ffplay.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._ffplay.kill()
        rc = self._ffplay.returncode if self._ffplay else 0
        self._ffplay = None
        self.stopped.emit(rc)


# ---------- Main Window (boombox UI) ----------

class BoomboxUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("boombox5 – HD Radio (NRSC‑5)")
        self.setMinimumSize(980, 520)
        self.setStyleSheet(
            """
            QMainWindow { background: #151515; }
            QLabel#lcd { font-family: 'DS-Digital', monospace; color: #7CFC00; background:#0a0a0a; padding: 12px 18px; border-radius: 10px; }
            QFrame#boombox { border: 4px solid #333; border-radius: 24px; background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1e1e1e, stop:1 #111); }
            QPushButton { background: #2a2a2a; color:#eee; border:1px solid #444; border-radius: 10px; padding:10px 14px; }
            QPushButton:hover { background:#333; }
            QPushButton:pressed { background:#222; }
            QSlider::groove:horizontal { height: 10px; background:#333; border-radius:5px; }
            QSlider::handle:horizontal { width: 18px; background:#777; border-radius:9px; margin:-4px 0; }
            QTextEdit { background:#0f0f0f; color:#ccc; border:1px solid #333; }
            QLabel#art { background:#0c0c0c; border:1px solid #2c2c2c; border-radius:12px; }
            """
        )

        central = QtWidgets.QFrame(objectName="boombox")
        self.setCentralWidget(central)
        layout = QtWidgets.QGridLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setHorizontalSpacing(18)

        # Left/Right speakers (visual)
        left_spk = self._speaker()
        right_spk = self._speaker()

        # Center stack: LCD + tuner + controls + logs
        center = QtWidgets.QVBoxLayout()

        # LCD display
        self.lcd = QtWidgets.QLabel("—.—— MHz  P0  ⏸", objectName="lcd")
        f = self.lcd.font()
        f.setPointSize(26)
        self.lcd.setFont(f)
        self.lcd.setAlignment(QtCore.Qt.AlignCenter)
        center.addWidget(self.lcd)

        # Tuner dial (slider)
        freq_row = QtWidgets.QHBoxLayout()
        self.freq_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.freq_slider.setMinimum(880)   # 88.0 MHz
        self.freq_slider.setMaximum(1080)  # 108.0 MHz
        self.freq_slider.setValue(995)     # default 99.5
        self.freq_slider.valueChanged.connect(self._update_lcd)
        freq_row.addWidget(QtWidgets.QLabel("88.0"))
        freq_row.addWidget(self.freq_slider, 1)
        freq_row.addWidget(QtWidgets.QLabel("108.0"))
        center.addLayout(freq_row)

        # Program buttons 0..3
        prog_row = QtWidgets.QHBoxLayout()
        self.prog_group = QtWidgets.QButtonGroup(self)
        for i in range(4):
            b = QtWidgets.QPushButton(f"Program {i}")
            b.setCheckable(True)
            if i == 0:
                b.setChecked(True)
            self.prog_group.addButton(b, i)
            prog_row.addWidget(b)
        center.addLayout(prog_row)

        # Transport + volume
        ctrl_row = QtWidgets.QHBoxLayout()
        self.play_btn = QtWidgets.QPushButton("▶ Play")
        self.stop_btn = QtWidgets.QPushButton("■ Stop")
        self.scan_btn = QtWidgets.QPushButton("🔎 Quick Scan")
        ctrl_row.addWidget(self.play_btn)
        ctrl_row.addWidget(self.stop_btn)
        ctrl_row.addWidget(self.scan_btn)
        ctrl_row.addStretch(1)
        ctrl_row.addWidget(QtWidgets.QLabel("Volume"))
        self.volume = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        ctrl_row.addWidget(self.volume)
        center.addLayout(ctrl_row)

        # Logs / metadata
        self.log = QtWidgets.QTextEdit(readOnly=True)
        self.log.setFixedHeight(140)
        center.addWidget(self.log)

        # Right-side metadata/art panel
        side_panel = QtWidgets.QVBoxLayout()
        self.art = QtWidgets.QLabel(objectName="art")
        self.art.setFixedSize(220, 220)
        self.art.setAlignment(QtCore.Qt.AlignCenter)
        self.art.setText("No Art")
        side_panel.addWidget(self.art)
        self.meta_lbl = QtWidgets.QLabel("Title — Artist\nAlbum")
        self.meta_lbl.setWordWrap(True)
        side_panel.addWidget(self.meta_lbl)
        side_panel.addStretch(1)

        layout.addWidget(left_spk, 0, 0, 3, 1)
        layout.addLayout(center, 0, 1, 3, 1)
        layout.addLayout(side_panel, 0, 2, 3, 1)

        # State
        self.cfg = NRSC5Config()
        self.worker = RadioWorker(self.cfg)
        self.thread = QtCore.QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.start()

        # Album-art fetch timer (debounced)
        self._artist = None
        self._title = None
        self._album = None
        self._art_cache: dict[str, QtGui.QPixmap] = {}
        self._art_timer = QtCore.QTimer(self)
        self._art_timer.setInterval(600)  # debounce
        self._art_timer.setSingleShot(True)
        self._art_timer.timeout.connect(self._fetch_album_art)

        # Wire up
        self.play_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.scan_btn.clicked.connect(self._scan)
        # Important: connect to a slot we implement
        self.prog_group.idToggled.connect(self._prog_changed)
        self.volume.valueChanged.connect(self._set_volume)
        self.worker.logLine.connect(self._handle_log_line)
        self.worker.started.connect(lambda: self._append_log("[audio] started"))
        self.worker.stopped.connect(lambda rc: self._append_log(f"[audio] stopped rc={rc}"))

        self._update_lcd()

        # Basic sanity checks
        if not which("nrsc5"):
            self._append_log("WARNING: nrsc5 was not found in PATH.")
        if not which("ffplay"):
            self._append_log("WARNING: ffplay (FFmpeg) was not found in PATH.")

    # --- UI helpers ---
    def _speaker(self) -> QtWidgets.QFrame:
        f = QtWidgets.QFrame()
        v = QtWidgets.QVBoxLayout(f)
        grill = QtWidgets.QLabel()
        pm = QtGui.QPixmap(300, 300)
        pm.fill(QtGui.QColor("#1b1b1b"))
        painter = QtGui.QPainter(pm)
        pen = QtGui.QPen(QtGui.QColor("#2a2a2a"))
        painter.setPen(pen)
        for y in range(10, 300, 18):
            for x in range(10, 300, 18):
                painter.drawEllipse(QtCore.QPoint(x, y), 3, 3)
        painter.end()
        grill.setPixmap(pm)
        v.addWidget(grill)
        return f

    def _mhz(self) -> float:
        return round(self.freq_slider.value() / 10.0, 1)

    def _update_lcd(self):
        prog = self.prog_group.checkedId() if self.prog_group.checkedId() >= 0 else 0
        self.lcd.setText(f"{self._mhz():.1f} MHz  P{prog}  {'▶' if self.play_btn.isDown() else '⏸'}")

    def _append_log(self, s: str):
        self.log.append(s)

    def _handle_log_line(self, s: str):
        """Parse simple PAD-like lines from nrsc5 stderr and trigger art lookup.
        Typical lines include: "Title: ...", "Artist: ..." or combined messages.
        This is heuristic — for best results use the nrsc5 Python API later.
        """
        self._append_log(s)
        lower = s.lower()
        updated = False
        if lower.startswith("title:"):
            self._title = s.split(":", 1)[1].strip() or None
            updated = True
        elif lower.startswith("artist:"):
            self._artist = s.split(":", 1)[1].strip() or None
            updated = True
        elif lower.startswith("album:"):
            self._album = s.split(":", 1)[1].strip() or None
            updated = True
        elif "title" in lower and "artist" in lower and ":" in s:
            # crude combined line parser
            try:
                parts = [p.strip() for p in s.split("|")]
                for p in parts:
                    if p.lower().startswith("title:"):
                        self._title = p.split(":", 1)[1].strip()
                    if p.lower().startswith("artist:"):
                        self._artist = p.split(":", 1)[1].strip()
                    if p.lower().startswith("album:"):
                        self._album = p.split(":", 1)[1].strip()
                updated = True
            except Exception:
                pass
        if updated:
            self.meta_lbl.setText(f"{self._title or ''} — {self._artist or ''}\n{self._album or ''}")
            self._art_timer.start()

    def _set_volume(self, val: int):
        # Let ffplay manage system volume: we cannot control its volume over stdin here.
        # (Extend by launching ffplay with -af volume and restarting, or use python-vlc.)
        pass

    # --- Actions ---
    def _start(self):
        self.cfg.frequency_mhz = self._mhz()
        self.cfg.program = self.prog_group.checkedId() if self.prog_group.checkedId() >= 0 else 0
        QtCore.QMetaObject.invokeMethod(self.worker, "start")
        self._update_lcd()

    def _stop(self):
        QtCore.QMetaObject.invokeMethod(self.worker, "stop")
        self._update_lcd()

    def _scan(self):
        # Very simple scan: step 0.2 MHz across the band and log carriers that lock quickly
        self._append_log("[scan] starting quick scan (very basic)")

        def do_scan():
            orig_freq = self._mhz()
            step = 0.2
            found = []
            for i in range(880, 1081, int(step * 10)):
                if self.worker._nrsc5:  # stop if playing
                    break
                f = i / 10.0
                try:
                    p = subprocess.Popen(["nrsc5", "-q", "-o", "-", f"{f}", "0"],
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        out, err = p.communicate(timeout=2.5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        out, err = p.communicate()
                    if b"Program 0" in err or b"Audio" in err or b"Acquiring" in err:
                        found.append(f)
                        self.worker.logLine.emit(f"[scan] possible HD at {f:.1f} MHz")
                except Exception:
                    pass
            self.worker.logLine.emit("[scan] done: " + ", ".join(f"{x:.1f}" for x in found) if found else "none")
            self.freq_slider.setValue(int(orig_freq * 10))

        threading.Thread(target=do_scan, daemon=True, name="quick-scan").start()

    # --- Program button toggles ---
    @QtCore.Slot(int, bool)
    def _prog_changed(self, id: int, checked: bool):
        """Update the selected HD subchannel (program 0–3) when a button toggles."""
        if not checked:
            return
        self.cfg.program = id
        self._update_lcd()
        # Optional: auto-retune if already playing.
        # if self.worker and self.worker._nrsc5:
        #     self._stop()
        #     self._start()

    # --- Album art lookup ---
    def _fetch_album_art(self):
        if not self._artist and not self._title:
            return
        key = json.dumps({"a": self._artist, "t": self._title, "al": self._album})
        if key in self._art_cache:
            self.art.setPixmap(self._art_cache[key].scaled(self.art.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            return
        threading.Thread(target=self._fetch_art_worker, args=(key, self._artist, self._title, self._album), daemon=True, name="art-fetch").start()

    def _fetch_art_worker(self, key: str, artist: str | None, title: str | None, album: str | None):
        try:
            # iTunes Search API: no auth needed. Prefer album name; fallback to artist+title.
            q = album or f"{artist or ''} {title or ''}".strip()
            if not q:
                return
            url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({
                "term": q,
                "media": "music",
                "limit": 1,
            })
            r = requests.get(url, timeout=5)
            if r.ok:
                js = r.json()
                if js.get("resultCount", 0) > 0:
                    art_url = js["results"][0].get("artworkUrl100") or js["results"][0].get("artworkUrl60")
                    if art_url:
                        # request a larger image by swapping size, many iTunes URLs support 600x or 1200x
                        art_url = art_url.replace("100x100bb", "600x600bb").replace("60x60bb", "600x600bb")
                        img = requests.get(art_url, timeout=5)
                        if img.ok:
                            pm = QtGui.QPixmap()
                            pm.loadFromData(img.content)
                            if not pm.isNull():
                                self._art_cache[key] = pm
                                self.art.setPixmap(pm.scaled(self.art.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                                return
        except Exception as e:
            self._append_log(f"[art] lookup failed: {e}")
        # fallback UI
        self.art.setText("No Art")

    # --- Ensure clean shutdown ---
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._stop()
            if self.thread and self.thread.isRunning():
                self.thread.quit()
                self.thread.wait(2000)
        except Exception:
            pass
        super().closeEvent(event)


# ---------- Run ----------

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = BoomboxUI()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
