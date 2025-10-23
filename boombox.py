#!/usr/bin/env python3
"""
sdr_boombox: QoL fixes for metadata display and "segment/no track" handling

Changes vs boombox5:
  1) Full-title display: removed pixel-width eliding. Titles/artists now wrap across lines.
     Optional marquee for super-long lines (toggle MARQUEE=True).
  2) Segment detection: if we see "XHDR: ... -1" or Title/Artist look like a station slug/slogan,
     we treat it as a non-music segment. We:
        • Stop album-art lookups
        • Clear current art
        • Show "On-air segment" and station branding if available
"""

import os
import sys
import re
import json
import urllib.parse
import threading
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
import requests

APP_NAME = "SDR-Boombox"
MARQUEE = False  # set True to enable scrolling title when it exceeds N chars


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


class RadioWorker(QtCore.QObject):
    started = QtCore.Signal()
    stopped = QtCore.Signal(int)
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
        cmd += ["-o", "-", f"{self.cfg.frequency_mhz}", str(self.cfg.program)]
        return cmd

    def build_ffplay_cmd(self) -> list[str]:
        return ["ffplay", "-autoexit", "-nodisp", "-hide_banner", "-loglevel", "error", "-i", "-"]

    @QtCore.Slot()
    def start(self):
        self.stop()
        self._stop_evt.clear()
        try:
            nrsc5_cmd = self.build_nrsc5_cmd()
            ffplay_cmd = self.build_ffplay_cmd()

            self._ffplay = subprocess.Popen(
                ffplay_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._nrsc5 = subprocess.Popen(
                nrsc5_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
            )

            def forward():
                assert self._nrsc5 and self._ffplay
                src = self._nrsc5.stdout; dst = self._ffplay.stdin
                if not src or not dst: return
                try:
                    while not self._stop_evt.is_set():
                        chunk = src.read(8192)
                        if not chunk: break
                        dst.write(chunk); dst.flush()
                except Exception:
                    pass

            def read_logs():
                assert self._nrsc5
                err = self._nrsc5.stderr
                if not err: return
                for line in iter(err.readline, b""):
                    if self._stop_evt.is_set(): break
                    try:
                        self.logLine.emit(line.decode("utf-8", errors="ignore").rstrip())
                    except Exception:
                        pass

            self._forward_thread = threading.Thread(target=forward, daemon=True).start()
            threading.Thread(target=read_logs, daemon=True).start()
            self.started.emit()
        except FileNotFoundError as e:
            self.logLine.emit(f"Executable not found: {e}")
            self.stop()

    @QtCore.Slot()
    def stop(self):
        self._stop_evt.set()
        if self._nrsc5 and self._nrsc5.poll() is None:
            self._nrsc5.terminate()
            try: self._nrsc5.wait(timeout=2)
            except subprocess.TimeoutExpired: self._nrsc5.kill()
        self._nrsc5 = None
        if self._ffplay and self._ffplay.poll() is None:
            try:
                if self._ffplay.stdin:
                    try: self._ffplay.stdin.close()
                    except Exception: pass
                self._ffplay.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._ffplay.kill()
        rc = self._ffplay.returncode if self._ffplay else 0
        self._ffplay = None
        self.stopped.emit(rc)


class BoomboxUI(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SDR-Boombox – HD Radio (NRSC‑5)")
        self.setMinimumSize(1000, 560)
        self.setStyleSheet(
            """
            QMainWindow { background: #151515; }
            QLabel#lcd {
                font-family: 'DS-Digital', monospace;
                color: #7CFC00; background:#0a0a0a;
                padding: 14px 22px; border-radius: 14px; letter-spacing: 1px;
            }
            QFrame#boombox {
                border: 4px solid #333; border-radius: 24px;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #1e1e1e, stop:1 #111);
            }
            QPushButton { background: #2a2a2a; color:#eee; border:1px solid #444; border-radius: 10px; padding:10px 14px; }
            QPushButton:hover { background:#333; } QPushButton:pressed { background:#222; }
            QSlider::groove:horizontal { height: 10px; background:#333; border-radius:5px; }
            QSlider::handle:horizontal { width: 18px; background:#777; border-radius:9px; margin:-4px 0; }
            QTextEdit { background:#0f0f0f; color:#ccc; border:1px solid #333; }

            QLabel#art { background:#0c0c0c; border:1px solid #2c2c2c; border-radius:12px; }

            QFrame#metaCard { background: rgba(0,0,0,0.55); border: 1px solid #202020; border-radius: 12px; }
            QLabel#metaTitle { color: #f2f2f2; font-size: 16px; font-weight: 600; }
            QLabel#metaSubtitle { color: #b9b9b9; font-size: 13px; font-weight: 400; }
            """
        )

        central = QtWidgets.QFrame(objectName="boombox")
        self.setCentralWidget(central)
        layout = QtWidgets.QGridLayout(central)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setHorizontalSpacing(18)

        left_spk = self._speaker(); right_spk = self._speaker()

        center = QtWidgets.QVBoxLayout()

        self.lcd = QtWidgets.QLabel("—.—— MHz  P0  ⏸", objectName="lcd")
        f = self.lcd.font(); f.setPointSize(26); self.lcd.setFont(f)
        self.lcd.setAlignment(QtCore.Qt.AlignCenter)
        center.addWidget(self.lcd)

        freq_row = QtWidgets.QHBoxLayout()
        self.freq_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.freq_slider.setRange(880, 1080); self.freq_slider.setValue(995)
        self.freq_slider.valueChanged.connect(self._update_lcd)
        freq_row.addWidget(QtWidgets.QLabel("88.0"))
        freq_row.addWidget(self.freq_slider, 1)
        freq_row.addWidget(QtWidgets.QLabel("108.0"))
        center.addLayout(freq_row)

        prog_row = QtWidgets.QHBoxLayout()
        self.prog_group = QtWidgets.QButtonGroup(self)
        for i in range(4):
            b = QtWidgets.QPushButton(f"Program {i}"); b.setCheckable(True)
            if i == 0: b.setChecked(True)
            self.prog_group.addButton(b, i); prog_row.addWidget(b)
        center.addLayout(prog_row)

        ctrl_row = QtWidgets.QHBoxLayout()
        self.play_btn = QtWidgets.QPushButton("▶ Play")
        self.stop_btn = QtWidgets.QPushButton("■ Stop")
        self.scan_btn = QtWidgets.QPushButton("🔎 Quick Scan")
        ctrl_row.addWidget(self.play_btn); ctrl_row.addWidget(self.stop_btn); ctrl_row.addWidget(self.scan_btn)
        ctrl_row.addStretch(1)
        center.addLayout(ctrl_row)

        self.log = QtWidgets.QTextEdit(readOnly=True); self.log.setFixedHeight(160); center.addWidget(self.log)

        side_panel = QtWidgets.QVBoxLayout()
        self.art = QtWidgets.QLabel(objectName="art")
        self.art.setFixedSize(260, 260); self.art.setAlignment(QtCore.Qt.AlignCenter); self.art.setText("No Art")
        side_panel.addWidget(self.art)

        self.meta_card = QtWidgets.QFrame(objectName="metaCard")
        meta_layout = QtWidgets.QVBoxLayout(self.meta_card)
        meta_layout.setContentsMargins(12, 10, 12, 10)
        self.meta_title = QtWidgets.QLabel(" ", objectName="metaTitle")
        self.meta_title.setWordWrap(True)
        self.meta_sub = QtWidgets.QLabel(" ", objectName="metaSubtitle")
        self.meta_sub.setWordWrap(True)
        # optional marquee
        self._marquee_timer = QtCore.QTimer(self); self._marquee_timer.setInterval(200); self._marquee_timer.timeout.connect(self._tick_marquee)
        self._marquee_text = None
        meta_layout.addWidget(self.meta_title); meta_layout.addWidget(self.meta_sub)
        side_panel.addWidget(self.meta_card)
        side_panel.addStretch(1)

        layout.addWidget(left_spk, 0, 0, 3, 1)
        layout.addLayout(center, 0, 1, 3, 1)
        layout.addLayout(side_panel, 0, 2, 3, 1)

        self.cfg = NRSC5Config()
        self.worker = RadioWorker(self.cfg)
        self.thread = QtCore.QThread(self); self.worker.moveToThread(self.thread); self.thread.start()

        self._artist = None; self._title = None; self._album = None
        self._station = None; self._slogan = None
        self._is_segment = False
        self._art_cache: dict[str, QtGui.QPixmap] = {}
        self._art_timer = QtCore.QTimer(self); self._art_timer.setInterval(600); self._art_timer.setSingleShot(True)
        self._art_timer.timeout.connect(self._fetch_album_art)

        self.play_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.scan_btn.clicked.connect(self._scan)
        self.prog_group.idToggled.connect(self._prog_changed)
        self.worker.logLine.connect(self._handle_log_line)
        self.worker.started.connect(lambda: self._append_log("[audio] started"))
        self.worker.stopped.connect(lambda rc: self._append_log(f"[audio] stopped rc={rc}"))

        self._update_lcd()

        if not which("nrsc5"): self._append_log("WARNING: nrsc5 was not found in PATH.")
        if not which("ffplay"): self._append_log("WARNING: ffplay (FFmpeg) was not found in PATH.")

    def _speaker(self) -> QtWidgets.QFrame:
        f = QtWidgets.QFrame(); v = QtWidgets.QVBoxLayout(f)
        grill = QtWidgets.QLabel(); pm = QtGui.QPixmap(320, 320); pm.fill(QtGui.QColor("#1b1b1b"))
        painter = QtGui.QPainter(pm); pen = QtGui.QPen(QtGui.QColor("#2a2a2a")); painter.setPen(pen)
        for y in range(10, 320, 18):
            for x in range(10, 320, 18):
                painter.drawEllipse(QtCore.QPoint(x, y), 3, 3)
        painter.end(); grill.setPixmap(pm); v.addWidget(grill); return f

    def _mhz(self) -> float: return round(self.freq_slider.value() / 10.0, 1)

    def _update_lcd(self):
        prog = self.prog_group.checkedId() if self.prog_group.checkedId() >= 0 else 0
        self.lcd.setText(f"{self._mhz():.1f} MHz  P{prog}  {'▶' if self.play_btn.isDown() else '⏸'}")

    def _append_log(self, s: str): self.log.append(s)

    # --- Regexes ---
    _ts_re = re.compile(r"^\s*\d{2}:\d{2}:\d{2}\s+")
    _title_re = re.compile(r"\bTitle:\s*(.+)", re.IGNORECASE)
    _artist_re = re.compile(r"\bArtist:\s*(.+)", re.IGNORECASE)
    _album_re = re.compile(r"\bAlbum:\s*(.+)", re.IGNORECASE)
    _slogan_re = re.compile(r"\bSlogan:\s*(.+)", re.IGNORECASE)
    _station_re = re.compile(r"\bStation name:\s*(.+)", re.IGNORECASE)
    _xhdr_re = re.compile(r"\bXHDR:\s*\d+\s+[0-9A-Fa-f]+\s+(-?\d+)\b")

    def _handle_log_line(self, s: str):
        self._append_log(s)
        line = self._ts_re.sub("", s).strip()

        # XHDR segment detection
        m = self._xhdr_re.search(line)
        if m:
            last = m.group(1).strip()
            self._is_segment = (last == "-1")
            if self._is_segment:
                # Clear metadata/art and show segment indicator
                self._append_log("[meta] detected on-air segment (no track id)")
                self._title = None; self._artist = None; self._album = None
                self._set_meta_labels(segment=True)
                self._clear_art()
                return  # skip further parsing on this line

        updated = False
        m = self._title_re.search(line)
        if m:
            self._title = m.group(1).strip()
            updated = True
        m = self._artist_re.search(line)
        if m:
            self._artist = m.group(1).strip()
            updated = True
        m = self._album_re.search(line)
        if m:
            self._album = m.group(1).strip()
            updated = True

        m = self._station_re.search(line)
        if m:
            self._station = m.group(1).strip()
            self.lcd.setText(f"{self._mhz():.1f} MHz  P{self.prog_group.checkedId() if self.prog_group.checkedId()>=0 else 0}  {self._station}")
        m = self._slogan_re.search(line)
        if m:
            self._slogan = m.group(1).strip()
            self.lcd.setText(f"{self._mhz():.1f} MHz  P{self.prog_group.checkedId() if self.prog_group.checkedId()>=0 else 0}  {self._slogan}")

        # Heuristic: if title/artist equal station branding, treat as segment
        branding = {x for x in [self._station, self._slogan] if x}
        if updated and branding:
            title_lower = (self._title or "").lower()
            artist_lower = (self._artist or "").lower()
            looks_like_brand = any(b.lower() in title_lower or b.lower() in artist_lower for b in branding)
            if looks_like_brand:
                self._is_segment = True

        if updated:
            self._set_meta_labels(segment=self._is_segment)
            if not self._is_segment:
                self._art_timer.start()
            else:
                self._clear_art()

    def _set_meta_labels(self, segment: bool = False):
        if segment:
            # Prefer slogan or station name for display
            line1 = self._slogan or self._station or "On-air segment"
            line2 = ""  # no album for segments
            self.meta_title.setText(line1)
            self.meta_sub.setText(line2)
            self._stop_marquee()
            return

        title = (self._title or "").strip()
        artist = (self._artist or "").strip()
        album = (self._album or "").strip()

        title_line = f"{title} — {artist}".strip(" —")
        self.meta_title.setText(title_line if title_line else " ")
        self.meta_sub.setText(album if album else " ")

        # Word wrap already enabled; optionally marquee very long lines
        if MARQUEE and len(title_line) > 48:
            self._start_marquee(title_line)
        else:
            self._stop_marquee()

    def _start_marquee(self, text: str):
        self._marquee_text = text + "   "
        if not self._marquee_timer.isActive():
            self._marquee_timer.start()

    def _stop_marquee(self):
        if self._marquee_timer.isActive():
            self._marquee_timer.stop()
        self._marquee_text = None

    def _tick_marquee(self):
        if not self._marquee_text:
            return
        self._marquee_text = self._marquee_text[1:] + self._marquee_text[0]
        self.meta_title.setText(self._marquee_text)

    def _clear_art(self):
        self._art_timer.stop()
        self.art.setPixmap(QtGui.QPixmap())  # clear
        self.art.setText("No Art")

    def _set_volume(self, val: int): pass  # ffplay controls output

    def _start(self):
        self.cfg.frequency_mhz = self._mhz()
        self.cfg.program = self.prog_group.checkedId() if self.prog_group.checkedId() >= 0 else 0
        QtCore.QMetaObject.invokeMethod(self.worker, "start")
        self._update_lcd()

    def _stop(self):
        QtCore.QMetaObject.invokeMethod(self.worker, "stop")
        self._update_lcd()

    def _scan(self):
        self._append_log("[scan] starting quick scan (very basic)")
        def do_scan():
            orig = self._mhz(); step = 0.2; found = []
            for i in range(880, 1081, int(step * 10)):
                if self.worker._nrsc5: break
                f = i / 10.0
                try:
                    p = subprocess.Popen(["nrsc5", "-q", "-o", "-", f"{f}", "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    try:
                        out, err = p.communicate(timeout=2.5)
                    except subprocess.TimeoutExpired:
                        p.kill(); out, err = p.communicate()
                    if b"Program 0" in err or b"Audio" in err or b"Acquiring" in err:
                        found.append(f); self.worker.logLine.emit(f"[scan] possible HD at {f:.1f} MHz")
                except Exception: pass
            self.worker.logLine.emit("[scan] done: " + (", ".join(f"{x:.1f}" for x in found) if found else "none"))
            self.freq_slider.setValue(int(orig * 10))
        threading.Thread(target=do_scan, daemon=True, name="quick-scan").start()

    @QtCore.Slot(int, bool)
    def _prog_changed(self, id: int, checked: bool):
        if not checked: return
        self.cfg.program = id; self._update_lcd()

    def _fetch_album_art(self):
        # Guard: don't fetch during segments
        if self._is_segment: return
        if not getattr(self, "_artist", None) and not getattr(self, "_title", None):
            return
        key = json.dumps({"a": self._artist, "t": self._title, "al": self._album})
        if key in self._art_cache:
            self.art.setPixmap(self._art_cache[key].scaled(self.art.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            return
        threading.Thread(target=self._fetch_art_worker, args=(key, self._artist, self._title, self._album), daemon=True).start()

    def _fetch_art_worker(self, key: str, artist: str | None, title: str | None, album: str | None):
        try:
            q = (album or f"{artist or ''} {title or ''}".strip())
            if not q: return
            url = "https://itunes.apple.com/search?" + urllib.parse.urlencode({"term": q, "media": "music", "limit": 1})
            r = requests.get(url, timeout=5)
            if r.ok:
                js = r.json()
                if js.get("resultCount", 0) > 0:
                    art_url = js["results"][0].get("artworkUrl100") or js["results"][0].get("artworkUrl60")
                    if art_url:
                        art_url = art_url.replace("100x100bb", "600x600bb").replace("60x60bb", "600x600bb")
                        img = requests.get(art_url, timeout=5)
                        if img.ok:
                            pm = QtGui.QPixmap(); pm.loadFromData(img.content)
                            if not pm.isNull():
                                self._art_cache[key] = pm
                                self.art.setPixmap(pm.scaled(self.art.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
                                return
        except Exception as e:
            self._append_log(f"[art] lookup failed: {e}")
        # if search failed, keep prior art; do not set "No Art" unless empty

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._stop()
            if self.thread and self.thread.isRunning():
                self.thread.quit(); self.thread.wait(2000)
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = BoomboxUI(); w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
