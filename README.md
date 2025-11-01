# SDR-BoomBox

A modern GUI-driven HD Radio (NRSC-5) and analog FM receiver for Software Defined Radios (RTL-SDR). Features automatic HD Radio decoding with seamless fallback to analog FM, live metadata display, album art fetching, station scanning, and preset management.

![SDR-BoomBox In Action!](https://raw.githubusercontent.com/sjhilt/SDR-BoomBox/refs/heads/main/resources/screenshot.png)

![Version](https://img.shields.io/badge/version-1.0.5-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)
![License](https://img.shields.io/badge/license-MIT-orange)

## Features

### Core Radio Functionality
- **HD Radio (NRSC-5) Reception**: Decode and play digital HD Radio broadcasts with HD1/HD2/HD3/HD4 channel selection
- **HD Channel Selection**: Toggle between different HD subchannels (HD1, HD2, HD3, HD4) for stations that broadcast multiple programs
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
- **Song Statistics Tracking**: Automatic logging of played songs with comprehensive analytics
- **Traffic & Weather Maps**: Real-time traffic and weather radar maps from HD Radio data services
- **Winamp-Style Visualizer**: Animated spectrum analyzer when no album art is available
- **Station Logo Display**: Automatic station logo watermark on album art
- **Sleep Prevention**: Keeps computer awake while playing radio
- **Smart Cleanup**: Automatic cleanup of cached files after every 3 songs

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
   - The app will attempt HD Radio first, then fall back to analog FM if needed
   - Select HD channel (HD1-HD4) using the dropdown menu to access different programs on the same frequency

### Preset Management

- **Save a preset**: Right-click on any P0-P3 button and select "Save current frequency" (includes HD channel selection)
- **Load a preset**: Left-click on a preset button (restores both frequency and HD channel)
- **Clear a preset**: Right-click and select "Clear preset"

### Song Statistics

**View your listening history and analytics**:
```bash
python boombox.py --stats
```

The statistics viewer shows:
- **Recent Songs**: Last 20 songs played with timestamps
- **Top Songs**: Most frequently played songs
- **Top Artists**: Artists ranked by play count
- **Stations**: All stations you've listened to
- **Search**: Find songs in your history
- **Time Analysis**: Hourly and daily listening patterns

Songs are automatically logged while using the app. Station IDs and commercials are filtered out.

### Traffic & Weather Maps

![Traffic and Weather Map](https://raw.githubusercontent.com/sjhilt/SDR-BoomBox/refs/heads/main/resources/map_traffic_weather.png)

- Click the **Map** button to open the traffic and weather viewer
- Maps are automatically assembled from HD Radio data services
- Traffic data shows real-time road conditions
- Weather radar overlay displays precipitation
- Maps update automatically when new data is broadcast

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
    gain: float | None = 40.0  # RTL-SDR gain
    device_index: int | None = None  # Auto-select device
    volume: float = 1.0        # Audio volume
    ppm: int = 5               # Frequency correction
    hd_program: int = 0        # HD channel (0=HD1, 1=HD2, 2=HD3, 3=HD4)
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

### Version 1.0.5
- Added comprehensive song statistics tracking system
- New statistics viewer GUI (`python boombox.py --stats`)
- Automatic song logging with metadata (title, artist, album, station)
- Traffic and weather map display from HD Radio data services
- Winamp-style spectrum analyzer visualization
- Station logo watermark display
- Sleep prevention while playing
- Smart cleanup after every 3 songs
- Hide/show log toggle button
- Various UI improvements and bug fixes

### Version 1.0.4
- Added HD channel selection (HD1, HD2, HD3, HD4) with dropdown menu
- HD channel selection is saved with presets
- LCD display shows current HD channel
- Automatic restart when switching HD channels during playback

### Version 1.0.3
- Simplified interface (removed scan feature)
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
