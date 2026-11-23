import base64
import glob
import io
import json
import math
import os
import re
import tempfile
import threading
import time
import urllib.request

from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request
from HersheyFonts import HersheyFonts
import serial
import serial.tools.list_ports

from universal_doc_extractor import UniversalDocumentExtractor

app = Flask(__name__)
FONTS_DIR = "fonts"
os.makedirs(FONTS_DIR, exist_ok=True)

# Global Plotter & Document State
state = {
    "serial_conn": None,
    "is_plotting": False,
    "stop_requested": False,
    "progress": 0,
    "status": "Ready",
    "gcode_lines": [],
    "doc_text_items": [],
    "doc_table_lines_mm": [],
    "doc_diagram_strokes_mm": [],
    "total_pages": 1,
    "current_page": 0,
    "temp_file_path": None,
}


# ==========================================================
# HTML5 WEB STUDIO INTERFACE (Drag-Drop, Ctrl+V Paste, URL Fetch)
# ==========================================================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Auto-Homework Pen Plotter — Web Studio</title>
<style>
  :root {
    --bg: #0b0f19;
    --panel: #141820;
    --card: #1e293b;
    --border: #334155;
    --accent: #38bdf8;
    --text: #f1f5f9;
    --muted: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }
  body { background: var(--bg); color: var(--text); height: 100vh; display: flex; overflow: hidden; }

  /* Sidebar */
  .sidebar { width: 320px; background: var(--panel); border-right: 1px solid var(--border); padding: 14px; display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }
  .brand { font-size: 20px; font-weight: 700; color: var(--accent); margin-bottom: 4px; }
  .section-title { font-size: 11px; font-weight: 700; color: var(--muted); letter-spacing: 0.8px; margin-top: 6px; }
  .row { display: flex; gap: 6px; align-items: center; }
  select, input[type="text"], button { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 7px 10px; font-size: 13px; }
  select, input[type="text"] { flex: 1; }
  button { cursor: pointer; font-weight: 600; transition: 0.15s; }
  button:hover { filter: brightness(1.15); }
  .btn-green { background: #16a34a; border-color: #15803d; }
  .btn-blue { background: #2563eb; border-color: #1d4ed8; }
  .btn-orange { background: #ea580c; border-color: #c2410c; }
  .btn-red { background: #dc2626; border-color: #b91c1c; }
  .slider-group { display: flex; flex-direction: column; gap: 3px; font-size: 12px; }
  input[type="range"] { width: 100%; accent-color: var(--accent); }

  /* Workspace */
  .workspace { flex: 1; display: flex; flex-direction: column; padding: 12px; gap: 10px; }
  .topbar { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 8px 12px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .drop-zone { border: 2px dashed #475569; border-radius: 8px; padding: 10px; text-align: center; font-size: 12px; color: var(--muted); background: #0f172a; cursor: pointer; }
  .drop-zone.dragover { border-color: var(--accent); color: var(--accent); background: #172554; }

  .split { flex: 1; display: grid; grid-template-columns: 38% 62%; gap: 10px; min-height: 0; }
  .pane { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; overflow: hidden; }
  .pane-header { padding: 8px 12px; background: var(--card); border-bottom: 1px solid var(--border); font-size: 13px; font-weight: 600; display: flex; justify-content: space-between; align-items: center; }
  textarea { flex: 1; background: #0f172a; color: #e2e8f0; border: none; padding: 12px; font-family: 'Consolas', monospace; font-size: 13px; resize: none; outline: none; }

  .canvas-wrap { flex: 1; position: relative; background: #070a12; overflow: hidden; }
  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas:active { cursor: grabbing; }

  .telemetry { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; font-family: 'Consolas', monospace; font-size: 12px; color: var(--accent); display: flex; justify-content: space-between; }
  .progress-bar { height: 8px; background: #0f172a; border-radius: 4px; overflow: hidden; }
  .progress-fill { height: 100%; width: 0%; background: #22c55e; transition: width 0.2s; }
</style>
</head>
<body>

  <!-- LEFT SIDEBAR -->
  <div class="sidebar">
    <div class="brand">✒ Web Plotter Studio</div>

    <div class="section-title">1. ARDUINO CONNECTION</div>
    <div class="row">
      <select id="portSelect"></select>
      <button onclick="refreshPorts()">↻</button>
    </div>
    <button id="connectBtn" class="btn-green" onclick="toggleConnect()">Connect Arduino</button>

    <div class="section-title">2. HANDWRITING STYLE & SIZE</div>
    <select id="styleSelect" onchange="regeneratePaths()"></select>

    <div class="slider-group">
      <label id="sizeLbl">Letter Height: 6.0 mm</label>
      <input type="range" id="sizeSlider" min="3" max="14" step="0.5" value="6" oninput="updateSliders()">
    </div>

    <div class="slider-group">
      <label id="slantLbl">Handwriting Slant: 15%</label>
      <input type="range" id="slantSlider" min="0" max="100" step="5" value="15" oninput="updateSliders()">
    </div>

    <div class="slider-group">
      <label id="servoLbl">Pen Down Servo Angle: 45°</label>
      <input type="range" id="servoSlider" min="10" max="120" step="1" value="45" oninput="updateSliders()">
    </div>

    <div class="section-title">3. PLOTTER EXECUTION</div>
    <button onclick="regeneratePaths()">⟳ Regenerate Preview</button>
    <button id="startBtn" class="btn-blue" onclick="startPlotter()">▶ Start Writing on Plotter</button>
    <button id="stopBtn" class="btn-red" onclick="stopPlotter()" disabled>■ Emergency Stop</button>

    <div class="progress-bar"><div id="progressFill" class="progress-fill"></div></div>
    <div id="statusText" style="font-size:12px; color:#94a3b8;">Status: Ready</div>
  </div>

  <!-- RIGHT WORKSPACE -->
  <div class="workspace">
    <div class="topbar">
      <input type="file" id="fileInput" accept=".pdf,.png,.jpg,.jpeg,.bmp,.webp,.docx,.txt" style="display:none" onchange="uploadFile(this.files[0])">
      <button class="btn-orange" onclick="document.getElementById('fileInput').click()">📂 Upload PDF / Image / Word</button>

      <button id="prevPageBtn" onclick="changePage(-1)" disabled>◀</button>
      <span id="pageInfo" style="font-size:12px; color:#94a3b8;">Page 1/1</span>
      <button id="nextPageBtn" onclick="changePage(1)" disabled>▶</button>

      <input type="text" id="urlInput" placeholder="Paste any website URL to extract homework text...">
      <button class="btn-blue" onclick="fetchWebsite()">🌐 Extract URL</button>

      <label style="font-size:12px; display:flex; align-items:center; gap:4px; margin-left:auto;">
        <input type="checkbox" id="exactModeChk" onchange="regeneratePaths()"> 1:1 Exact Layout
      </label>
    </div>

    <div id="dropZone" class="drop-zone" onclick="document.getElementById('fileInput').click()">
      📋 <b>Drag & Drop</b> any PDF, Sketch, Diagram, or Word file here — or press <b>Ctrl + V</b> anywhere on this page to paste a screenshot/image directly!
    </div>

    <div class="split">
      <!-- Text Editor -->
      <div class="pane">
        <div class="pane-header">
          <span>Extracted / Editable Text</span>
          <button style="padding:3px 8px; font-size:11px;" onclick="regeneratePaths()">Update Sheet</button>
        </div>
        <textarea id="textEditor" oninput="debouncedRegen()">Experiment 1: Ohm's Law

Aim: To verify the relationship between voltage and current.

Formula: V = I * R</textarea>
      </div>

      <!-- Interactive A4 Preview -->
      <div class="pane">
        <div class="pane-header">
          <div class="row" style="gap:10px; font-size:12px;">
            <label><input type="checkbox" id="lyrText" checked onchange="drawA4()"> Text</label>
            <label><input type="checkbox" id="lyrTables" checked onchange="drawA4()"> Tables</label>
            <label><input type="checkbox" id="lyrDiagrams" checked onchange="drawA4()"> Drawings & Shading</label>
          </div>
          <div class="row">
            <button style="padding:2px 8px;" onclick="zoomBy(0.85)">-</button>
            <button style="padding:2px 8px;" onclick="zoomBy(1.18)">+</button>
            <button style="padding:2px 8px;" onclick="resetView()">Reset</button>
          </div>
        </div>
        <div class="canvas-wrap">
          <canvas id="a4Canvas"></canvas>
        </div>
      </div>
    </div>

    <div class="telemetry">
      <span id="cursorTelemetry">Cursor: (0.0 mm, 0.0 mm) | Zoom: 100% (Scroll to Zoom, Drag to Pan)</span>
      <span id="statsTelemetry">Commands: 0 | Est. Time: 0s</span>
    </div>
  </div>

<script>
let previewData = { text_strokes: [], table_strokes: [], diagram_strokes: [] };
let currentPage = 0, totalPages = 1;
let zoom = 2.1, panX = 40, panY = 25;
let isDragging = false, dragStartX = 0, dragStartY = 0;
let regenTimer = null;

const canvas = document.getElementById('a4Canvas');
const ctx = canvas.getContext('2d');

window.onload = async () => {
  resizeCanvas();
  window.addEventListener('resize', resizeCanvas);
  await refreshPorts();
  await loadStyles();
  await regeneratePaths();
  setInterval(pollStatus, 800);
};

function resizeCanvas() {
  canvas.width = canvas.parentElement.clientWidth;
  canvas.height = canvas.parentElement.clientHeight;
  drawA4();
}

// ==========================================================
// CTRL+V CLIPBOARD IMAGE & TEXT PASTE + DRAG-AND-DROP
// ==========================================================
window.addEventListener('paste', async (e) => {
  const items = (e.clipboardData || e.originalEvent.clipboardData).items;
  for (const item of items) {
    if (item.type.indexOf('image') !== -1) {
      e.preventDefault();
      const file = item.getAsFile();
      await uploadFile(file);
      return;
    }
  }
});

const dropZone = document.getElementById('dropZone');
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', async (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files.length > 0) {
    await uploadFile(e.dataTransfer.files[0]);
  }
});

async function uploadFile(file) {
  if (!file) return;
  document.getElementById('statusText').innerText = `Extracting ${file.name}...`;
  const formData = new FormData();
  formData.append('file', file);

  const res = await fetch('/api/upload', { method: 'POST', body: formData });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    return;
  }
  applyExtractedDoc(data);
}

async function changePage(delta) {
  const target = currentPage + delta;
  if (target < 0 || target >= totalPages) return;
  const res = await fetch(`/api/page/${target}`);
  const data = await res.json();
  applyExtractedDoc(data);
}

async function fetchWebsite() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return;
  document.getElementById('statusText').innerText = 'Fetching webpage text...';
  const res = await fetch('/api/fetch_url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    return;
  }
  document.getElementById('exactModeChk').checked = false;
  document.getElementById('textEditor').value = data.text;
  regeneratePaths();
}

function applyExtractedDoc(data) {
  currentPage = data.current_page;
  totalPages = data.total_pages;
  document.getElementById('pageInfo').innerText = `Page ${currentPage + 1}/${totalPages}`;
  document.getElementById('prevPageBtn').disabled = (currentPage <= 0);
  document.getElementById('nextPageBtn').disabled = (currentPage >= totalPages - 1);
  document.getElementById('exactModeChk').checked = data.is_exact_layout;
  document.getElementById('textEditor').value = data.plain_text;
  regeneratePaths();
}

// ==========================================================
// PATH GENERATION & A4 CANVAS RENDERER
// ==========================================================
function updateSliders() {
  document.getElementById('sizeLbl').innerText = `Letter Height: ${parseFloat(document.getElementById('sizeSlider').value).toFixed(1)} mm`;
  document.getElementById('slantLbl').innerText = `Handwriting Slant: ${document.getElementById('slantSlider').value}%`;
  document.getElementById('servoLbl').innerText = `Pen Down Servo Angle: ${document.getElementById('servoSlider').value}°`;
  debouncedRegen();
}

function debouncedRegen() {
  clearTimeout(regenTimer);
  regenTimer = setTimeout(regeneratePaths, 250);
}

async function regeneratePaths() {
  const payload = {
    text: document.getElementById('textEditor').value,
    style: document.getElementById('styleSelect').value,
    letter_h: parseFloat(document.getElementById('sizeSlider').value),
    slant: parseFloat(document.getElementById('slantSlider').value),
    servo: parseInt(document.getElementById('servoSlider').value),
    exact_mode: document.getElementById('exactModeChk').checked,
    show_text: document.getElementById('lyrText').checked,
    show_tables: document.getElementById('lyrTables').checked,
    show_diagrams: document.getElementById('lyrDiagrams').checked
  };

  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  previewData = data;
  document.getElementById('statsTelemetry').innerText = `Commands: ${data.gcode_count} | Est. Time: ${data.est_time}`;
  document.getElementById('statusText').innerText = `Status: Ready (${data.gcode_count} G-code lines)`;
  drawA4();
}

function drawA4() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const wPx = 210 * zoom;
  const hPx = 297 * zoom;

  // Shadow & Ivory A4 Sheet
  ctx.fillStyle = '#03050a';
  ctx.fillRect(panX + 6, panY + 6, wPx, hPx);
  ctx.fillStyle = '#fdfbf7';
  ctx.fillRect(panX, panY, wPx, hPx);
  ctx.strokeStyle = '#64748b';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(panX, panY, wPx, hPx);

  // Ruled lines if not in 1:1 exact layout mode
  if (!document.getElementById('exactModeChk').checked) {
    ctx.strokeStyle = '#fca5a5';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(panX + 20 * zoom, panY);
    ctx.lineTo(panX + 20 * zoom, panY + hPx);
    ctx.stroke();

    ctx.strokeStyle = '#dbeafe';
    for (let y = 30; y < 290; y += 10) {
      ctx.beginPath();
      ctx.moveTo(panX, panY + y * zoom);
      ctx.lineTo(panX + wPx, panY + y * zoom);
      ctx.stroke();
    }
  }

  ctx.strokeStyle = '#111111';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(0.9, 0.55 * zoom);

  const renderStrokes = (list) => {
    ctx.beginPath();
    for (const stroke of list) {
      if (stroke.length < 2) continue;
      ctx.moveTo(panX + stroke[0][0] * zoom, panY + stroke[0][1] * zoom);
      for (let i = 1; i < stroke.length; i++) {
        ctx.lineTo(panX + stroke[i][0] * zoom, panY + stroke[i][1] * zoom);
      }
    }
    ctx.stroke();
  };

  if (document.getElementById('lyrTables').checked) renderStrokes(previewData.table_strokes);
  if (document.getElementById('lyrDiagrams').checked) renderStrokes(previewData.diagram_strokes);
  if (document.getElementById('lyrText').checked) renderStrokes(previewData.text_strokes);
}

// ==========================================================
// ZOOM, PAN & HARDWARE CONTROLS
// ==========================================================
function zoomBy(factor) { zoom = Math.max(0.8, Math.min(12, zoom * factor)); drawA4(); }
function resetView() { zoom = 2.1; panX = 40; panY = 25; drawA4(); }

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.15 : 0.87;
  const oldZ = zoom;
  zoom = Math.max(0.8, Math.min(12, zoom * factor));
  panX = e.offsetX - (e.offsetX - panX) * (zoom / oldZ);
  panY = e.offsetY - (e.offsetY - panY) * (zoom / oldZ);
  drawA4();
});

canvas.addEventListener('mousedown', (e) => { isDragging = true; dragStartX = e.clientX - panX; dragStartY = e.clientY - panY; });
window.addEventListener('mouseup', () => { isDragging = false; });
canvas.addEventListener('mousemove', (e) => {
  if (isDragging) {
    panX = e.clientX - dragStartX;
    panY = e.clientY - dragStartY;
    drawA4();
  }
  const mmX = ((e.offsetX - panX) / zoom).toFixed(1);
  const mmY = ((e.offsetY - panY) / zoom).toFixed(1);
  document.getElementById('cursorTelemetry').innerText = `Cursor: (${mmX} mm, ${mmY} mm) | Zoom: ${Math.round((zoom / 2.1) * 100)}%`;
});

async function refreshPorts() {
  const res = await fetch('/api/ports');
  const data = await res.json();
  const sel = document.getElementById('portSelect');
  sel.innerHTML = data.ports.map(p => `<option value="${p}">${p}</option>`).join('');
}

async function loadStyles() {
  const res = await fetch('/api/styles');
  const data = await res.json();
  const sel = document.getElementById('styleSelect');
  sel.innerHTML = data.styles.map(s => `<option value="${s}">${s}</option>`).join('');
}

async function toggleConnect() {
  const port = document.getElementById('portSelect').value;
  const res = await fetch('/api/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port })
  });
  const data = await res.json();
  const btn = document.getElementById('connectBtn');
  btn.innerText = data.connected ? 'Disconnect' : 'Connect Arduino';
  btn.className = data.connected ? 'btn-red' : 'btn-green';
  document.getElementById('statusText').innerText = data.message;
}

async function startPlotter() {
  await fetch('/api/start', { method: 'POST' });
  document.getElementById('startBtn').disabled = true;
  document.getElementById('stopBtn').disabled = false;
}

async function stopPlotter() {
  await fetch('/api/stop', { method: 'POST' });
}

async function pollStatus() {
  const res = await fetch('/api/status');
  const data = await res.json();
  document.getElementById('progressFill').style.width = `${data.progress}%`;
  if (data.is_plotting) {
    document.getElementById('statusText').innerText = data.status;
  } else {
    document.getElementById('startBtn').disabled = false;
    document.getElementById('stopBtn').disabled = true;
  }
}
</script>
</body>
</html>
"""


# ==========================================================
# FLASK API ROUTES
# ==========================================================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/ports")
def api_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return jsonify({"ports": ports if ports else ["No Ports Found"]})


@app.route("/api/styles")
def api_styles():
    custom_files = sorted(glob.glob(os.path.join(FONTS_DIR, "*.json")))
    custom_labels = [f"[Custom] {os.path.splitext(os.path.basename(f))[0]}" for f in custom_files]
    hf = HersheyFonts()
    builtin_labels = [f"[Built-in] {name}" for name in sorted(hf.default_font_names)]
    return jsonify({"styles": custom_labels + builtin_labels})


@app.route("/api/connect", methods=["POST"])
def api_connect():
    if state["serial_conn"] and state["serial_conn"].is_open:
        state["serial_conn"].close()
        state["serial_conn"] = None
        return jsonify({"connected": False, "message": "Status: Disconnected"})

    port = request.json.get("port", "")
    if "No Ports" in port or not port:
        return jsonify({"connected": False, "message": "Status: Select a valid COM port"})

    try:
        conn = serial.Serial(port, 115200, timeout=2)
        time.sleep(2)
        conn.flushInput()
        state["serial_conn"] = conn
        return jsonify({"connected": True, "message": f"Status: Connected ({port})"})
    except Exception as e:
        return jsonify({"connected": False, "message": f"Error: {str(e)[:40]}"})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"})
    f = request.files["file"]
    ext = os.path.splitext(f.filename or "paste.png")[1].lower() or ".png"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    f.save(tmp.name)
    tmp.close()

    state["temp_file_path"] = tmp.name
    return _load_page(0)


@app.route("/api/page/<int:page_num>")
def api_page(page_num):
    return _load_page(page_num)


def _load_page(page_num):
    path = state.get("temp_file_path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "No file loaded"})
    try:
        data = UniversalDocumentExtractor.extract_file(path, page_num)
        state["total_pages"] = data["total_pages"]
        state["current_page"] = page_num
        state["doc_text_items"] = data["text_items"]
        state["doc_table_lines_mm"] = data["table_lines_mm"]
        state["doc_diagram_strokes_mm"] = data["diagram_strokes_mm"]
        return jsonify({
            "plain_text": data["plain_text"],
            "current_page": page_num,
            "total_pages": data["total_pages"],
            "is_exact_layout": data["is_exact_layout"]
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/fetch_url", methods=["POST"])
def api_fetch_url():
    url = request.json.get("url", "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=8).read().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        blocks = [p.get_text(" ", strip=True) for p in soup.find_all(["h1", "h2", "h3", "p", "li"])]
        clean_lines = [b for b in blocks if len(b) > 15]
        state["doc_text_items"] = []
        state["doc_table_lines_mm"] = []
        state["doc_diagram_strokes_mm"] = []
        return jsonify({"text": "\n\n".join(clean_lines[:40])})
    except Exception as e:
        return jsonify({"error": f"Could not extract URL: {e}"})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    cfg = request.json
    letter_h = float(cfg.get("letter_h", 6.0))
    slant_amt = float(cfg.get("slant", 15.0)) / 100.0
    pen_down_angle = int(cfg.get("servo", 45))
    selected = cfg.get("style", "[Built-in] scripts")

    custom_glyphs = {}
    is_custom = selected.startswith("[Custom] ")
    fallback_font_name = "scripts"

    if is_custom:
        style_name = selected.replace("[Custom] ", "", 1)
        json_path = os.path.join(FONTS_DIR, f"{style_name}.json")
        if os.path.exists(json_path):
            with open(json_path, "r") as f:
                custom_glyphs = json.load(f)
    else:
        fallback_font_name = selected.replace("[Built-in] ", "", 1)

    font = HersheyFonts()
    font.load_default_font(fallback_font_name)
    font.normalize_rendering(letter_h)

    text_strokes = []
    table_strokes = list(state["doc_table_lines_mm"]) if cfg.get("exact_mode") else []
    diagram_strokes = list(state["doc_diagram_strokes_mm"]) if cfg.get("exact_mode") else []
    slant = 0.14 * slant_amt

    def render_run(txt, sx, sy, h_mm):
        cx = sx
        for ch in txt:
            if ch == " ":
                cx += h_mm * 0.48
                continue
            if is_custom and ch in custom_glyphs:
                glyph = [[(x * h_mm, y * h_mm) for x, y in s] for s in custom_glyphs[ch]]
            else:
                font.normalize_rendering(h_mm)
                glyph = list(font.strokes_for_text(ch))
            max_x = 0
            for s in glyph:
                pts = [[cx + x + (y * slant), sy - y] for x, y in s]
                for x, _ in s:
                    if x > max_x:
                        max_x = x
                if len(pts) > 1:
                    text_strokes.append(pts)
            cx += (max_x if max_x > 0 else h_mm * 0.35) + (h_mm * 0.12)

    if cfg.get("exact_mode") and state["doc_text_items"]:
        for item in state["doc_text_items"]:
            render_run(item["text"], item["x_mm"], item["y_mm"], item["height_mm"] * (letter_h / 6.0))
    else:
        cx, cy = 22.0, 30.0
        for ch in cfg.get("text", ""):
            if ch == "\n":
                cx, cy = 22.0, cy + 10.0
                continue
            if ch == " ":
                cx += letter_h * 0.52
                continue
            if is_custom and ch in custom_glyphs:
                glyph = [[(x * letter_h, y * letter_h) for x, y in s] for s in custom_glyphs[ch]]
            else:
                glyph = list(font.strokes_for_text(ch))
            max_x = 0
            for s in glyph:
                pts = [[cx + x + (y * slant), cy - y] for x, y in s]
                for x, _ in s:
                    if x > max_x:
                        max_x = x
                if len(pts) > 1:
                    text_strokes.append(pts)
            cx += (max_x if max_x > 0 else letter_h * 0.4) + (letter_h * 0.14)
            if cx > 195.0:
                cx, cy = 22.0, cy + 10.0

    # Build G-code
    gcode = ["G21", "G90", "M3 S0", "G0 X0.000 Y0.000 F3500"]
    dist = 0.0

    def add_gcode(strokes, feed):
        nonlocal dist
        for s in strokes:
            if len(s) < 2:
                continue
            gcode.append("M3 S0")
            gcode.append(f"G0 X{s[0][0]:.3f} Y{s[0][1]:.3f} F3500")
            gcode.append(f"M3 S{pen_down_angle}")
            lx, ly = s[0]
            for px, py in s[1:]:
                d = math.hypot(px - lx, py - ly)
                if d < 0.05:
                    continue
                dist += d
                gcode.append(f"G1 X{px:.3f} Y{py:.3f} F{feed}")
                lx, ly = px, py

    if cfg.get("show_tables", True):
        add_gcode(table_strokes, 1400)
    if cfg.get("show_diagrams", True):
        add_gcode(diagram_strokes, 1600)
    if cfg.get("show_text", True):
        add_gcode(text_strokes, 1800)

    gcode.extend(["M3 S0", "G0 X0.000 Y0.000 F3500"])
    state["gcode_lines"] = gcode

    est_sec = int((dist / 28.0) + (len(gcode) * 0.015))
    mins, secs = divmod(est_sec, 60)

    return jsonify({
        "text_strokes": text_strokes,
        "table_strokes": table_strokes,
        "diagram_strokes": diagram_strokes,
        "gcode_count": len(gcode),
        "est_time": f"{mins}m {secs}s"
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    if not state["serial_conn"] or not state["serial_conn"].is_open:
        return jsonify({"error": "Connect Arduino first!"})
    state["is_plotting"] = True
    state["stop_requested"] = False
    threading.Thread(target=_stream_worker, daemon=True).start()
    return jsonify({"started": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["stop_requested"] = True
    return jsonify({"stopping": True})


@app.route("/api/status")
def api_status():
    return jsonify({
        "is_plotting": state["is_plotting"],
        "progress": state["progress"],
        "status": state["status"]
    })


def _stream_worker():
    lines = state["gcode_lines"]
    total = max(len(lines), 1)
    conn = state["serial_conn"]
    for i, line in enumerate(lines):
        if state["stop_requested"]:
            conn.write(b"M3 S0\n")
            state["status"] = "Stopped by user."
            break
        conn.write((line + "\n").encode())
        while True:
            resp = conn.readline().decode().strip()
            if resp == "ok" or "error" in resp:
                break
        state["progress"] = int(((i + 1) / total) * 100)
        state["status"] = f"Writing: {state['progress']}% ({i + 1}/{total})"
    else:
        state["status"] = "Finished writing homework!"
    state["is_plotting"] = False


if __name__ == "__main__":
    print("Open http://127.0.0.1:5000 in your browser!")
    app.run(host="127.0.0.1", port=5000, debug=False)