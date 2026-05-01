const $ = (id) => document.getElementById(id);
const state = { audioVersion: 0 };

function setStatusPill(text, active = false) {
  const pill = $('status-pill');
  pill.textContent = text;
  pill.classList.toggle('active', active);
}

function readForm() {
  return {
    frequency_mhz: Number($('frequency').value),
    mode: $('mode').value,
    hd_program: Number($('hd-program').value),
    gain: Number($('gain').value),
    ppm: Number($('ppm').value),
    device_index: Number($('device-index').value),
    use_rtltcp: $('use-rtltcp').checked,
    rtltcp_host: $('rtltcp-host').value.trim() || '127.0.0.1',
  };
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(await response.text() || `${response.status} ${response.statusText}`);
  return response.json();
}

function initTabs() {
  document.querySelectorAll('.tab-button').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.tab-button').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      button.classList.add('active');
      document.getElementById(button.dataset.tab).classList.add('active');
      // Leaflet needs invalidateSize when its container becomes visible
      if (button.dataset.tab === 'weather-tab' && weatherMap) {
        setTimeout(() => weatherMap.invalidateSize(), 100);
      }
    });
  });
}

function toggleLogs() {
  const logs = $('logs');
  const button = $('toggle-logs');
  if (!logs || !button) return;
  const hidden = logs.classList.toggle('logs-hidden');
  button.textContent = hidden ? 'Show Log' : 'Hide Log';
}

async function loadPresets() {
  const response = await fetch('/api/presets');
  const data = await response.json();
  const list = $('preset-list');
  list.innerHTML = '';
  for (const preset of data.presets) {
    const button = document.createElement('button');
    button.className = 'preset';
    button.textContent = `${preset.name} \u00b7 ${preset.frequency_mhz.toFixed(1)} ${preset.mode.toUpperCase()}`;
    button.addEventListener('click', () => {
      $('frequency').value = preset.frequency_mhz;
      $('mode').value = preset.mode;
      $('hd-program').value = preset.hd_program;
    });
    list.appendChild(button);
  }
}

function renderStatus(status) {
  setStatusPill(status.running ? `Playing ${status.frequency_mhz.toFixed(1)} MHz ${status.mode.toUpperCase()}` : 'Stopped', status.running);
  const fields = [
    ['Running', String(status.running)], ['Mode', status.mode], ['Frequency', `${status.frequency_mhz.toFixed(1)} MHz`],
    ['HD Program', `HD${status.hd_program + 1}`], ['Gain', String(status.gain)], ['PPM', String(status.ppm)],
    ['Device Index', String(status.device_index)], ['rtl_tcp', status.use_rtltcp ? status.rtltcp_host : 'disabled'],
    ['Listeners', String(status.listeners)], ['Bytes Sent', String(status.bytes_sent)], ['Last Log', status.last_log || '-'], ['Last Error', status.last_error || '-'],
  ];
  const grid = $('status-grid');
  grid.innerHTML = '';
  for (const [k, v] of fields) {
    const dt = document.createElement('dt');
    dt.textContent = k;
    const dd = document.createElement('dd');
    dd.textContent = v;
    grid.append(dt, dd);
  }
}

function renderMetadata(meta) {
  $('meta-station').textContent = meta.station || meta.slogan || 'No station metadata yet';
  $('meta-title').textContent = meta.title || 'No track metadata yet';
  $('meta-artist').textContent = meta.artist || 'Artist information will appear here';
  $('meta-album').textContent = meta.album ? `Album: ${meta.album}` : '';
  $('meta-source').textContent = meta.source || 'No source';
  $('meta-updated').textContent = meta.updated_at ? new Date(meta.updated_at * 1000).toLocaleTimeString() : 'Not updated';

  const weatherTitle = $('weather-song-title');
  const weatherArtist = $('weather-song-artist');
  if (weatherTitle) weatherTitle.textContent = meta.title || 'No track metadata yet';
  if (weatherArtist) weatherArtist.textContent = meta.artist || 'Tune a station to populate song info.';

  const heroTitle = $('hero-title');
  const heroArtist = $('hero-artist');
  if (heroTitle) heroTitle.textContent = meta.title || 'No track metadata yet';
  if (heroArtist) heroArtist.textContent = meta.artist || '';

  const art = $('album-art');
  if (meta.art_url) {
    art.src = meta.art_url;
    art.style.visibility = 'visible';
  } else {
    art.removeAttribute('src');
    art.style.visibility = 'hidden';
  }
}

// Leaflet map state
let weatherMap = null;
let weatherOverlayLayer = null;
let weatherMapInitCenter = null;

