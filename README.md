# Boombox – HD Radio GUI

Boombox is a Python-based graphical interface that emulates a retro boombox while tuning and playing HD Radio broadcasts using the `nrsc5` decoder. It provides live audio playback, station metadata, and automatic album art lookup using publicly available music search APIs.

## Features

- HD Radio tuning using the `nrsc5` command-line decoder
- Playback via FFmpeg (`ffplay`)
- Retro-style interface using PySide6 or compatible Qt bindings
- Song metadata parsing (Title, Artist, Album)
- Automatic online lookup of album art based on metadata
- Basic HD Radio scan functionality

## Requirements

### System Requirements

- A working installation of the `nrsc5` binary
- FFmpeg with `ffplay` available in PATH
- An RTL-SDR device (or compatible input file)
- Python 3.9 or newer

### Python Dependencies

Install with:

```
pip install -r requirements.txt
```

## Usage

To run the application:

```
python boombox.py
```

Once launched:

- Use the frequency slider to tune between 88.0 MHz and 108.0 MHz
- Select HD Radio program channels (0–3)
- Click Play to start tuning and audio playback
- Metadata will appear automatically if broadcast by the station
- Album art will be fetched online if not embedded

## Album Art Lookup

If the HD Radio broadcast does not provide embedded artwork, the application performs a background search using public music metadata APIs based on the current song title and artist. Artwork is cached to minimize repeated lookups.

## Roadmap

- Integrate native `nrsc5` Python API for embedded metadata and artwork
- Add signal metrics (MER, BER, bitrate)
- Implement presets and station favorites
- Package into standalone executable

## License

This project is released under the MIT License.
