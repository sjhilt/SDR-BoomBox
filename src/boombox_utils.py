"""
Utility functions for SDR-Boombox
Shared helper functions and constants
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path
from PySide6 import QtGui


# Application constants
APP_NAME = "SDR-Boombox"
FALLBACK_TIMEOUT_S = 6.0
PRESETS_PATH = Path.home() / ".sdr_boombox_presets.json"
SETTINGS_PATH = Path.home() / ".sdr_boombox_settings.json"
LOT_FILES_DIR = Path.home() / ".sdr_boombox_data"
MAX_LOG_LINES = 1000  # Maximum lines to keep in log to prevent memory issues


def which(cmd: str) -> str | None:
    """Find executable in PATH"""
    p = shutil.which(cmd)
    if p:
        return p
    # Windows .exe quick check
    for d in os.getenv("PATH", "").split(os.pathsep):
        if not d:
            continue
        cand = Path(d) / (cmd + ".exe")
        if cand.exists():
            return str(cand)
    return None


def emoji_pixmap(emoji: str, size: int = 256) -> QtGui.QPixmap:
    """Create a pixmap from an emoji character"""
    pm = QtGui.QPixmap(size, size)
    pm.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pm)
    painter.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
    font_family = "Apple Color Emoji" if sys.platform == "darwin" else "Segoe UI Emoji"
    painter.setFont(QtGui.QFont(font_family, int(size * 0.75)))
    painter.drawText(pm.rect(), QtCore.Qt.AlignCenter, emoji)
    painter.end()
    return pm


class SleepPreventer:
    """Prevent system sleep while playing audio"""
    
    def __init__(self):
        self._caffeinate_process = None
    
    def prevent_sleep(self, prevent: bool, log_callback=None):
        """Prevent or allow system sleep (but allow screen saver)"""
        if sys.platform == "darwin":  # macOS
            if prevent:
                if not self._caffeinate_process:
                    try:
                        # Use caffeinate with -i flag to prevent idle sleep only
                        self._caffeinate_process = subprocess.Popen(
                            ["caffeinate", "-i"],  # -i prevents idle sleep only
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        if log_callback:
                            log_callback("[system] Sleep prevention enabled (screen saver allowed)")
                    except Exception as e:
                        if log_callback:
                            log_callback(f"[system] Could not prevent sleep: {e}")
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
                    if log_callback:
                        log_callback("[system] Sleep prevention disabled")
        
        elif sys.platform == "win32":  # Windows
            import ctypes
            if prevent:
                # Prevent sleep on Windows (but allow screen saver)
                # ES_CONTINUOUS | ES_SYSTEM_REQUIRED (no ES_DISPLAY_REQUIRED)
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
                if log_callback:
                    log_callback("[system] Sleep prevention enabled (screen saver allowed)")
            else:
                # Allow sleep on Windows
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS
                if log_callback:
                    log_callback("[system] Sleep prevention disabled")
        
        elif sys.platform.startswith("linux"):  # Linux
            if prevent:
                try:
                    # Try using systemd-inhibit - only inhibit sleep, not idle
                    self._caffeinate_process = subprocess.Popen(
                        ["systemd-inhibit", "--what=sleep", "--who=SDR-Boombox", 
                         "--why=Playing radio", "sleep", "infinity"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    if log_callback:
                        log_callback("[system] Sleep prevention enabled (screen saver allowed)")
                except Exception:
                    if log_callback:
                        log_callback("[system] Could not prevent sleep (systemd-inhibit not available)")
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
                    if log_callback:
                        log_callback("[system] Sleep prevention disabled")
    
    def cleanup(self):
        """Clean up on exit"""
        self.prevent_sleep(False)


# Import QtCore for the emoji_pixmap function
from PySide6 import QtCore


def cleanup_lot_files(keep_recent: bool = False, log_callback=None):
    """Clean up LOT files directory
    
    Args:
        keep_recent: If True, keep the most recent 20 files. If False, delete all.
        log_callback: Optional callback for logging
    """
    try:
        if not LOT_FILES_DIR.exists():
            return 0
        
        files = list(LOT_FILES_DIR.glob("*"))
        
        if not files:
            return 0
        
        if keep_recent and len(files) > 20:
            # Sort by modification time and keep only the most recent 20
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            files_to_delete = files[20:]
        elif not keep_recent:
            # Delete all files
            files_to_delete = files
        else:
            return 0
        
        deleted_count = 0
        for f in files_to_delete:
            try:
                if f.is_file():
                    f.unlink()
                    deleted_count += 1
            except Exception as e:
                if log_callback:
                    log_callback(f"[cleanup] Could not delete {f.name}: {e}")
        
        if log_callback and deleted_count > 0:
            log_callback(f"[cleanup] Deleted {deleted_count} LOT files")
        
        return deleted_count
        
    except Exception as e:
        if log_callback:
            log_callback(f"[cleanup] Error cleaning LOT files: {e}")
        return 0
