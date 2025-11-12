"""
Visualizer module for SDR-Boombox
Winamp-style spectrum analyzer visualization
"""

import random
from PySide6 import QtCore, QtGui, QtWidgets


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
