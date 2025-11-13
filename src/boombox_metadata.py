"""
Metadata and album art handling module for SDR-Boombox
Handles song metadata, album art fetching, and station logos
"""

import re
import time
import threading
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from PySide6 import QtCore, QtGui


class MetadataHandler(QtCore.QObject):
    """Handles metadata parsing and album art fetching"""
    
    # Signals
    artReady = QtCore.Signal(QtGui.QPixmap)  # pixmap
    artClear = QtCore.Signal()  # signal to clear art and show visualizer
    stationInfoUpdate = QtCore.Signal(list)  # list of station info items
    metadataUpdate = QtCore.Signal(str, str)  # title, subtitle
    
    # Regex patterns for parsing
    _title_re = re.compile(r"\bTitle:\s*(.+)", re.IGNORECASE)
    _artist_re = re.compile(r"\bArtist:\s*(.+)", re.IGNORECASE)
    _album_re = re.compile(r"\bAlbum:\s*(.+)", re.IGNORECASE)
    _slogan_re = re.compile(r"\bSlogan:\s*(.+)", re.IGNORECASE)
    _station_re = re.compile(r"\bStation name:\s*(.+)", re.IGNORECASE)
    _genre_re = re.compile(r"\bGenre:\s*(.+)", re.IGNORECASE)
    _message_re = re.compile(r"\b(?:Message|Alert|Info):\s*(.+)", re.IGNORECASE)
    _bitrate_re = re.compile(r"\bBitrate:\s*(\d+(?:\.\d+)?)\s*kbps", re.IGNORECASE)
    _audio_re = re.compile(r"\bAudio bit rate:\s*(\d+(?:\.\d+)?)\s*kbps", re.IGNORECASE)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        from .boombox_utils import LOT_FILES_DIR
        self.lot_dir = LOT_FILES_DIR
        
        # Current metadata state
        self._station_name = ""
        self.station_name = ""
        self.station_slogan = ""
        self.station_genre = ""
        self.station_messages = []
        self.last_title = ""
        self.last_artist = ""
        self.last_album = ""
        self.current_bitrate = 0.0
        self.current_frequency = 0.0
        self.current_hd_channel = 0
        
        # Album art state
        self.has_lot_art = False
        self.current_art_key = ""
        self.pending_lot_art = ""
        
        # Station logos
        self.station_logos = []  # List of logo info dicts
        self.current_logo_index = 0
        
        # Song change tracking for delayed logging
        self.pending_song_log = None
        self.song_log_timer = QtCore.QTimer()
        self.song_log_timer.setSingleShot(True)
        self.song_log_timer.timeout.connect(self._execute_delayed_log)
        self.last_logged_song = None  # Track last logged song to avoid duplicates
        
        # iTunes art fetch delay timer
        self.itunes_fetch_timer = QtCore.QTimer()
        self.itunes_fetch_timer.setSingleShot(True)
        self.itunes_fetch_timer.timeout.connect(self._delayed_itunes_fetch)
        self.pending_itunes_fetch = None  # Store (artist, title, log_callback) tuple
        
        # Stats database
        self.stats_db = None
        self.songs_logged_count = 0  # Track songs for cleanup trigger
        self.lot_files_count = 0  # Track LOT files for periodic cleanup
        
        # Periodic cleanup timer (every 30 minutes)
        self.periodic_cleanup_timer = QtCore.QTimer()
        self.periodic_cleanup_timer.timeout.connect(self._periodic_cleanup)
        self.periodic_cleanup_timer.start(30 * 60 * 1000)  # 30 minutes in milliseconds
        
        try:
            import sys
            import os
            # Add parent directory to path to import boombox_stats
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            from boombox_stats import StatsDatabase
            self.stats_db = StatsDatabase()
        except ImportError:
            pass  # Stats module not available
        
    def parse_log_line(self, line: str, hd_program: int = 0, log_callback=None):
        """Parse a log line for metadata"""
        metadata_changed = False
        updates = {}
        
        # Store HD channel for stats
        self.current_hd_channel = hd_program
        
        # Station name
        m = self._station_re.search(line)
        if m:
            self.station_name = m.group(1).strip()
            updates['station_name'] = self.station_name
            metadata_changed = True
        
        # Slogan
        m = self._slogan_re.search(line)
        if m:
            self.station_slogan = m.group(1).strip()
            updates['station_slogan'] = self.station_slogan
            metadata_changed = True
        
        # Genre
        m = self._genre_re.search(line)
        if m:
            self.station_genre = m.group(1).strip()
            updates['station_genre'] = self.station_genre
            metadata_changed = True
        
        # Messages/Alerts
        m = self._message_re.search(line)
        if m:
            msg = m.group(1).strip()
            if msg and msg not in self.station_messages:
                self.station_messages.append(msg)
                if len(self.station_messages) > 5:  # Keep only last 5
                    self.station_messages.pop(0)
                updates['station_messages'] = self.station_messages.copy()
                metadata_changed = True
        
        # Bitrate
        m = self._bitrate_re.search(line) or self._audio_re.search(line)
        if m:
            try:
                self.current_bitrate = float(m.group(1))
                updates['bitrate'] = self.current_bitrate
                metadata_changed = True
            except:
                pass
        
        # Title
        m = self._title_re.search(line)
        if m:
            t = m.group(1).strip()
            if t and t != self.last_title:
                # Song changed - clear any existing art
                if self.last_title:  # Only clear if we had a previous song
                    self.artClear.emit()
                self.last_title = t
                self.has_lot_art = False  # Reset for new song
                self.current_art_key = ""  # Reset art key for new song
                updates['title'] = t
                metadata_changed = True
        
        # Artist
        m = self._artist_re.search(line)
        if m:
            a = m.group(1).strip()
            if a and a != self.last_artist:
                # Artist changed - clear any existing art
                if self.last_artist:  # Only clear if we had a previous artist
                    self.artClear.emit()
                self.last_artist = a
                self.has_lot_art = False
                self.current_art_key = ""  # Reset art key for new song
                updates['artist'] = a
                metadata_changed = True
        
        # Album
        m = self._album_re.search(line)
        if m:
            album = m.group(1).strip()
            if album and album != self.last_album:
                self.last_album = album
                updates['album'] = album
                metadata_changed = True
        
        # Check for LOT file references
        if "LOT file:" in line:
            lot_match = re.search(r"port=(\d+).*?name=([^\s]+)", line)
            if lot_match:
                port = lot_match.group(1)
                lot_file = lot_match.group(2).strip()
                
                if lot_file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                    # Skip traffic/weather image files (but not DWRI info files)
                    if any(x in lot_file for x in ['TMT_', 'DWRO_']):
                        return
                    
                    # Check if it's a station logo
                    is_likely_logo = ('$$' in lot_file or 'SLWRXR' in lot_file or 
                                     '_logo' in lot_file.lower() or
                                     port == '5103' or
                                     (lot_file.startswith('4655_') and '$$' in lot_file))
                    
                    # Skip HD3 logos if we're on HD1
                    if hd_program == 0 and port == '5103':
                        return
                    
                    if is_likely_logo:
                        updates['logo_file'] = (lot_file, port)
                        metadata_changed = True
                    else:
                        # Regular album art
                        expected_ports = {
                            0: ['0810', '0010'],  # HD1
                            1: ['1810', '0011'],  # HD2  
                            2: ['5103', '0012'],  # HD3
                            3: ['5104', '0013']   # HD4
                        }
                        
                        if hd_program in expected_ports:
                            if port in expected_ports[hd_program]:
                                updates['art_file'] = (lot_file, port)
                                metadata_changed = True
        
        if metadata_changed:
            # Handle album art if found
            if 'art_file' in updates:
                lot_file, port = updates['art_file']
                self.handle_lot_art(lot_file, log_callback)
            
            # Handle station logo if found
            if 'logo_file' in updates:
                logo_file, port = updates['logo_file']
                self.handle_station_logo(logo_file, port, log_callback)
            
            # Emit station info updates
            if 'station_name' in updates or 'station_slogan' in updates or 'station_genre' in updates:
                self._update_station_display()
                # Also update metadata display if we don't have song info
                if not self.last_title and not self.last_artist:
                    self._update_metadata_display()
            
            # Emit metadata updates for title/artist
            if 'title' in updates or 'artist' in updates:
                self._update_metadata_display()
                # Schedule delayed logging if we have both title and artist
                if self.last_title and self.last_artist:
                    self._schedule_delayed_log(log_callback)
                # Schedule iTunes art fetch with delay if we have both artist and title
                # and we don't have LOT art yet
                if self.last_title and self.last_artist and not self.has_lot_art:
                    # Cancel any pending fetch
                    self.itunes_fetch_timer.stop()
                    # Store the fetch info and start timer (wait 3 seconds for LOT art)
                    self.pending_itunes_fetch = (self.last_artist, self.last_title, log_callback)
                    self.itunes_fetch_timer.start(3000)  # 3 second delay
                    if log_callback:
                        log_callback("[art] Scheduling iTunes fetch in 3 seconds (waiting for possible LOT art)")
    
    def handle_lot_art(self, lot_file: str, log_callback=None):
        """Handle album art from LOT files"""
        def try_load_art(attempts=0):
            try:
                lot_path = self.lot_dir / lot_file
                
                # Try to find with prefix if exact file doesn't exist
                if not lot_path.exists():
                    matching_files = list(self.lot_dir.glob(f"*_{lot_file}"))
                    if matching_files:
                        lot_path = matching_files[0]
                        if log_callback:
                            log_callback(f"[art] Found LOT file with prefix: {lot_path.name}")
                
                if lot_path.exists():
                    # Check if file is stable
                    size1 = lot_path.stat().st_size
                    time.sleep(0.1)
                    if lot_path.exists():
                        size2 = lot_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            QtCore.QTimer.singleShot(200, lambda: try_load_art(attempts + 1))
                            return
                    
                    pm = QtGui.QPixmap(str(lot_path))
                    if not pm.isNull():
                        self.has_lot_art = True
                        self.current_art_key = f"LOT||{lot_file}"
                        # Cancel any pending iTunes fetch since we have LOT art now
                        if self.itunes_fetch_timer.isActive():
                            self.itunes_fetch_timer.stop()
                            self.pending_itunes_fetch = None
                            if log_callback:
                                log_callback("[art] Cancelled pending iTunes fetch - LOT art available")
                        self.artReady.emit(pm)
                        if log_callback:
                            log_callback(f"[art] Album art loaded from LOT file: {lot_file} (replacing any iTunes art)")
                    else:
                        if log_callback:
                            log_callback(f"[art] LOT file exists but couldn't load as image: {lot_file}")
                elif attempts < 5:
                    if log_callback:
                        log_callback(f"[art] Waiting for LOT file: {lot_file} (attempt {attempts + 1})")
                    QtCore.QTimer.singleShot(500, lambda: try_load_art(attempts + 1))
                else:
                    if log_callback:
                        log_callback(f"[art] LOT file never appeared: {lot_file}")
            except Exception as e:
                if log_callback:
                    log_callback(f"[art] Error handling LOT file {lot_file}: {e}")
        
        try_load_art()
    
    def handle_station_logo(self, logo_file: str, port: str = None, log_callback=None):
        """Handle station logo display"""
        # Check if we already have this logo
        for existing_logo in self.station_logos:
            if existing_logo['file'] == logo_file:
                if log_callback:
                    log_callback(f"[art] Logo already in collection: {logo_file}")
                return
        
        def try_load_logo(attempts=0):
            try:
                logo_path = self.lot_dir / logo_file
                
                # Try to find with prefix if exact file doesn't exist
                if not logo_path.exists():
                    matching_files = list(self.lot_dir.glob(f"*_{logo_file}"))
                    if matching_files:
                        logo_path = matching_files[0]
                        if log_callback:
                            log_callback(f"[art] Found logo file with prefix: {logo_path.name}")
                
                if logo_path.exists():
                    # Check if file is stable
                    size1 = logo_path.stat().st_size
                    time.sleep(0.1)
                    if logo_path.exists():
                        size2 = logo_path.stat().st_size
                        if size1 != size2 and attempts < 3:
                            QtCore.QTimer.singleShot(200, lambda: try_load_logo(attempts + 1))
                            return
                    
                    pm = QtGui.QPixmap(str(logo_path))
                    if not pm.isNull():
                        # Add to logos collection
                        logo_info = {
                            'file': logo_file,
                            'path': str(logo_path),
                            'port': port,
                            'pixmap': pm
                        }
                        self.station_logos.append(logo_info)
                        
                        # Emit the first logo immediately as art (for watermark display)
                        if len(self.station_logos) == 1:
                            # Emit a smaller version to signal it's a logo
                            small_logo = pm.scaled(48, 48, QtCore.Qt.KeepAspectRatio, 
                                                 QtCore.Qt.SmoothTransformation)
                            self.artReady.emit(small_logo)
                        
                        if log_callback:
                            log_callback(f"[art] Added logo to collection ({len(self.station_logos)} total): {logo_file}")
                    else:
                        if log_callback:
                            log_callback(f"[art] Logo file exists but couldn't load as image: {logo_file}")
                elif attempts < 5:
                    if log_callback:
                        log_callback(f"[art] Waiting for logo file: {logo_file} (attempt {attempts + 1})")
                    QtCore.QTimer.singleShot(500, lambda: try_load_logo(attempts + 1))
                else:
                    if log_callback:
                        log_callback(f"[art] Logo file never appeared: {logo_file}")
            except Exception as e:
                if log_callback:
                    log_callback(f"[art] Error handling logo file {logo_file}: {e}")
        
        try_load_logo()
    
    def get_next_logo(self):
        """Get the next station logo in rotation"""
        if len(self.station_logos) > 1:
            self.current_logo_index = (self.current_logo_index + 1) % len(self.station_logos)
            logo_info = self.station_logos[self.current_logo_index]
            return logo_info['pixmap'], logo_info
        return None, None
    
    def _delayed_itunes_fetch(self):
        """Called after delay to fetch iTunes art if still needed"""
        if self.pending_itunes_fetch:
            artist, title, log_callback = self.pending_itunes_fetch
            self.pending_itunes_fetch = None
            
            # Check again if we still need iTunes art
            if not self.has_lot_art and self.last_artist == artist and self.last_title == title:
                self.fetch_itunes_art(artist, title, log_callback)
            elif log_callback:
                if self.has_lot_art:
                    log_callback("[art] Cancelled iTunes fetch - LOT art received during wait")
                else:
                    log_callback("[art] Cancelled iTunes fetch - song changed during wait")
    
    def fetch_itunes_art(self, artist: str, title: str, log_callback=None):
        """Fetch album art from iTunes API"""
        # Don't fetch if we already have LOT art
        if self.has_lot_art:
            if log_callback:
                log_callback("[art] Skipping iTunes fetch - LOT art already available")
            return
        
        # Check if this looks like station content
        if self.looks_like_station(artist) or self.looks_like_station(title):
            return
        
        key = f"iTunes||{artist}||{title}"
        if key == self.current_art_key:
            return  # Already fetched for this song
        
        def fetch():
            try:
                if log_callback:
                    log_callback(f"[art] Fetching album art from iTunes API for: {artist} - {title}")
                
                q = quote_plus(f"{artist} {title}")
                req = Request(f"https://itunes.apple.com/search?term={q}&entity=song&limit=1",
                             headers={"User-Agent": "SDR-Boombox"})
                
                with urlopen(req, timeout=5) as r:
                    data = r.read().decode("utf-8", "ignore")
                
                # Parse for artwork URL
                m = re.search(r'"artworkUrl100"\s*:\s*"([^"]+)"', data)
                if m:
                    url = m.group(1).replace("100x100bb.jpg", "300x300bb.jpg")
                    with urlopen(Request(url, headers={"User-Agent": "SDR-Boombox"}), timeout=5) as r2:
                        raw = r2.read()
                    
                    pm = QtGui.QPixmap()
                    pm.loadFromData(raw)
                    if not pm.isNull():
                        # Check if song hasn't changed while we were fetching
                        current_key = f"iTunes||{artist}||{title}"
                        if current_key == f"iTunes||{self.last_artist}||{self.last_title}":
                            # Update the art key to mark this as iTunes art
                            self.current_art_key = current_key
                            self.artReady.emit(pm)
                            if log_callback:
                                log_callback(f"[art] Album art retrieved from iTunes API successfully")
                        else:
                            if log_callback:
                                log_callback(f"[art] Song changed while fetching iTunes art, discarding")
                    else:
                        if log_callback:
                            log_callback(f"[art] iTunes API returned invalid image data")
                else:
                    if log_callback:
                        log_callback(f"[art] No album art found in iTunes API for: {artist} - {title}")
                    # Emit signal to clear art and show visualizer
                    self.artClear.emit()
            except Exception as e:
                if log_callback:
                    log_callback(f"[art] iTunes API fetch failed: {str(e)[:100]}")
                # Emit signal to clear art and show visualizer on error
                self.artClear.emit()
        
        # Run in background thread
        threading.Thread(target=fetch, daemon=True).start()
    
    @staticmethod
    def looks_like_station(text: str) -> bool:
        """Check if text looks like station content rather than a song"""
        if not text:
            return False
        
        t = text.lower()
        
        # Station-related phrases (case-insensitive)
        bad_phrases = ["commercial", "advertisement", "promo", "jingle", "weather", "traffic",
                      "coming up", "you're listening", "stay tuned", "call us", "text us", "win", 
                      "contest", "hd1", "hd2", "hd3", "hd4", "station id", "station identification", 
                      "#1", "us-", "us101", "us 101"]
        
        # Check for exact phrase matches
        for phrase in bad_phrases:
            if phrase in t:
                return True
        
        # Check for specific station call signs and patterns
        station_patterns = [
            r"^w[a-z]{2,3}\s+",  # Call signs starting with W (WUSY, WKDF, etc.)
            r"^k[a-z]{2,3}\s+",  # Call signs starting with K (KQED, KROQ, etc.)
            r"^(kiss|rock|country|hits|classic|news|talk)\s*(fm|am)?$",  # Just "KISS FM" etc
            r"^\d{2,3}\.\d\s*(fm|am)?$",  # Just frequency like "103.7"
            r"chattanooga'?s?\s+(rock|country|hits|classic)\s+station",  # Station slogans
            r"^(rock|kiss|country|hits|classic)\s+\d{2,3}\.\d$",  # "Rock 103.7" pattern
            r"^\w{3,4}\s+\w{2,3}-?\d{2,3}",  # Pattern like "WUSY US-101" or "KROQ HD-2"
            r"^us-?\d{2,3}",  # US-101, US101 patterns
            r"^\w{3,4}\s+\d{2,3}\.\d"  # Call sign + frequency like "WUSY 100.7"
        ]
        
        for pattern in station_patterns:
            if re.search(pattern, t):
                return True
        
        return False
    
    def _periodic_cleanup(self):
        """Periodic cleanup of LOT files (called every 30 minutes)"""
        try:
            removed = self.cleanup_lot_files(keep_count=50)
            if removed and removed > 0:
                # Log through parent if available
                parent = self.parent()
                if parent and hasattr(parent, 'log'):
                    parent.log(f"[cleanup] Periodic cleanup: Removed {removed} old LOT files (keeping last 50)")
        except Exception:
            pass  # Silently handle any errors
    
    def reset(self):
        """Reset all metadata state"""
        self.station_name = ""
        self.station_slogan = ""
        self.station_genre = ""
        self.station_messages = []
        self.last_title = ""
        self.last_artist = ""
        self.last_album = ""
        self.current_bitrate = 0.0
        self.current_frequency = 0.0
        self.current_hd_channel = 0
        self.has_lot_art = False
        self.current_art_key = ""
        self.pending_lot_art = ""
        self.station_logos = []
        self.current_logo_index = 0
        # Cancel any pending iTunes fetch
        self.itunes_fetch_timer.stop()
        self.pending_itunes_fetch = None
        # Cancel any pending song log
        self.song_log_timer.stop()
        self.pending_song_log = None
        self.last_logged_song = None
    
    def cleanup_lot_files(self, keep_count: int = 50):
        """Clean up old LOT files, keeping only the most recent ones"""
        try:
            if not self.lot_dir.exists():
                return 0
            
            # Get all files in the LOT directory
            files = list(self.lot_dir.glob("*"))
            
            # If no files, nothing to clean
            if not files:
                return 0
            
            # Sort by modification time (oldest first)
            files.sort(key=lambda f: f.stat().st_mtime)
            
            # If we have more than keep_count files, delete the oldest
            if len(files) > keep_count:
                removed_count = 0
                for f in files[:-keep_count]:
                    try:
                        f.unlink()
                        removed_count += 1
                    except Exception as e:
                        # Log individual file deletion errors but continue
                        pass
                return removed_count
        except Exception as e:
            # Log the error but don't crash
            pass
        return 0
    
    def _update_station_display(self):
        """Update station display information"""
        items = []
        
        if self.station_name:
            items.append({
                'text': self.station_name,
                'color': '#7CFC00',
                'weight': 600
            })
        
        if self.station_slogan:
            items.append({
                'text': self.station_slogan,
                'color': '#b9b9b9',
                'weight': 400,
                'style': 'italic'
            })
        
        if self.station_genre:
            items.append({
                'text': f"Genre: {self.station_genre}",
                'color': '#a0a0a0',
                'weight': 400
            })
        
        if items:
            self.stationInfoUpdate.emit(items)
    
    def _update_metadata_display(self):
        """Update metadata display for title and artist"""
        if self.last_title and self.last_artist:
            # We have both title and artist - show them
            self.metadataUpdate.emit(self.last_title, self.last_artist)
        elif self.last_title:
            # Only title, no artist
            self.metadataUpdate.emit(self.last_title, "")
        elif self.last_artist:
            # Only artist, no title
            self.metadataUpdate.emit("", self.last_artist)
        elif self.station_name and self.station_slogan:
            # No song info but we have station info - show station name and slogan
            self.metadataUpdate.emit(self.station_name, self.station_slogan)
        elif self.station_name:
            # Only station name, no slogan or song info
            self.metadataUpdate.emit(self.station_name, "")
    
    def set_frequency(self, frequency: float):
        """Set the current frequency for stats logging"""
        self.current_frequency = frequency
    
    def _schedule_delayed_log(self, log_callback=None):
        """Schedule a delayed log to wait for complete metadata"""
        # Cancel any existing timer
        self.song_log_timer.stop()
        
        # Store the pending log info
        self.pending_song_log = {
            'title': self.last_title,
            'artist': self.last_artist,
            'album': self.last_album,
            'log_callback': log_callback
        }
        
        # Start timer - wait 2 seconds for any additional metadata updates
        self.song_log_timer.start(2000)
    
    def _execute_delayed_log(self):
        """Execute the delayed log after timer expires"""
        if not self.pending_song_log:
            return
        
        # Extract the pending log info
        title = self.pending_song_log['title']
        artist = self.pending_song_log['artist']
        album = self.pending_song_log['album']
        log_callback = self.pending_song_log['log_callback']
        
        # Check if this is actually a new song (not a duplicate)
        current_song = f"{artist}||{title}"
        if current_song == self.last_logged_song:
            if log_callback:
                log_callback(f"[stats] Skipping duplicate: {artist} - {title}")
            self.pending_song_log = None
            return
        
        # Check if this was the last song played (from database)
        # This prevents re-logging when restarting app or switching back to a station
        if self.stats_db and self._is_last_played_song(artist, title):
            if log_callback:
                log_callback(f"[stats] Skipping last played song (likely app restart or station switch): {artist} - {title}")
            # Update our session tracking but don't log to database
            self.last_logged_song = current_song
            self.pending_song_log = None
            return
        
        # Log the song
        self._log_to_stats(title, artist, album, log_callback)
        
        # Update last logged song
        self.last_logged_song = current_song
        self.pending_song_log = None
    
    def _is_last_played_song(self, artist: str, title: str) -> bool:
        """Check if this song was the last one played (from database)"""
        if not self.stats_db:
            return False
        
        try:
            # Get the last played song from the database
            import sqlite3
            from pathlib import Path
            
            db_path = Path.home() / ".sdr_boombox_stats.db"
            if not db_path.exists():
                return False
            
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            
            # Get the most recent song entry
            cursor.execute("""
                SELECT artist, title FROM songs 
                ORDER BY timestamp DESC 
                LIMIT 1
            """)
            
            result = cursor.fetchone()
            conn.close()
            
            if result:
                last_artist, last_title = result
                # Check if it matches the current song
                return (last_artist == artist and last_title == title)
            
        except Exception:
            # If there's any error, just proceed with logging
            pass
        
        return False
    
    def _log_to_stats(self, title: str, artist: str, album: str, log_callback=None):
        """Log song to stats database"""
        if not self.stats_db:
            return
        
        # Don't log station content
        if self.looks_like_station(artist) or self.looks_like_station(title):
            if log_callback:
                log_callback(f"[stats] Skipping station content: {artist} - {title}")
            return
        
        # Also skip if title and artist are identical (likely station ID)
        if title == artist:
            if log_callback:
                log_callback(f"[stats] Skipping identical title/artist (likely station ID): {title}")
            return
        
        try:
            # Use station name if available, otherwise use frequency
            station = self.station_name if self.station_name else f"{self.current_frequency:.1f} MHz"
            
            self.stats_db.add_song(
                title=title,
                artist=artist,
                station=station,
                frequency=self.current_frequency,
                album=album,
                hd_channel=self.current_hd_channel
            )
            
            if log_callback:
                log_callback(f"[stats] Logged: {artist} - {title} on {station}")
            
            # Increment counter and cleanup LOT files every 5 songs
            self.songs_logged_count += 1
            if self.songs_logged_count >= 5:
                self.songs_logged_count = 0
                removed = self.cleanup_lot_files(keep_count=50)  # Reduced from 100 to 50
                if removed and removed > 0 and log_callback:
                    log_callback(f"[cleanup] Removed {removed} old LOT files (keeping last 50)")
                    
        except Exception as e:
            if log_callback:
                log_callback(f"[stats] Error logging song: {e}")
