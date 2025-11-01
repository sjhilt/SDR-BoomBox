#!/usr/bin/env python3
"""
===============================================================
   SDR-Boombox Stats Tracker
   Song History & Station Analytics
===============================================================

Author:     @sjhilt
Project:    SDR-Boombox Stats Module
License:    MIT License
Version:    1.0.0
Python:     3.10+

Description:
------------
Tracks and displays statistics for songs played on different radio stations.
Stores history in a JSON database and provides a GUI viewer for analytics.

Usage:
------
Run with: python boombox.py --stats
Or standalone: python boombox_stats.py
"""

import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import Dict, List, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

# Database path
STATS_DB_PATH = Path.home() / ".sdr_boombox_stats.json"

class StatsDatabase:
    """Manages the song statistics database"""
    
    def __init__(self):
        self.db_path = STATS_DB_PATH
        self.data = self._load_database()
    
    def _load_database(self) -> dict:
        """Load existing database or create new one"""
        if self.db_path.exists():
            try:
                with open(self.db_path, 'r') as f:
                    return json.load(f)
            except:
                return {"songs": [], "stations": {}}
        return {"songs": [], "stations": {}}
    
    def save_database(self):
        """Save database to disk"""
        try:
            with open(self.db_path, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"Error saving database: {e}")
    
    def add_song(self, title: str, artist: str, station: str, frequency: float, 
                 album: str = "", hd_channel: int = 0):
        """Add a song play to the database"""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "title": title,
            "artist": artist,
            "album": album,
            "station": station,
            "frequency": frequency,
            "hd_channel": hd_channel
        }
        
        self.data["songs"].append(entry)
        
        # Update station info
        station_key = f"{frequency:.1f} MHz"
        if station:
            station_key = station
        
        if station_key not in self.data["stations"]:
            self.data["stations"][station_key] = {
                "frequency": frequency,
                "name": station,
                "play_count": 0,
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat()
            }
        
        self.data["stations"][station_key]["play_count"] += 1
        self.data["stations"][station_key]["last_seen"] = datetime.now().isoformat()
        
        # Keep only last 10000 songs to prevent database from growing too large
        if len(self.data["songs"]) > 10000:
            self.data["songs"] = self.data["songs"][-10000:]
        
        self.save_database()
    
    def get_stats(self) -> dict:
        """Get comprehensive statistics"""
        stats = {
            "total_songs": len(self.data["songs"]),
            "unique_songs": 0,
            "unique_artists": 0,
            "stations": len(self.data["stations"]),
            "top_songs": [],
            "top_artists": [],
            "top_stations": [],
            "recent_songs": [],
            "hourly_distribution": defaultdict(int),
            "daily_distribution": defaultdict(int)
        }
        
        # Count unique songs and artists
        unique_songs = set()
        artist_counts = Counter()
        song_counts = Counter()
        
        for song in self.data["songs"]:
            song_key = f"{song['artist']} - {song['title']}"
            unique_songs.add(song_key)
            artist_counts[song['artist']] += 1
            song_counts[song_key] += 1
            
            # Time distribution
            dt = datetime.fromisoformat(song['timestamp'])
            stats["hourly_distribution"][dt.hour] += 1
            stats["daily_distribution"][dt.weekday()] += 1
        
        stats["unique_songs"] = len(unique_songs)
        stats["unique_artists"] = len(artist_counts)
        
        # Top items
        stats["top_songs"] = song_counts.most_common(10)
        stats["top_artists"] = artist_counts.most_common(10)
        
        # Top stations by play count
        station_list = [(k, v["play_count"]) for k, v in self.data["stations"].items()]
        station_list.sort(key=lambda x: x[1], reverse=True)
        stats["top_stations"] = station_list[:10]
        
        # Recent songs
        stats["recent_songs"] = self.data["songs"][-20:][::-1]  # Last 20, reversed
        
        return stats
    
    def search_songs(self, query: str) -> List[dict]:
        """Search for songs matching query"""
        query = query.lower()
        results = []
        
        for song in self.data["songs"]:
            if (query in song['title'].lower() or 
                query in song['artist'].lower() or 
                query in song.get('album', '').lower()):
                results.append(song)
        
        return results[-100:]  # Return last 100 matches
    
    def get_station_history(self, station: str) -> List[dict]:
        """Get all songs played on a specific station"""
        results = []
        for song in self.data["songs"]:
            if song['station'] == station or f"{song['frequency']:.1f}" in station:
                results.append(song)
        return results[-100:]  # Return last 100


