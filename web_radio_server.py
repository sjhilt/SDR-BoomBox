"""
RTL-SDR web radio backend.

This module provides:
- a FastAPI web server for the browser UI
- radio control endpoints for HD Radio and analog FM
- a browser-friendly WAV audio stream
- lightweight metadata/art extraction from NRSC-5 log output and LOT files

The goal is to let an RTL-SDR stay attached to one machine while users
control tuning and listen remotely from a web browser.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# This web server is generic to SDR-BoomBox and does not depend on the
# Rock1037-specific iHeart modules from the side project fork.

# Project-local paths for bundled binaries, static files, and LOT data handling.
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "webui"
LOCAL_NRSC5 = BASE_DIR / "nrsc5.exe"
LOCAL_RTL_FM = BASE_DIR / "rtl_fm.exe"
LOCAL_RTL_TCP = BASE_DIR / "rtl_tcp.exe"
LOT_DIR = Path.home() / '.sdr_boombox_data'

# Regular expressions used to extract metadata from decoder log output.
TITLE_RE = re.compile(r"\bTitle:\s*(.+)", re.IGNORECASE)
ARTIST_RE = re.compile(r"\bArtist:\s*(.+)", re.IGNORECASE)
ALBUM_RE = re.compile(r"\bAlbum:\s*(.+)", re.IGNORECASE)
STATION_RE = re.compile(r"\bStation name:\s*(.+)", re.IGNORECASE)
SLOGAN_RE = re.compile(r"\bSlogan:\s*(.+)", re.IGNORECASE)
LOT_RE = re.compile(r"LOT file:.*?port=(\d+).*?name=([^\s]+)", re.IGNORECASE)
TMT_RE = re.compile(r"name=(TMT_[^\s]+\.png)", re.IGNORECASE)
DWRO_RE = re.compile(r"name=(DWRO_[^\s]+\.png)", re.IGNORECASE)
DWRI_RE = re.compile(r"name=(DWRI_[^\s]+)", re.IGNORECASE)


def find_local_exe(path: Path) -> str | None:
    """Return the local executable path if the bundled file exists."""
    return str(path) if path.exists() else None

def build_process_env() -> dict[str, str]:
    """Build an environment that prefers the project-local SDR binaries and DLLs."""
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    env["PATH"] = f"{BASE_DIR}{os.pathsep}{current_path}" if current_path else str(BASE_DIR)
    return env


def wav_header(sample_rate: int = 48000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """Create a streaming WAV header for browser playback."""
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = 0xFFFFFFFF
    riff_size = 36 + data_size
    return b"".join([
        b"RIFF", (riff_size & 0xFFFFFFFF).to_bytes(4, "little"), b"WAVE",
        b"fmt ", (16).to_bytes(4, "little"), (1).to_bytes(2, "little"),
        channels.to_bytes(2, "little"), sample_rate.to_bytes(4, "little"),
        byte_rate.to_bytes(4, "little"), block_align.to_bytes(2, "little"),
        bits_per_sample.to_bytes(2, "little"), b"data",
        (data_size & 0xFFFFFFFF).to_bytes(4, "little"),
    ])


def to_data_url(raw: bytes, mime: str) -> str:
    """Convert raw image bytes into a data URL the browser can render directly."""
    import base64
    return f"data:{mime};base64," + base64.b64encode(raw).decode('ascii')


@dataclass
class RadioMetadata:
    """Current now-playing metadata exposed to the web UI."""
    title: str = ""
    artist: str = ""
    album: str = ""
    station: str = ""
    slogan: str = ""
    art_url: str = ""
    source: str = ""
    updated_at: float | None = None


@dataclass
class RadioStatus:
    """Live receiver/process status exposed through the API."""
    running: bool = False
    mode: Literal['hd', 'fm', 'stopped'] = 'stopped'
    frequency_mhz: float = 103.7
    hd_program: int = 0
    gain: float = 40.0
    ppm: int = 5
    device_index: int = 0
    use_rtltcp: bool = False
    rtltcp_host: str = '127.0.0.1'
    started_at: float | None = None
    bytes_sent: int = 0
    listeners: int = 0
    last_error: str = ''
    last_log: str = 'Idle'




@dataclass
class MapState:
    """Latest traffic/weather map state exposed to the web UI."""
    traffic_tiles: list[dict] | None = None
    weather_overlay_url: str = ''
    weather_info_file: str = ''
    weather_location: dict | None = None
    last_updated: float | None = None


class MapManager:
    """Track HD Radio traffic/weather assets in LOT files for the web UI."""

    def __init__(self, logger):
        self._logger = logger
        self._lock = threading.RLock()
        self._state = MapState(traffic_tiles=[])

    def reset(self) -> None:
        with self._lock:
            self._state = MapState(traffic_tiles=[])

    def get(self) -> MapState:
        with self._lock:
            state = asdict(self._state)
        return MapState(**state)

    def update_from_line(self, line: str) -> None:
        if 'TMT_' in line and '.png' in line:
            match = TMT_RE.search(line)
            if match:
                self._handle_traffic_tile(match.group(1))
        elif 'DWRI_' in line:
            match = DWRI_RE.search(line)
            if match:
                self._handle_weather_info(match.group(1))
        elif 'DWRO_' in line and '.png' in line:
            match = DWRO_RE.search(line)
            if match:
                self._handle_weather_overlay(match.group(1))

    def _resolve_lot_file(self, name: str) -> Path | None:
        candidates = [LOT_DIR / name] + list(LOT_DIR.glob(f'*_{name}'))
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    def _handle_traffic_tile(self, name: str) -> None:
        path = self._resolve_lot_file(name)
        if not path:
            return
        clean_name = name[name.index('TMT_'):] if '_TMT_' in name else name
        parts = clean_name.split('_')
        if len(parts) < 6:
            return
        try:
            row = int(parts[2])
            col = int(parts[3])
            timestamp = f'{parts[4]}_{parts[5]}'
        except Exception:
            return
        tile = {
            'row': row,
            'col': col,
            'timestamp': timestamp,
            'name': path.name,
            'url': f'/lot/{path.name}',
        }
        with self._lock:
            existing = [t for t in (self._state.traffic_tiles or []) if t.get('timestamp') == timestamp]
            existing = [t for t in existing if not (t['row'] == row and t['col'] == col)]
            existing.append(tile)
            existing.sort(key=lambda t: (t['row'], t['col']))
            self._state.traffic_tiles = existing
            self._state.last_updated = time.time()
        self._logger(f'[map] traffic tile {row},{col} received')

    def _handle_weather_overlay(self, name: str) -> None:
        path = self._resolve_lot_file(name)
        if not path:
            return
        with self._lock:
            self._state.weather_overlay_url = f'/lot/{path.name}'
            self._state.last_updated = time.time()
        self._logger(f'[weather] overlay received: {path.name}')

    def _handle_weather_info(self, name: str) -> None:
        path = self._resolve_lot_file(name)
        if not path:
            return
        location = None
        try:
            content = path.read_text(encoding='utf-8', errors='ignore')
            coords_matches = re.findall(r'\((-?\d+\.?\d*),(-?\d+\.?\d*)\)', content)
            if len(coords_matches) >= 2:
                lat1, lon1 = float(coords_matches[0][0]), float(coords_matches[0][1])
                lat2, lon2 = float(coords_matches[1][0]), float(coords_matches[1][1])
                location = {
                    'lat': round((lat1 + lat2) / 2, 4),
                    'lon': round((lon1 + lon2) / 2, 4),
                }
            elif coords_matches:
                location = {
                    'lat': float(coords_matches[0][0]),
                    'lon': float(coords_matches[0][1]),
                }
        except Exception:
            location = None
        with self._lock:
            self._state.weather_info_file = path.name
            self._state.weather_location = location
            self._state.last_updated = time.time()
        self._logger(f'[weather] info received: {path.name}')

class TuneRequest(BaseModel):
    """Validated tune request sent by the browser when the user clicks Tune."""
    frequency_mhz: float = Field(..., ge=64.0, le=108.0)
    mode: Literal['hd', 'fm'] = 'hd'
    hd_program: int = Field(0, ge=0, le=3)
    gain: float = 40.0
    ppm: int = 5
    device_index: int = 0
    use_rtltcp: bool = False
    rtltcp_host: str = '127.0.0.1'


class MetadataManager:
    """Parse radio logs and decide what metadata/art the web UI should show."""
    def __init__(self, logger):
        self._lock = threading.RLock()
        self._metadata = RadioMetadata()
        self._logger = logger
        self._song_key = ''
        self._freq = 98.7
        self._hd_program = 0

    def reset(self, freq: float, hd_program: int) -> None:
        """Clear metadata state when the user changes station or mode."""
        with self._lock:
            self._metadata = RadioMetadata()
            self._song_key = ''
            self._freq = freq
            self._hd_program = hd_program

    def get(self) -> RadioMetadata:
        """Return a thread-safe snapshot of the current metadata."""
        with self._lock:
            return RadioMetadata(**asdict(self._metadata))

    def update_from_line(self, line: str) -> None:
        """Inspect one decoder log line for title/artist/station/art updates."""
        changed = False
        with self._lock:
            for regex, field in ((TITLE_RE, 'title'), (ARTIST_RE, 'artist'), (ALBUM_RE, 'album'), (STATION_RE, 'station'), (SLOGAN_RE, 'slogan')):
                m = regex.search(line)
                if m:
                    value = m.group(1).strip()
                    if value and getattr(self._metadata, field) != value:
                        setattr(self._metadata, field, value)
                        self._metadata.updated_at = time.time()
                        changed = True

            lot = LOT_RE.search(line)
            if lot:
                port, name = lot.groups()
                art = self._read_lot_art(name, port)
                if art and art != self._metadata.art_url:
                    self._metadata.art_url = art
                    self._metadata.source = 'LOT'
                    self._metadata.updated_at = time.time()
                    changed = True

            song_key = f"{self._metadata.artist}||{self._metadata.title}"
            should_resolve = bool(
                self._metadata.title
                and self._metadata.artist
                and not self._looks_like_station(self._metadata.title)
                and not self._looks_like_station(self._metadata.artist)
                and song_key != self._song_key
            )
            if should_resolve:
                self._song_key = song_key

        if should_resolve:
            threading.Thread(target=self._resolve_art_and_metadata, args=(song_key,), daemon=True).start()
        elif changed:
            self._logger('[meta] metadata updated')

    def _expected_art_ports(self) -> list[str]:
        """Return the NRSC-5 LOT ports that typically contain album art for the active HD program."""
        return {
            0: ['0810', '0010'],
            1: ['1810', '0011'],
            2: ['5103', '0012'],
            3: ['5104', '0013'],
        }.get(self._hd_program, ['0810', '0010'])

    def _should_ignore_lot_file(self, lot_file: str, port: str) -> bool:
        """Filter out traffic, weather, logos, and other non-album-art LOT images."""
        lower = lot_file.lower()
        if any(x in lot_file for x in ['TMT_', 'DWRO_']):
            return True
        if '$$' in lot_file or 'SLWRXR' in lot_file or '_logo' in lower:
            return True
        if self._hd_program == 0 and port == '5103':
            return True
        if port not in self._expected_art_ports():
            return True
        return False

    def _read_lot_art(self, lot_file: str, port: str) -> str:
        """Load a valid LOT image and return it as a browser-ready data URL."""
        if self._should_ignore_lot_file(lot_file, port):
            return ''
        candidates = [LOT_DIR / lot_file] + list(LOT_DIR.glob(f'*_{lot_file}'))
        for cand in candidates:
            if cand.exists() and cand.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.bmp'}:
                try:
                    raw = cand.read_bytes()
                    mime = 'image/png' if cand.suffix.lower() == '.png' else 'image/jpeg'
                    return to_data_url(raw, mime)
                except Exception:
                    return ''
        return ''

    def _resolve_art_and_metadata(self, expected_song_key: str) -> None:
        """Try generic fallback art lookup once a real song title and artist are known."""
        md = self.get()
        art_url = self._lookup_itunes_art(md.artist, md.title)
        source = 'iTunes' if art_url else (md.source or 'HD Radio')

        with self._lock:
            current_song_key = f"{self._metadata.artist}||{self._metadata.title}"
            if current_song_key != expected_song_key:
                return
            if art_url:
                self._metadata.art_url = art_url
            self._metadata.source = source
            self._metadata.updated_at = time.time()
        self._logger(f'[meta] now playing: {md.artist} - {md.title}')

    def _lookup_itunes_art(self, artist: str, title: str) -> str:
        """Use the iTunes search API as a last-resort album art lookup."""
        if self._looks_like_station(artist) or self._looks_like_station(title):
            return ''
        try:
            q = quote_plus(f'{artist} {title}')
            req = Request(f'https://itunes.apple.com/search?term={q}&entity=song&limit=1', headers={'User-Agent': 'RTL-SDR Web Radio'})
            with urlopen(req, timeout=5) as r:
                data = json.loads(r.read().decode('utf-8', 'ignore'))
            results = data.get('results') or []
            if results:
                return (results[0].get('artworkUrl100') or '').replace('100x100bb.jpg', '600x600bb.jpg')
        except Exception:
            return ''
        return ''

    def _looks_like_station(self, text: str) -> bool:
        """Heuristic to avoid treating station IDs, overlays, or promos as songs."""
        if not text:
            return False
        t = text.lower()
        bad_phrases = [
            'commercial', 'advertisement', 'promo', 'jingle', 'weather', 'traffic',
            'coming up', "you're listening", 'stay tuned', 'call us', 'text us', 'win',
            'contest', 'hd1', 'hd2', 'hd3', 'hd4', 'station id', 'station identification',
            '#1', 'us-', 'us101', 'us 101'
        ]
        if any(phrase in t for phrase in bad_phrases):
            return True
        station_patterns = [
            r'^w[a-z]{2,3}\s+',
            r'^k[a-z]{2,3}\s+',
            r'^(kiss|rock|country|hits|classic|news|talk)\s*(fm|am)?$',
            r'^\d{2,3}\.\d\s*(fm|am)?$',
            r"chattanooga'?s?\s+(rock|country|hits|classic)\s+station",
            r'^(rock|kiss|country|hits|classic)\s+\d{2,3}\.\d$',
            r'^\w{3,4}\s+\w{2,3}-?\d{2,3}',
            r'^us-?\d{2,3}',
            r'^\w{3,4}\s+\d{2,3}\.\d'
        ]
        return any(re.search(pattern, t) for pattern in station_patterns)


class RadioController:
    """Own the decoder process, audio fan-out, logs, and live receiver state."""
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._listeners: dict[int, asyncio.Queue[bytes | None]] = {}
        self._listener_seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._header_bytes = b''
        self._status = RadioStatus()
        self._recent_logs: list[str] = []
        self._metadata = MetadataManager(self._log)
        self._maps = MapManager(self._log)

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the FastAPI event loop so background threads can push audio to listeners."""
        self._loop = loop

    def _log(self, line: str) -> None:
        """Append a timestamped log entry for the UI log panel."""
        ts = time.strftime('%H:%M:%S')
        entry = f'{ts} {line}'
        with self._lock:
            self._status.last_log = entry
            self._recent_logs.append(entry)
            self._recent_logs = self._recent_logs[-250:]

    def get_logs(self) -> list[str]:
        """Return recent log lines for the frontend log viewer."""
        with self._lock:
            return list(self._recent_logs)

    def get_status(self) -> RadioStatus:
        """Return a thread-safe snapshot of receiver status."""
        with self._lock:
            s = asdict(self._status)
        return RadioStatus(**s)

    def get_metadata(self) -> RadioMetadata:
        """Return a snapshot of current now-playing metadata."""
        return self._metadata.get()

    def get_maps(self) -> MapState:
        """Return the latest traffic/weather assets for the web UI."""
        return self._maps.get()

    def _broadcast(self, chunk: bytes | None) -> None:
        """Push one audio chunk to every connected browser listener."""
        if not self._loop:
            return
        for queue in list(self._listeners.values()):
            self._loop.call_soon_threadsafe(queue.put_nowait, chunk)

    def _set_listener_count(self) -> None:
        """Update the status object with the current number of browser listeners."""
        with self._lock:
            self._status.listeners = len(self._listeners)

    def _build_nrsc5_cmd(self, req: TuneRequest) -> list[str]:
        """Build the HD Radio decoder command line."""
        exe = find_local_exe(LOCAL_NRSC5) or 'nrsc5'
        cmd = [exe]
        if req.use_rtltcp:
            cmd += ['-H', req.rtltcp_host]
        else:
            cmd += ['-d', str(req.device_index)]
        cmd += ['-p', str(req.ppm), '-g', str(req.gain), '--dump-aas-files', str(LOT_DIR), '-t', 'wav', '-o', '-']
        cmd += [str(req.frequency_mhz), str(req.hd_program)]
        return cmd

    def _build_rtl_fm_cmd(self, req: TuneRequest) -> list[str]:
        """Build the analog FM decoder command line."""
        exe = find_local_exe(LOCAL_RTL_FM) or 'rtl_fm'
        cmd = [exe, '-M', 'wbfm', '-f', f'{req.frequency_mhz}M', '-s', '200k', '-r', '48k', '-E', 'deemp=75', '-g', str(req.gain), '-p', f'{int(req.ppm):+d}']
        if not req.use_rtltcp:
            cmd += ['-d', str(req.device_index)]
        cmd += ['-']
        return cmd

    def stop(self) -> None:
        """Stop the active decoder process and disconnect all listeners."""
        with self._lock:
            proc = self._proc
            self._proc = None
            self._header_bytes = b''
            self._status.running = False
            self._status.mode = 'stopped'
            self._status.started_at = None
        if proc is not None:
            with contextlib.suppress(Exception):
                if proc.stdout: proc.stdout.close()
            with contextlib.suppress(Exception):
                if proc.stderr: proc.stderr.close()
            with contextlib.suppress(Exception):
                proc.terminate()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    proc.kill()
        self._broadcast(None)
        self._log('[radio] stopped')

    def tune(self, req: TuneRequest) -> RadioStatus:
        """Start a fresh decoder process for the requested station and mode."""
        with self._lock:
            self._status.last_error = ''
        self.stop()
        self._metadata.reset(req.frequency_mhz, req.hd_program)
        self._maps.reset()
        LOT_DIR.mkdir(exist_ok=True)
        cmd = self._build_nrsc5_cmd(req) if req.mode == 'hd' else self._build_rtl_fm_cmd(req)
        self._log(f'[radio] starting {req.mode.upper()} {req.frequency_mhz:.1f} MHz')
        self._log('[radio] command: ' + ' '.join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                cwd=str(BASE_DIR),
                env=build_process_env(),
            )
        except FileNotFoundError as exc:
            with self._lock:
                self._status.last_error = str(exc)
            raise HTTPException(status_code=500, detail=f'Missing executable: {exc}') from exc
        except Exception as exc:
            with self._lock:
                self._status.last_error = str(exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        with self._lock:
            self._proc = proc
            self._status.running = True
            self._status.mode = req.mode
            self._status.frequency_mhz = req.frequency_mhz
            self._status.hd_program = req.hd_program
            self._status.gain = req.gain
            self._status.ppm = req.ppm
            self._status.device_index = req.device_index
            self._status.use_rtltcp = req.use_rtltcp
            self._status.rtltcp_host = req.rtltcp_host
            self._status.started_at = time.time()
            self._status.bytes_sent = 0
            self._header_bytes = b'' if req.mode == 'hd' else wav_header()
        self._reader_thread = threading.Thread(target=self._reader_loop, args=(req.mode, proc), daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, args=(proc,), daemon=True)
        self._reader_thread.start()
        self._stderr_thread.start()
        return self.get_status()

    def _reader_loop(self, mode: str, proc: subprocess.Popen) -> None:
        """Read decoder audio bytes and fan them out to the browser stream."""
        sent_header = False
        try:
            while True:
                if proc.stdout is None:
                    break
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                if mode == 'hd' and not sent_header:
                    if not self._header_bytes:
                        self._header_bytes += chunk
                        if len(self._header_bytes) < 44:
                            continue
                        chunk = self._header_bytes
                    sent_header = True
                if mode == 'fm' and not sent_header:
                    self._broadcast(self._header_bytes)
                    sent_header = True
                with self._lock:
                    self._status.bytes_sent += len(chunk)
                self._broadcast(chunk)
        except Exception as exc:
            with self._lock:
                self._status.last_error = str(exc)
            self._log(f'[radio] audio reader error: {exc}')
        finally:
            with self._lock:
                was_running = self._status.running
            if was_running:
                self.stop()

    def _stderr_loop(self, proc: subprocess.Popen) -> None:
        """Read decoder stderr for metadata extraction and troubleshooting logs."""
        try:
            if proc.stderr is None:
                return
            for raw_line in iter(proc.stderr.readline, b''):
                if not raw_line:
                    break
                line = raw_line.decode('utf-8', 'ignore').strip()
                if not line:
                    continue
                self._log(line)
                self._metadata.update_from_line(line)
                self._maps.update_from_line(line)
            rc = proc.poll()
            if rc not in (None, 0):
                self._log(f'[radio] process exited with code {rc}')
        except Exception as exc:
            self._log(f'[radio] stderr reader error: {exc}')

    async def open_listener(self):
        """Create one async generator for a browser audio client."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=128)
        with self._lock:
            listener_id = self._listener_seq
            self._listener_seq += 1
            self._listeners[listener_id] = queue
            header = self._header_bytes
        self._set_listener_count()

        async def gen():
            try:
                if header:
                    yield header
                while True:
                    chunk = await queue.get()
                    if chunk is None:
                        break
                    yield chunk
            finally:
                with self._lock:
                    self._listeners.pop(listener_id, None)
                self._set_listener_count()

        return gen()


# FastAPI application instance and shared radio controller.
controller = RadioController()


@asynccontextmanager
async def lifespan(app: FastAPI):
    controller.attach_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        controller.stop()


app = FastAPI(title='RTL-SDR Web Radio', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


# Static frontend assets.
@app.get('/')
async def root() -> FileResponse:
    return FileResponse(WEB_DIR / 'index.html')


@app.get('/app.js')
async def app_js() -> FileResponse:
    return FileResponse(WEB_DIR / 'app.js', media_type='application/javascript', headers={'Cache-Control': 'no-cache, no-store, must-revalidate'})


@app.get('/style.css')
async def style_css() -> FileResponse:
    return FileResponse(WEB_DIR / 'style.css', media_type='text/css', headers={'Cache-Control': 'no-cache, no-store, must-revalidate'})


@app.get('/lot/{filename:path}')
async def lot_file(filename: str) -> FileResponse:
    file_path = LOT_DIR / filename
    if not file_path.exists():
        prefixed = list(LOT_DIR.glob(f'*_{filename}'))
        if prefixed:
            file_path = prefixed[0]
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='LOT file not found')
    return FileResponse(file_path)


# JSON API used by the browser frontend.
@app.get('/api/status')
async def api_status() -> JSONResponse:
    return JSONResponse(asdict(controller.get_status()))


@app.get('/api/metadata')
async def api_metadata() -> JSONResponse:
    return JSONResponse(asdict(controller.get_metadata()))


@app.get('/api/logs')
async def api_logs() -> JSONResponse:
    return JSONResponse({'logs': controller.get_logs()})


@app.get('/api/maps')
async def api_maps() -> JSONResponse:
    return JSONResponse(asdict(controller.get_maps()))


@app.get('/api/presets')
async def api_presets() -> JSONResponse:
    presets = [
        {'name': 'Rock 103.7', 'frequency_mhz': 103.7, 'mode': 'hd', 'hd_program': 0},
        {'name': 'Real 97.7', 'frequency_mhz': 97.7, 'mode': 'hd', 'hd_program': 0},
        {'name': '100.7 HD2', 'frequency_mhz': 100.7, 'mode': 'hd', 'hd_program': 1},
        {'name': 'Analog FM 88.1', 'frequency_mhz': 88.1, 'mode': 'fm', 'hd_program': 0},
    ]
    return JSONResponse({'presets': presets})


@app.post('/api/tune')
async def api_tune(payload: TuneRequest) -> JSONResponse:
    status = controller.tune(payload)
    return JSONResponse(asdict(status))


@app.post('/api/stop')
async def api_stop() -> JSONResponse:
    controller.stop()
    return JSONResponse(asdict(controller.get_status()))


@app.get('/api/audio')
async def api_audio(_ts: int | None = Query(default=None)) -> StreamingResponse:
    generator = await controller.open_listener()
    return StreamingResponse(generator, media_type='audio/wav')


@app.get('/api/health')
async def api_health() -> JSONResponse:
    return JSONResponse({'ok': True, 'nrsc5': bool(find_local_exe(LOCAL_NRSC5)), 'rtl_fm': bool(find_local_exe(LOCAL_RTL_FM)), 'rtl_tcp': bool(find_local_exe(LOCAL_RTL_TCP)), 'webui': WEB_DIR.exists()})


# Local development entry point.
if __name__ == '__main__':
    import uvicorn
    uvicorn.run('web_radio_server:app', host='0.0.0.0', port=8000, reload=False)


