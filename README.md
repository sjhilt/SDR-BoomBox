# SDR-BoomBox 📻

A modern GUI-driven HD Radio (NRSC-5) and analog FM receiver for Software Defined Radios (RTL-SDR). Features automatic HD Radio decoding with seamless fallback to analog FM, live metadata display, album art fetching, station scanning, and preset management.

![Version](https://img.shields.io/badge/version-1.0.2-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-orange)

## ✨ Features

### Core Radio Functionality
- **HD Radio (NRSC-5) Reception**: Decode and play digital HD Radio broadcasts
- **Automatic Analog Fallback**: Seamlessly switches to wideband FM when HD signal is unavailable (6-second timeout)
- **Frequency Range**: 88.0 - 108.0 MHz with 0.1 MHz precision tuning
- **HD Program Selection**: Support for HD1, HD2, HD3, and HD4 subchannels

### User Interface
- **Retro Digital Display**: LCD-style frequency display with play/pause indicators
- **Dark Theme**: Modern dark interface with custom styling
- **System Tray Integration**: Minimize to system tray with quick access menu
- **Real-time Log Display**: Monitor decoder output and system messages
- **Metadata Display**: Show station name, slogan, song title, artist, and album
- **Album Art**: Automatic artwork fetching from iTunes API based on song metadata

### Advanced Features
- **Station Scanning**: Full-band scan (88-108 MHz) using RTL-Power with peak detection
- **Preset Management**: 4 preset slots with right-click save/load functionality
- **Persistent Settings**: Presets saved to `~/.sdr_boombox_presets.json`
- **Configurable Parameters**: Gain control, PPM correction, device selection

## 🔧 System Requirements

### Hardware
- RTL-SDR dongle (or compatible SDR device)
- Computer with USB port
- Antenna suitable for FM reception

### Software Dependencies

#### Required Command-Line Tools
- **`nrsc5`**: HD Radio decoder ([GitHub](https://github.com/theori-io/nrsc5))
- **`ffplay`**: Audio playback (part of FFmpeg)

#### Optional Tools (for full functionality)
- **`rtl_fm`**: Analog FM demodulation (for fallback mode)
- **`rtl_power`**: Spectrum scanning (for station scan feature)

#### Python Dependencies
```bash
pip install PySide6
```

## 📦 Installation

1. **Install RTL-SDR drivers**:
   ```bash
   # macOS (using Homebrew)
   brew install rtl-sdr
   
   # Linux (Debian/Ubuntu)
   sudo apt-get install rtl-sdr
   
   # Windows
   # Download and install from https://osmocom.org/projects/rtl-sdr/
   ```

2. **Install nrsc5**:
   ```bash
   # Build from source
   git clone https://github.com/theori-io/nrsc5.git
   cd nrsc5
   mkdir build && cd build
   cmake ..
   make
   sudo make install
   ```

3. **Install FFmpeg** (for ffplay):
   ```bash
   # macOS
   brew install ffmpeg
   
   # Linux
   sudo apt-get install ffmpeg
   
   # Windows
   # Download from https://ffmpeg.org/download.html
   ```

4. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

## 🚀 Usage

### Basic Operation

1. **Launch the application**:
   ```bash
   python boombox.py
   ```

2. **Tune to a station**:
   - Use the frequency slider to select a station (88.0 - 108.0 MHz)
   - Or click a preset button to jump to a saved frequency

3. **Start playback**:
   - Click the "▶ Play" button
   - The app will attempt HD Radio first, then fall back to analog FM if needed

4. **HD Radio programs**:
   - Use the HD Program dropdown to select subchannels (0-3)
   - Program 0 is typically the main channel (HD1)

### Preset Management

- **Save a preset**: Right-click on any P0-P3 button and select "Save current frequency"
- **Load a preset**: Left-click on a preset button
- **Clear a preset**: Right-click and select "Clear preset"

### Station Scanning

1. Click the "🔎 Scan (88–108 MHz)" button
2. Wait for the scan to complete (uses rtl_power)
3. Double-click a station in the results to tune to it

### System Tray

- The app creates a system tray icon (📻)
- Right-click for options: Show, Hide, Quit
- Useful for keeping the app running in the background

## ⚙️ Configuration

The application uses these default settings (adjustable in code):

```python
# In boombox.py
@dataclass
class Cfg:
    mhz: float = 105.5         # Default frequency
    hd_prog: int = 0           # HD program (0-3)
    gain: float | None = 28.0  # RTL-SDR gain
    device_index: int | None = None  # Auto-select device
    volume: float = 1.0        # Audio volume
    ppm: int = 5               # Frequency correction
```

### Fallback Behavior

- **Timeout**: 6 seconds to acquire HD Radio sync
- **Auto-fallback**: Enabled by default (checkbox in UI)
- **Manual control**: Uncheck "Auto analog fallback" to stay on HD only

## 🎨 Technical Details

### Audio Pipeline

**HD Radio Mode**:
```
RTL-SDR → nrsc5 → stdout (PCM) → ffplay
```

**Analog FM Mode**:
```
RTL-SDR → rtl_fm (WBFM) → stdout (S16LE) → ffplay
```

### Metadata Parsing

The app parses nrsc5 stderr output for:
- Station name
- Station slogan  
- Song title
- Artist name
- Album name
- Synchronization status

### Album Art Fetching

When song metadata is detected:
1. Queries iTunes Search API with artist + title
2. Downloads 300x300 artwork if available
3. Falls back to radio emoji (📻) if not found
4. Caches results to avoid duplicate requests

### Scanning Algorithm

1. Uses `rtl_power` to sweep 88-108 MHz
2. Applies 7-point moving average smoothing
3. Detects peaks above threshold (max - 6 dB)
4. Snaps frequencies to 0.1 MHz grid
5. Returns sorted list by signal strength

## 🐛 Troubleshooting

### Common Issues

**"nrsc5 not found in PATH"**
- Ensure nrsc5 is installed and in your system PATH
- Try specifying full path in the code

**No audio output**
- Check that ffplay is installed
- Verify audio device is working
- Check system volume settings

**"No HD sync, switching to analog FM"**
- Normal behavior for non-HD stations
- May indicate weak signal - adjust antenna
- Try increasing gain value

**Scan finds no stations**
- Ensure rtl_power is installed
- Check antenna connection
- Try manual tuning first to verify reception

### Debug Mode

Monitor the built-in log window for:
- Decoder output
- Synchronization messages
- Error messages
- Signal status

## 📄 License

This project is released under the MIT License. See the source code for full license text.

## 🙏 Acknowledgments

- [nrsc5](https://github.com/theori-io/nrsc5) - HD Radio decoder
- [rtl-sdr](https://osmocom.org/projects/rtl-sdr/) - SDR driver and utilities
- [FFmpeg](https://ffmpeg.org/) - Audio playback
- [PySide6](https://doc.qt.io/qtforpython/) - Qt GUI framework

## 📝 Changelog

### Version 1.0.2
- Added automatic HD to analog FM fallback
- Implemented station scanning with rtl_power
- Added preset management system
- System tray integration
- Enhanced metadata display
- Album art fetching from iTunes API

### Version 1.0.0
- Initial release
- Basic HD Radio reception
- Simple GUI interface

## 🚧 Roadmap

- [ ] Signal strength indicators (MER, BER)
- [ ] Recording functionality
- [ ] RDS decoding for analog FM
- [ ] Spectrum waterfall display
- [ ] More preset slots
- [ ] Keyboard shortcuts
- [ ] Settings dialog for configuration
- [ ] Cross-platform installer packages

## 👤 Author

**@sjhilt**
- GitHub: [https://github.com/sjhilt/SDR-BoomBox](https://github.com/sjhilt/SDR-BoomBox)

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/sjhilt/SDR-BoomBox/issues).

## ⭐ Show your support

Give a ⭐️ if this project helped you enjoy HD Radio and FM broadcasts!
