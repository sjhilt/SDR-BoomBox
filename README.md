# SDR-BoomBox

A modern GUI-driven HD Radio (NRSC-5) and analog FM receiver for Software Defined Radios (RTL-SDR). Features automatic HD Radio decoding with seamless fallback to analog FM, live metadata display, album art fetching, station scanning, and preset management.

![SDR-BoomBox In Action!](https://raw.githubusercontent.com/sjhilt/SDR-BoomBox/refs/heads/main/resources/screenshot.png)

![Version](https://img.shields.io/badge/version-1.0.2-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-orange)

## Features

### Core Radio Functionality
- **HD Radio (NRSC-5) Reception**: Decode and play digital HD Radio broadcasts (HD1/Program 0)
- **Automatic Analog Fallback**: Seamlessly switches to wideband FM when HD signal is unavailable (6-second timeout)
- **Frequency Range**: 88.0 - 108.0 MHz with 0.1 MHz precision tuning

### User Interface
- **Retro Digital Display**: LCD-style frequency display with play/pause indicators
- **Dark Theme**: Modern dark interface with custom styling
- **System Tray Integration**: Minimize to system tray with quick access menu
- **Real-time Log Display**: Monitor decoder output and system messages
- **Metadata Display**: Show station name, slogan, song title, artist, and album
- **Album Art**: Automatic artwork fetching from iTunes API based on song metadata

### Advanced Features
- **Preset Management**: 4 preset slots with right-click save/load functionality
- **Persistent Settings**: Presets saved to `~/.sdr_boombox_presets.json`
- **Smart Default Frequency**: Uses preset P0 as default if set, otherwise 98.7 MHz
- **Configurable Parameters**: Gain control, PPM correction, device selection

## System Requirements

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

#### Python Dependencies
```bash
pip install -r requirements.txt
```

Required package:
- `PySide6` - Qt GUI framework for the application interface

## Installation

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

## Usage

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
   - The app will attempt HD Radio first (HD1/Program 0), then fall back to analog FM if needed

### Preset Management

- **Save a preset**: Right-click on any P0-P3 button and select "Save current frequency"
- **Load a preset**: Left-click on a preset button
- **Clear a preset**: Right-click and select "Clear preset"

### System Tray

- The app creates a system tray icon (📻)
- Right-click for options: Show, Hide, Quit
- Useful for keeping the app running in the background

## Configuration

The application uses these default settings (adjustable in code):

```python
# In boombox.py
@dataclass
class Cfg:
    mhz: float = 98.7          # Default frequency (or P0 if set)
    gain: float | None = 28.0  # RTL-SDR gain
    device_index: int | None = None  # Auto-select device
    volume: float = 1.0        # Audio volume
    ppm: int = 5               # Frequency correction
```

### Fallback Behavior

- **Timeout**: 6 seconds to acquire HD Radio sync
- **Auto-fallback**: Enabled by default (checkbox in UI)
- **Manual control**: Uncheck "Auto analog fallback" to stay on HD only

## Technical Details

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

## Troubleshooting

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

### Debug Mode

Monitor the built-in log window for:
- Decoder output
- Synchronization messages
- Error messages
- Signal status

## License

This project is released under the MIT License. See the source code for full license text.

## Acknowledgments

- [nrsc5](https://github.com/theori-io/nrsc5) - HD Radio decoder
- [rtl-sdr](https://osmocom.org/projects/rtl-sdr/) - SDR driver and utilities
- [FFmpeg](https://ffmpeg.org/) - Audio playback
- [PySide6](https://doc.qt.io/qtforpython/) - Qt GUI framework

## Changelog

### Version 1.0.3
- Simplified interface (removed scan and HD program selection)
- HD Radio defaults to HD1/Program 0
- Smart default frequency (uses P0 preset if available)
- Fixed album art persistence when switching stations

### Version 1.0.2
- Added automatic HD to analog FM fallback
- Added preset management system
- System tray integration
- Enhanced metadata display
- Album art fetching from iTunes API

### Version 1.0.0
- Initial release
- Basic HD Radio reception
- Simple GUI interface


## 👤 Author

**@sjhilt**
- GitHub: [https://github.com/sjhilt/SDR-BoomBox](https://github.com/sjhilt/SDR-BoomBox)



Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/sjhilt/SDR-BoomBox/issues).

## ⭐ Show your support

Give a ⭐️ if this project helped you enjoy HD Radio and FM broadcasts!