function initWeatherMap(lat, lon) {
  const container = $('weather-map');
  if (!container || !window.L) return;
  if (weatherMap) {
    weatherMap.setView([lat, lon], 8);
    return;
  }
  container.style.display = 'block';
  weatherMap = L.map('weather-map', {
    center: [lat, lon],
    zoom: 8,
    zoomControl: true,
  });
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(weatherMap);
  weatherMapInitCenter = `${lat},${lon}`;
  // Fix Leaflet rendering in hidden tabs
  setTimeout(() => weatherMap.invalidateSize(), 200);
}

function updateWeatherOverlay(overlayUrl, lat, lon) {
  if (!weatherMap || !overlayUrl) return;
  // Remove old overlay
  if (weatherOverlayLayer) {
    weatherMap.removeLayer(weatherOverlayLayer);
    weatherOverlayLayer = null;
  }
  // Create image overlay covering ~2 degrees around center (roughly matches the desktop app's coverage)
  const span = 1.5;
  const bounds = [[lat - span, lon - span], [lat + span, lon + span]];
  weatherOverlayLayer = L.imageOverlay(overlayUrl, bounds, { opacity: 0.7, interactive: false }).addTo(weatherMap);
}

function renderMaps(data) {
  // Traffic tiles
  const traffic = $('traffic-grid');
  const tiles = (data.traffic_tiles || []).slice().sort((a, b) => (a.row - b.row) || (a.col - b.col));
  traffic.innerHTML = '';
  if (!tiles.length) {
    traffic.innerHTML = '<div class="traffic-placeholder">No traffic tiles received yet.</div>';
  } else {
    for (const tile of tiles) {
      const img = document.createElement('img');
      img.src = tile.url;
      img.alt = `Traffic tile ${tile.row},${tile.col}`;
      img.className = 'traffic-tile';
      traffic.appendChild(img);
    }
  }

  // Weather map
  const mapContainer = $('weather-map');
  const placeholder = $('weather-placeholder');
  const loc = data.weather_location;

  if (loc && loc.lat && loc.lon) {
    const centerKey = `${loc.lat},${loc.lon}`;
    if (!weatherMap || weatherMapInitCenter !== centerKey) {
      initWeatherMap(loc.lat, loc.lon);
    }
    if (mapContainer) mapContainer.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';

    if (data.weather_overlay_url) {
      updateWeatherOverlay(data.weather_overlay_url, loc.lat, loc.lon);
    }
  } else if (!weatherMap) {
    if (mapContainer) mapContainer.style.display = 'none';
    if (placeholder) placeholder.style.display = 'flex';
  }

  $('weather-info-file').textContent = data.weather_info_file || '-';
  $('weather-location').textContent = loc ? `${loc.lat}, ${loc.lon}` : '-';
  $('maps-updated').textContent = data.last_updated ? new Date(data.last_updated * 1000).toLocaleTimeString() : '-';
}

async function refreshStatus() {
  try { renderStatus(await (await fetch('/api/status')).json()); }
  catch (error) { setStatusPill(`Error: ${error.message}`); }
}

async function refreshMetadata() {
  try { renderMetadata(await (await fetch('/api/metadata')).json()); }
  catch (error) {}
}

async function refreshMaps() {
  try { renderMaps(await (await fetch('/api/maps')).json()); }
  catch (error) {}
}

async function refreshLogs() {
  try {
    const data = await (await fetch('/api/logs')).json();
    $('logs').textContent = (data.logs || []).slice(-60).join('\n') || 'No logs yet.';
  } catch (error) {
    $('logs').textContent = `Unable to load logs: ${error.message}`;
  }
}

function reloadAudioStream() {
  state.audioVersion += 1;
  const player = $('player');
  player.src = `/api/audio?_ts=${Date.now()}&v=${state.audioVersion}`;
  player.load();
  player.play().catch(() => {});
}

async function tuneAndPlay() {
  try {
    setStatusPill('Starting receiver...');
    await postJson('/api/tune', readForm());
    reloadAudioStream();
    await refreshStatus();
    await refreshMetadata();
    await refreshMaps();
    await refreshLogs();
  } catch (error) {
    setStatusPill(`Tune failed: ${error.message}`);
    alert(`Tune failed:\n${error.message}`);
  }
}

async function stopRadio() {
  try {
    await postJson('/api/stop');
    $('player').removeAttribute('src');
    $('player').load();
    await refreshStatus();
    await refreshMetadata();
    await refreshMaps();
    await refreshLogs();
  } catch (error) {
    alert(`Stop failed:\n${error.message}`);
  }
}

function init() {
  initTabs();
  $('play-btn').addEventListener('click', tuneAndPlay);
  $('stop-btn').addEventListener('click', stopRadio);
  $('toggle-logs').addEventListener('click', toggleLogs);
  loadPresets();
  refreshStatus();
  refreshMetadata();
  refreshMaps();
  refreshLogs();
  setInterval(refreshStatus, 2000);
  setInterval(refreshMetadata, 2000);
  setInterval(refreshMaps, 3000);
  setInterval(refreshLogs, 3000);
}

init();