class StatsViewer(QtWidgets.QMainWindow):
    """GUI viewer for song statistics"""
    
    def __init__(self):
        super().__init__()
        self.db = StatsDatabase()
        self.init_ui()
        self.refresh_stats()
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("SDR-Boombox Statistics Viewer")
        self.setMinimumSize(1200, 700)
        
        # Dark theme
        self.setStyleSheet("""
            QMainWindow { background: #1a1a1a; }
            QTabWidget { background: #222; }
            QTabBar::tab { 
                background: #333; 
                color: #ccc; 
                padding: 8px 16px; 
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected { 
                background: #444; 
                color: #fff; 
            }
            QTableWidget { 
                background: #2a2a2a; 
                color: #eee; 
                border: 1px solid #444;
                gridline-color: #333;
            }
            QTableWidget::item { padding: 4px; }
            QTableWidget::item:selected { background: #4a4a4a; }
            QHeaderView::section { 
                background: #333; 
                color: #fff; 
                padding: 6px;
                border: 1px solid #444;
            }
            QLabel { color: #eee; }
            QLabel#title { 
                font-size: 18px; 
                font-weight: bold; 
                color: #7CFC00;
                padding: 10px;
            }
            QLabel#stat { 
                font-size: 14px; 
                padding: 5px;
                background: #2a2a2a;
                border: 1px solid #444;
                border-radius: 4px;
            }
            QPushButton { 
                background: #3a3a3a; 
                color: #eee; 
                border: 1px solid #555; 
                border-radius: 4px; 
                padding: 6px 12px; 
            }
            QPushButton:hover { background: #4a4a4a; }
            QLineEdit { 
                background: #2a2a2a; 
                color: #eee; 
                border: 1px solid #444; 
                padding: 6px;
                border-radius: 4px;
            }
        """)
        
        # Central widget
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)
        
        # Title
        title = QtWidgets.QLabel("📻 SDR-Boombox Statistics", objectName="title")
        title.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(title)
        
        # Stats summary
        self.stats_frame = QtWidgets.QFrame()
        self.stats_frame.setFrameStyle(QtWidgets.QFrame.Box)
        stats_layout = QtWidgets.QHBoxLayout(self.stats_frame)
        
        self.lbl_total = QtWidgets.QLabel("Total Songs: 0", objectName="stat")
        self.lbl_unique = QtWidgets.QLabel("Unique Songs: 0", objectName="stat")
        self.lbl_artists = QtWidgets.QLabel("Artists: 0", objectName="stat")
        self.lbl_stations = QtWidgets.QLabel("Stations: 0", objectName="stat")
        
        stats_layout.addWidget(self.lbl_total)
        stats_layout.addWidget(self.lbl_unique)
        stats_layout.addWidget(self.lbl_artists)
        stats_layout.addWidget(self.lbl_stations)
        stats_layout.addStretch()
        
        layout.addWidget(self.stats_frame)
        
        # Tab widget
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs)
        
        # Recent songs tab
        self.recent_tab = QtWidgets.QWidget()
        self.setup_recent_tab()
        self.tabs.addTab(self.recent_tab, "Recent Songs")
        
        # Top songs tab
        self.top_songs_tab = QtWidgets.QWidget()
        self.setup_top_songs_tab()
        self.tabs.addTab(self.top_songs_tab, "Top Songs")
        
        # Top artists tab
        self.top_artists_tab = QtWidgets.QWidget()
        self.setup_top_artists_tab()
        self.tabs.addTab(self.top_artists_tab, "Top Artists")
        
        # Stations tab
        self.stations_tab = QtWidgets.QWidget()
        self.setup_stations_tab()
        self.tabs.addTab(self.stations_tab, "Stations")
        
        # Search tab
        self.search_tab = QtWidgets.QWidget()
        self.setup_search_tab()
        self.tabs.addTab(self.search_tab, "Search")
        
        # Time analysis tab
        self.time_tab = QtWidgets.QWidget()
        self.setup_time_tab()
        self.tabs.addTab(self.time_tab, "Time Analysis")
        
        # Refresh button
        btn_refresh = QtWidgets.QPushButton("🔄 Refresh Stats")
        btn_refresh.clicked.connect(self.refresh_stats)
        layout.addWidget(btn_refresh)
    
    def setup_recent_tab(self):
        """Setup recent songs tab"""
        layout = QtWidgets.QVBoxLayout(self.recent_tab)
        
        self.recent_table = QtWidgets.QTableWidget()
        self.recent_table.setColumnCount(6)
        self.recent_table.setHorizontalHeaderLabels(
            ["Time", "Title", "Artist", "Album", "Station", "Frequency"]
        )
        self.recent_table.horizontalHeader().setStretchLastSection(True)
        self.recent_table.setAlternatingRowColors(True)
        self.recent_table.setSortingEnabled(True)
        
        layout.addWidget(self.recent_table)
    
    def setup_top_songs_tab(self):
        """Setup top songs tab"""
        layout = QtWidgets.QVBoxLayout(self.top_songs_tab)
        
        self.top_songs_table = QtWidgets.QTableWidget()
        self.top_songs_table.setColumnCount(3)
        self.top_songs_table.setHorizontalHeaderLabels(["Rank", "Song", "Play Count"])
        self.top_songs_table.horizontalHeader().setStretchLastSection(False)
        self.top_songs_table.setColumnWidth(0, 60)
        self.top_songs_table.setColumnWidth(1, 600)
        self.top_songs_table.setAlternatingRowColors(True)
        
        layout.addWidget(self.top_songs_table)
    
    def setup_top_artists_tab(self):
        """Setup top artists tab"""
        layout = QtWidgets.QVBoxLayout(self.top_artists_tab)
        
        self.top_artists_table = QtWidgets.QTableWidget()
        self.top_artists_table.setColumnCount(3)
        self.top_artists_table.setHorizontalHeaderLabels(["Rank", "Artist", "Song Count"])
        self.top_artists_table.horizontalHeader().setStretchLastSection(False)
        self.top_artists_table.setColumnWidth(0, 60)
        self.top_artists_table.setColumnWidth(1, 400)
        self.top_artists_table.setAlternatingRowColors(True)
        
        layout.addWidget(self.top_artists_table)
    
    def setup_stations_tab(self):
        """Setup stations tab"""
        layout = QtWidgets.QVBoxLayout(self.stations_tab)
        
        self.stations_table = QtWidgets.QTableWidget()
        self.stations_table.setColumnCount(5)
        self.stations_table.setHorizontalHeaderLabels(
            ["Station", "Frequency", "Songs Played", "First Seen", "Last Seen"]
        )
        self.stations_table.horizontalHeader().setStretchLastSection(True)
        self.stations_table.setAlternatingRowColors(True)
        self.stations_table.setSortingEnabled(True)
        
        layout.addWidget(self.stations_table)
    
    def setup_search_tab(self):
        """Setup search tab"""
        layout = QtWidgets.QVBoxLayout(self.search_tab)
        
        # Search bar
        search_layout = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("Search for songs, artists, or albums...")
        self.search_input.returnPressed.connect(self.perform_search)
        
        btn_search = QtWidgets.QPushButton("🔍 Search")
        btn_search.clicked.connect(self.perform_search)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(btn_search)
        layout.addLayout(search_layout)
        
        # Results table
        self.search_results = QtWidgets.QTableWidget()
        self.search_results.setColumnCount(6)
        self.search_results.setHorizontalHeaderLabels(
            ["Time", "Title", "Artist", "Album", "Station", "Frequency"]
        )
        self.search_results.horizontalHeader().setStretchLastSection(True)
        self.search_results.setAlternatingRowColors(True)
        
        layout.addWidget(self.search_results)
    
    def setup_time_tab(self):
        """Setup time analysis tab"""
        layout = QtWidgets.QVBoxLayout(self.time_tab)
        
        # Time distribution info
        info_label = QtWidgets.QLabel("📊 Listening patterns by hour and day of week")
        info_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(info_label)
        
        # Hourly distribution
        hourly_label = QtWidgets.QLabel("Hourly Distribution:")
        layout.addWidget(hourly_label)
        
        self.hourly_text = QtWidgets.QTextEdit()
        self.hourly_text.setReadOnly(True)
        self.hourly_text.setMaximumHeight(150)
        layout.addWidget(self.hourly_text)
        
        # Daily distribution
        daily_label = QtWidgets.QLabel("Daily Distribution:")
        layout.addWidget(daily_label)
        
        self.daily_text = QtWidgets.QTextEdit()
        self.daily_text.setReadOnly(True)
        self.daily_text.setMaximumHeight(150)
        layout.addWidget(self.daily_text)
        
        layout.addStretch()
    
    def refresh_stats(self):
        """Refresh all statistics displays"""
        stats = self.db.get_stats()
        
        # Update summary labels
        self.lbl_total.setText(f"Total Songs: {stats['total_songs']:,}")
        self.lbl_unique.setText(f"Unique Songs: {stats['unique_songs']:,}")
        self.lbl_artists.setText(f"Artists: {stats['unique_artists']:,}")
        self.lbl_stations.setText(f"Stations: {stats['stations']}")
        
        # Update recent songs
        self.recent_table.setRowCount(len(stats['recent_songs']))
        for i, song in enumerate(stats['recent_songs']):
            dt = datetime.fromisoformat(song['timestamp'])
            time_str = dt.strftime("%Y-%m-%d %H:%M")
            
            self.recent_table.setItem(i, 0, QtWidgets.QTableWidgetItem(time_str))
            self.recent_table.setItem(i, 1, QtWidgets.QTableWidgetItem(song['title']))
            self.recent_table.setItem(i, 2, QtWidgets.QTableWidgetItem(song['artist']))
            self.recent_table.setItem(i, 3, QtWidgets.QTableWidgetItem(song.get('album', '')))
            self.recent_table.setItem(i, 4, QtWidgets.QTableWidgetItem(song['station']))
            self.recent_table.setItem(i, 5, QtWidgets.QTableWidgetItem(f"{song['frequency']:.1f} MHz"))
        
        # Update top songs
        self.top_songs_table.setRowCount(len(stats['top_songs']))
        for i, (song, count) in enumerate(stats['top_songs']):
            self.top_songs_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.top_songs_table.setItem(i, 1, QtWidgets.QTableWidgetItem(song))
            self.top_songs_table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(count)))
        
        # Update top artists
        self.top_artists_table.setRowCount(len(stats['top_artists']))
        for i, (artist, count) in enumerate(stats['top_artists']):
            self.top_artists_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(i + 1)))
            self.top_artists_table.setItem(i, 1, QtWidgets.QTableWidgetItem(artist))
            self.top_artists_table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(count)))
        
        # Update stations
        self.stations_table.setRowCount(len(self.db.data['stations']))
        for i, (station_key, station_data) in enumerate(self.db.data['stations'].items()):
            self.stations_table.setItem(i, 0, QtWidgets.QTableWidgetItem(station_key))
            self.stations_table.setItem(i, 1, QtWidgets.QTableWidgetItem(f"{station_data['frequency']:.1f} MHz"))
            self.stations_table.setItem(i, 2, QtWidgets.QTableWidgetItem(str(station_data['play_count'])))
            
            first_dt = datetime.fromisoformat(station_data['first_seen'])
            last_dt = datetime.fromisoformat(station_data['last_seen'])
            
            self.stations_table.setItem(i, 3, QtWidgets.QTableWidgetItem(first_dt.strftime("%Y-%m-%d")))
            self.stations_table.setItem(i, 4, QtWidgets.QTableWidgetItem(last_dt.strftime("%Y-%m-%d %H:%M")))
        
        # Update time analysis
        self.update_time_analysis(stats)
    
    def update_time_analysis(self, stats):
        """Update time analysis displays"""
        # Hourly distribution
        hourly_text = "Hour  | Songs Played | Graph\n"
        hourly_text += "-" * 50 + "\n"
        
        max_hourly = max(stats['hourly_distribution'].values()) if stats['hourly_distribution'] else 1
        
        for hour in range(24):
            count = stats['hourly_distribution'].get(hour, 0)
            bar_length = int((count / max_hourly) * 30) if max_hourly > 0 else 0
            bar = "█" * bar_length
            hourly_text += f"{hour:02d}:00 | {count:4d} songs  | {bar}\n"
        
        self.hourly_text.setPlainText(hourly_text)
        
        # Daily distribution
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        daily_text = "Day       | Songs Played | Graph\n"
        daily_text += "-" * 50 + "\n"
        
        max_daily = max(stats['daily_distribution'].values()) if stats['daily_distribution'] else 1
        
        for i, day in enumerate(days):
            count = stats['daily_distribution'].get(i, 0)
            bar_length = int((count / max_daily) * 30) if max_daily > 0 else 0
            bar = "█" * bar_length
            daily_text += f"{day:9s} | {count:4d} songs  | {bar}\n"
        
        self.daily_text.setPlainText(daily_text)
    
    def perform_search(self):
        """Perform search and display results"""
        query = self.search_input.text().strip()
        if not query:
            return
        
        results = self.db.search_songs(query)
        
        self.search_results.setRowCount(len(results))
        for i, song in enumerate(results):
            dt = datetime.fromisoformat(song['timestamp'])
            time_str = dt.strftime("%Y-%m-%d %H:%M")
            
            self.search_results.setItem(i, 0, QtWidgets.QTableWidgetItem(time_str))
            self.search_results.setItem(i, 1, QtWidgets.QTableWidgetItem(song['title']))
            self.search_results.setItem(i, 2, QtWidgets.QTableWidgetItem(song['artist']))
            self.search_results.setItem(i, 3, QtWidgets.QTableWidgetItem(song.get('album', '')))
            self.search_results.setItem(i, 4, QtWidgets.QTableWidgetItem(song['station']))
            self.search_results.setItem(i, 5, QtWidgets.QTableWidgetItem(f"{song['frequency']:.1f} MHz"))


def main():
    """Main entry point for standalone execution"""
    import sys
    app = QtWidgets.QApplication(sys.argv)
    viewer = StatsViewer()
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
