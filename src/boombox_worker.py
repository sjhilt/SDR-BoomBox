"""
Worker module for SDR-Boombox
Handles audio processing, HD Radio (nrsc5) and FM (rtl_fm) decoding
"""

import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from PySide6 import QtCore


@dataclass
class Cfg:
    mhz: float = 105.5
    gain: float | None = 40.0
    device_index: int | None = None
    volume: float = 1.0
    ppm: int = 5
    hd_program: int = 0  # 0 for HD1, 1 for HD2, etc.


class Worker(QtCore.QObject):
    """Worker thread for handling radio decoding and audio playback"""
    
    started = QtCore.Signal(str)       # "hd" | "fm"
    stopped = QtCore.Signal(int, str)  # rc, mode
    logLine = QtCore.Signal(str)
    hdSynced = QtCore.Signal()
    bitrateUpdate = QtCore.Signal(str)  # Bitrate display update

    def __init__(self, cfg: Cfg, lot_dir: Path):
        super().__init__()
        self.cfg = cfg
        self.lot_dir = lot_dir
        self._mode: str | None = None
        self._nrsc5: subprocess.Popen | None = None
        self._fm: subprocess.Popen | None = None
        self._ffplay: subprocess.Popen | None = None
        self._stop_evt = threading.Event()

    # ---------- command builders ----------
    def ffplay_cmd(self, is_fm: bool, muted: bool = False) -> list[str]:
        """Build ffplay command for audio playback"""
        base = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "warning"]
        # Add volume control for muting (0 = muted, 1 = normal)
        if muted:
            base += ["-volume", "0"]
        if is_fm:
            base += ["-f", "s16le", "-ar", "48000", "-i", "-"]
        else:
            base += ["-i", "-"]
        return base

    def nrsc5_cmd(self) -> list[str]:
        """Build nrsc5 command for HD Radio decoding"""
        # Ensure the LOT files directory exists
        self.lot_dir.mkdir(exist_ok=True)
        
        cmd = ["nrsc5"]
        if self.cfg.gain is not None: 
            cmd += ["-g", str(self.cfg.gain)]
        if self.cfg.device_index is not None: 
            cmd += ["-d", str(self.cfg.device_index)]
        # --dump-aas-files saves LOT files (album art and data services)
        cmd += ["--dump-aas-files", str(self.lot_dir)]
        # -o - pipes audio to stdout for ffplay
        cmd += ["-o", "-", f"{self.cfg.mhz}", str(self.cfg.hd_program)]
        return cmd

    def rtl_fm_cmd(self) -> list[str]:
        """Build rtl_fm command for analog FM decoding"""
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
        """Forward audio data from decoder to player in a separate thread"""
        def run():
            try:
                # Keep forwarding audio chunks until stopped
                while not self._stop_evt.is_set():
                    chunk = src.read(8192)  # Read 8KB chunks for smooth playback
                    if not chunk: 
                        break  # End of stream
                    
                    # Write to destination if pipe is still open
                    if dst and not dst.closed:
                        try:
                            dst.write(chunk)
                            dst.flush()
                        except (BrokenPipeError, OSError):
                            # Player closed the pipe, stop forwarding
                            break
            except Exception:
                pass  # Silently handle pipe errors during shutdown
            finally:
                # Clean up both pipes when done
                for pipe in [src, dst]:
                    if pipe and not pipe.closed:
                        try:
                            pipe.close()
                        except:
                            pass
        
        threading.Thread(target=run, daemon=True, name="pipe-forward").start()

    def _stderr_reader(self, proc, prefix="", on_line=None):
        """Read stderr from process and emit log lines"""
        def run():
            try:
                if not proc or not proc.stderr:
                    return
                    
                for line in iter(proc.stderr.readline, b""):
                    if self._stop_evt.is_set(): 
                        break
                    if not line:  # EOF
                        break
                    try:
                        s = line.decode("utf-8", "ignore").rstrip()
                        # Protect against extremely long lines
                        if len(s) > 5000:
                            s = s[:5000] + "... [truncated]"
                        self.logLine.emit(prefix + s)
                        if on_line: 
                            on_line(s)
                    except:
                        pass  # Ignore decode errors
            except (ValueError, OSError):
                # Pipe closed or process terminated
                pass
            except Exception:
                pass
        
        threading.Thread(target=run, daemon=True, name="stderr-reader").start()

    def _terminate(self, p: subprocess.Popen | None):
        """Safely terminate a subprocess with proper cleanup"""
        if not p: 
            return
        try:
            # Close all pipes first to prevent blocking on I/O
            for pipe in [p.stdin, p.stdout, p.stderr]:
                if pipe:
                    try:
                        pipe.close()
                    except:
                        pass
            
            # Try graceful termination first
            if p.poll() is None:  # Process still running
                p.terminate()  # Send SIGTERM
                try: 
                    p.wait(timeout=1.25)  # Give it time to exit cleanly
                except subprocess.TimeoutExpired: 
                    # Force kill if it didn't respond to terminate
                    try:
                        p.kill()  # Send SIGKILL
                        p.wait(timeout=0.5)
                    except:
                        pass  # Process might have already exited
        except Exception:
            pass  # Ignore errors during cleanup

    # ---------- slots ----------
    @QtCore.Slot()
    def stop(self):
        """Stop all decoding and playback"""
        self._stop_evt.set()
        
        # Give threads a moment to notice the stop event
        time.sleep(0.1)
        
        # Terminate processes in order
        for proc in [self._nrsc5, self._fm, self._ffplay]:
            if proc:
                self._terminate(proc)
        
        # Clear references
        self._nrsc5 = None
        self._fm = None
        
        # Get return code before clearing ffplay
        rc = 0
        if self._ffplay:
            try:
                rc = self._ffplay.returncode or 0
            except:
                rc = 0
        
        mode = self._mode or ""
        self._ffplay = None
        self._mode = None
        
        # Emit stopped signal
        try:
            self.stopped.emit(rc, mode)
        except RuntimeError:
            pass  # Object might be deleted

    @QtCore.Slot(bool)
    def start_hd(self, muted: bool = False):
        """Start HD Radio decoding"""
        self.stop()
        self._stop_evt.clear()
        self._mode = "hd"
        try:
            self._ffplay = subprocess.Popen(
                self.ffplay_cmd(is_fm=False, muted=muted), 
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            self._nrsc5 = subprocess.Popen(
                self.nrsc5_cmd(), 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                bufsize=0
            )
            self._pipe_forward(self._nrsc5.stdout, self._ffplay.stdin)

            def parse(line: str):
                if ("Synchronized" in line) or ("Audio program" in line) or ("SIG Service:" in line):
                    self.hdSynced.emit()
                # Parse bitrate from nrsc5 output - emit EXACTLY what appears in the log
                if "Audio bit rate:" in line:
                    try:
                        # Extract everything after "Audio bit rate:" including decimals and "kbps"
                        parts = line.split("Audio bit rate:", 1)
                        if len(parts) == 2:
                            bitrate_str = parts[1].strip()
                            # Emit exactly what's in the log (e.g., "46.7 kbps")
                            self.bitrateUpdate.emit(bitrate_str)
                    except:
                        pass

            self._stderr_reader(self._nrsc5, "", parse)
            self.started.emit("hd")
        except FileNotFoundError as e:
            self.logLine.emit(f"Missing executable: {e}")
            self.stop()

    @QtCore.Slot(bool)
    def start_fm(self, muted: bool = False):
        """Start analog FM decoding"""
        self.stop()
        self._stop_evt.clear()
        self._mode = "fm"
        try:
            self._ffplay = subprocess.Popen(
                self.ffplay_cmd(is_fm=True, muted=muted), 
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL
            )
            self._fm = subprocess.Popen(
                self.rtl_fm_cmd(), 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                bufsize=0
            )
            self._pipe_forward(self._fm.stdout, self._ffplay.stdin)
            self._stderr_reader(self._fm, "[rtl_fm] ")
            self.started.emit("fm")
        except FileNotFoundError as e:
            self.logLine.emit(f"Missing executable: {e}")
            self.stop()
