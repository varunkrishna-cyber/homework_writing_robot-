import glob
import json
import math
import os
import tempfile
import threading
import time
import urllib.request
import webbrowser

from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string, request
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import serial
import serial.tools.list_ports

from handwriting_font_builder import HandwritingFontBuilder
from universal_doc_extractor import UniversalDocumentExtractor, _fast_skeletonize

try:
    from HersheyFonts import HersheyFonts
    HAS_HERSHEY = True
except ImportError:
    HAS_HERSHEY = False

app = Flask(__name__)
FONTS_DIR = "fonts"
os.makedirs(FONTS_DIR, exist_ok=True)

SYSTEM_FONT_CACHE = {}

WORD_SYSTEM_FONTS = {
    "Segoe Print (Handwriting)": ["segoepr.ttf", "segoeprb.ttf"],
    "Segoe Script (Cursive)": ["segoesc.ttf", "segoescb.ttf"],
    "Comic Sans MS": ["comic.ttf", "comicbd.ttf"],
    "Lucida Handwriting": ["lhandw.ttf"],
    "Bradley Hand ITC": ["bradhitc.ttf"],
    "Ink Free": ["inkfree.ttf"],
    "Gabriola": ["gabriola.ttf"],
    "Times New Roman": ["times.ttf"],
    "Calibri": ["calibri.ttf"],
    "Arial": ["arial.ttf"],
    "Georgia": ["georgia.ttf"],
    "Consolas": ["consola.ttf"],
}

BUILTIN_GLYPHS = {
    'A': [[[0,0],[0.3,1],[0.6,0]], [[0.1,0.35],[0.5,0.35]]],
    'B': [[[0,0],[0,1],[0.4,1],[0.52,0.88],[0.52,0.62],[0.4,0.5],[0,0.5]], [[0.4,0.5],[0.55,0.38],[0.55,0.12],[0.4,0],[0,0]]],
    'C': [[[0.55,0.85],[0.4,1],[0.15,1],[0,0.8],[0,0.2],[0.15,0],[0.4,0],[0.55,0.15]]],
    'D': [[[0,0],[0,1],[0.35,1],[0.55,0.8],[0.55,0.2],[0.35,0],[0,0]]],
    'E': [[[0.5,1],[0,1],[0,0],[0.5,0]], [[0,0.5],[0.4,0.5]]],
    'F': [[[0.5,1],[0,1],[0,0]], [[0,0.5],[0.4,0.5]]],
    'G': [[[0.55,0.85],[0.4,1],[0.15,1],[0,0.8],[0,0.2],[0.15,0],[0.45,0],[0.55,0.2],[0.55,0.45],[0.32,0.45]]],
    'H': [[[0,1],[0,0]], [[0.55,1],[0.55,0]], [[0,0.5],[0.55,0.5]]],
    'I': [[[0.15,1],[0.45,1]], [[0.3,1],[0.3,0]], [[0.15,0],[0.45,0]]],
    'J': [[[0.45,1],[0.45,0.2],[0.32,0],[0.12,0],[0,0.18]]],
    'K': [[[0,1],[0,0]], [[0.55,1],[0.05,0.45],[0.55,0]]],
    'L': [[[0,1],[0,0],[0.5,0]]],
    'M': [[[0,0],[0,1],[0.32,0.35],[0.64,1],[0.64,0]]],
    'N': [[[0,0],[0,1],[0.55,0],[0.55,1]]],
    'O': [[[0.2,1],[0.4,1],[0.58,0.8],[0.58,0.2],[0.4,0],[0.2,0],[0,0.2],[0,0.8],[0.2,1]]],
    'P': [[[0,0],[0,1],[0.42,1],[0.55,0.85],[0.55,0.62],[0.42,0.5],[0,0.5]]],
    'Q': [[[0.2,1],[0.4,1],[0.58,0.8],[0.58,0.2],[0.4,0],[0.2,0],[0,0.2],[0,0.8],[0.2,1]], [[0.38,0.22],[0.62,-0.05]]],
    'R': [[[0,0],[0,1],[0.42,1],[0.55,0.85],[0.55,0.62],[0.42,0.5],[0,0.5]], [[0.32,0.5],[0.55,0]]],
    'S': [[[0.52,0.88],[0.38,1],[0.15,1],[0.02,0.85],[0.02,0.65],[0.52,0.38],[0.52,0.15],[0.38,0],[0.12,0],[0,0.14]]],
    'T': [[[0,1],[0.6,1]], [[0.3,1],[0.3,0]]],
    'U': [[[0,1],[0,0.2],[0.15,0],[0.4,0],[0.55,0.2],[0.55,1]]],
    'V': [[[0,1],[0.28,0],[0.56,1]]],
    'W': [[[0,1],[0.18,0],[0.35,0.6],[0.52,0],[0.7,1]]],
    'X': [[[0,1],[0.55,0]], [[0.55,1],[0,0]]],
    'Y': [[[0,1],[0.28,0.48],[0.56,1]], [[0.28,0.48],[0.28,0]]],
    'Z': [[[0,1],[0.55,1],[0,0],[0.55,0]]],
    'a': [[[0.42,0.55],[0.15,0.55],[0,0.4],[0,0.15],[0.15,0],[0.42,0]], [[0.42,0.55],[0.42,0]]],
    'b': [[[0,1],[0,0],[0.32,0],[0.45,0.15],[0.45,0.42],[0.32,0.55],[0,0.55]]],
    'c': [[[0.42,0.45],[0.3,0.55],[0.12,0.55],[0,0.4],[0,0.15],[0.12,0],[0.3,0],[0.42,0.1]]],
    'd': [[[0.45,1],[0.45,0],[0.15,0],[0,0.15],[0,0.42],[0.15,0.55],[0.45,0.55]]],
    'e': [[[0,0.28],[0.44,0.28],[0.44,0.44],[0.3,0.55],[0.12,0.55],[0,0.4],[0,0.14],[0.14,0],[0.38,0]]],
    'f': [[[0.38,0.92],[0.26,1],[0.15,0.9],[0.15,0]], [[0.02,0.55],[0.34,0.55]]],
    'g': [[[0.44,0.55],[0.15,0.55],[0,0.42],[0,0.15],[0.15,0],[0.44,0]], [[0.44,0.55],[0.44,-0.22],[0.3,-0.34],[0.1,-0.34]]],
    'h': [[[0,1],[0,0]], [[0,0.42],[0.16,0.55],[0.34,0.55],[0.44,0.42],[0.44,0]]],
    'i': [[[0.15,0.55],[0.15,0]], [[0.15,0.78],[0.15,0.82]]],
    'j': [[[0.22,0.55],[0.22,-0.22],[0.1,-0.34],[0,-0.3]], [[0.22,0.78],[0.22,0.82]]],
    'k': [[[0,1],[0,0]], [[0.42,0.55],[0.04,0.25],[0.42,0]]],
    'l': [[[0.15,1],[0.15,0.08],[0.25,0]]],
    'm': [[[0,0.55],[0,0]], [[0,0.44],[0.12,0.55],[0.24,0.55],[0.32,0.44],[0.32,0]], [[0.32,0.44],[0.44,0.55],[0.56,0.55],[0.64,0.44],[0.64,0]]],
    'n': [[[0,0.55],[0,0]], [[0,0.42],[0.16,0.55],[0.34,0.55],[0.44,0.42],[0.44,0]]],
    'o': [[[0.15,0.55],[0.32,0.55],[0.45,0.4],[0.45,0.15],[0.32,0],[0.15,0],[0,0.15],[0,0.4],[0.15,0.55]]],
    'p': [[[0,-0.34],[0,0.55],[0.32,0.55],[0.45,0.42],[0.45,0.15],[0.32,0],[0,0]]],
    'q': [[[0.44,-0.34],[0.44,0.55],[0.14,0.55],[0,0.42],[0,0.15],[0.14,0],[0.44,0]]],
    'r': [[[0,0.55],[0,0]], [[0,0.38],[0.14,0.55],[0.32,0.55]]],
    's': [[[0.4,0.46],[0.28,0.55],[0.12,0.55],[0.02,0.44],[0.4,0.16],[0.28,0],[0.1,0],[0,0.1]]],
    't': [[[0.18,0.88],[0.18,0.1],[0.28,0],[0.38,0]], [[0.04,0.55],[0.34,0.55]]],
    'u': [[[0,0.55],[0,0.14],[0.14,0],[0.32,0],[0.44,0.14],[0.44,0.55]]],
    'v': [[[0,0.55],[0.22,0],[0.44,0.55]]],
    'w': [[[0,0.55],[0.14,0],[0.28,0.38],[0.42,0],[0.56,0.55]]],
    'x': [[[0,0.55],[0.44,0]], [[0.44,0.55],[0,0]]],
    'y': [[[0,0.55],[0.22,0]], [[0.44,0.55],[0.12,-0.34],[0,-0.34]]],
    'z': [[[0,0.55],[0.42,0.55],[0,0],[0.42,0]]],
    '0': [[[0.18,1],[0.38,1],[0.52,0.8],[0.52,0.2],[0.38,0],[0.18,0],[0,0.2],[0,0.8],[0.18,1]]],
    '1': [[[0.12,0.82],[0.28,1],[0.28,0]], [[0.12,0],[0.44,0]]],
    '2': [[[0.04,0.82],[0.2,1],[0.4,1],[0.52,0.84],[0.52,0.65],[0.02,0],[0.54,0]]],
    '3': [[[0.04,0.86],[0.2,1],[0.4,1],[0.52,0.84],[0.52,0.64],[0.34,0.52],[0.22,0.52]], [[0.34,0.52],[0.54,0.38],[0.54,0.16],[0.38,0],[0.16,0],[0.02,0.14]]],
    '4': [[[0.42,0],[0.42,1],[0,0.32],[0.56,0.32]]],
    '5': [[[0.5,1],[0.06,1],[0.04,0.56],[0.38,0.56],[0.52,0.4],[0.52,0.16],[0.36,0],[0.14,0],[0.02,0.14]]],
    '6': [[[0.46,0.88],[0.32,1],[0.16,1],[0.02,0.8],[0.02,0.18],[0.16,0],[0.38,0],[0.52,0.18],[0.52,0.38],[0.38,0.52],[0.02,0.52]]],
    '7': [[[0,1],[0.54,1],[0.18,0]]],
    '8': [[[0.18,0.52],[0.04,0.66],[0.04,0.86],[0.18,1],[0.38,1],[0.52,0.86],[0.52,0.66],[0.38,0.52],[0.18,0.52],[0.02,0.36],[0.02,0.16],[0.18,0],[0.38,0],[0.54,0.16],[0.54,0.36],[0.38,0.52]]],
    '9': [[[0.52,0.48],[0.16,0.48],[0.02,0.62],[0.02,0.84],[0.16,1],[0.38,1],[0.52,0.84],[0.52,0.18],[0.36,0],[0.14,0]]],
    '.': [[[0.12,0.06],[0.18,0.06],[0.18,0],[0.12,0],[0.12,0.06]]],
    ',': [[[0.16,0.08],[0.16,-0.12],[0.08,-0.2]]],
    ':': [[[0.14,0.52],[0.14,0.46]], [[0.14,0.08],[0.14,0.02]]],
    ';': [[[0.14,0.52],[0.14,0.46]], [[0.14,0.08],[0.14,-0.12],[0.06,-0.2]]],
    '-': [[[0.06,0.42],[0.42,0.42]]],
    '=': [[[0.06,0.54],[0.46,0.54]], [[0.06,0.32],[0.46,0.32]]],
    '+': [[[0.26,0.68],[0.26,0.18]], [[0.04,0.43],[0.48,0.43]]],
    '*': [[[0.26,0.68],[0.26,0.22]], [[0.06,0.56],[0.46,0.34]], [[0.46,0.56],[0.06,0.34]]],
    '/': [[[0.04,0],[0.46,1]]],
    '(': [[[0.34,1],[0.14,0.75],[0.14,0.25],[0.34,0]]],
    ')': [[[0.12,1],[0.32,0.75],[0.32,0.25],[0.12,0]]],
    '!': [[[0.16,1],[0.16,0.25]], [[0.16,0.06],[0.16,0]]],
    '?': [[[0.04,0.82],[0.18,1],[0.38,1],[0.5,0.84],[0.5,0.65],[0.26,0.45],[0.26,0.26]], [[0.26,0.06],[0.26,0]]],
    "'": [[[0.14,1],[0.14,0.76]]],
    '"': [[[0.1,1],[0.1,0.76]], [[0.26,1],[0.26,0.76]]],
}

state = {
    "serial_conn": None,
    "is_plotting": False,
    "stop_requested": False,
    "progress": 0,
    "status": "Ready • Integrated Python + Web Studio",
    "gcode_lines": [],
    "doc_text_items": [],
    "doc_table_lines_mm": [],
    "doc_diagram_strokes_mm": [],
    "total_pages": 1,
    "current_page": 0,
    "temp_file_path": None,
}


def _find_windows_font_path(label):
    candidates = WORD_SYSTEM_FONTS.get(label, [])
    win_fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for fname in candidates:
        full = os.path.join(win_fonts_dir, fname)
        if os.path.exists(full):
            return full
    return None


def _get_word_font_glyph(font_label, ch):
    cache_key = (font_label, ch)
    if cache_key in SYSTEM_FONT_CACHE:
        return SYSTEM_FONT_CACHE[cache_key]

    ttf_path = _find_windows_font_path(font_label)
    if not ttf_path:
        return BUILTIN_GLYPHS.get(ch) or BUILTIN_GLYPHS.get(ch.upper(), [])

    try:
        pil_font = ImageFont.truetype(ttf_path, 72)
        canvas_size = 140
        baseline_px = 100
        cap_h_px = 52.0

        img = Image.new("L", (canvas_size, canvas_size), 0)
        draw = ImageDraw.Draw(img)
        draw.text((20, baseline_px - 68), ch, fill=255, font=pil_font)

        arr = np.array(img)
        if not np.any(arr):
            SYSTEM_FONT_CACHE[cache_key] = []
            return []

        ys, xs = np.where(arr > 128)
        x_min = int(xs.min())

        skel = _fast_skeletonize(arr > 128)
        h, w = skel.shape
        visited = np.zeros_like(skel, dtype=bool)
        neighbors = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
        norm_strokes = []

        sys_pts, sxs_pts = np.where(skel > 0)
        for sy, sx in zip(sys_pts, sxs_pts):
            if visited[sy, sx]:
                continue
            pts = [(int(sx), int(sy))]
            visited[sy, sx] = True
            cx, cy = int(sx), int(sy)
            pdx, pdy = 0, 0

            while True:
                best_next = None
                best_score = -999
                for dx, dy in neighbors:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and skel[ny, nx] > 0 and not visited[ny, nx]:
                        score = (dx * pdx + dy * pdy) if (pdx or pdy) else 0
                        if score > best_score:
                            best_score = score
                            best_next = (nx, ny, dx, dy)
                if best_next is None:
                    break
                nx, ny, pdx, pdy = best_next
                visited[ny, nx] = True
                pts.append((nx, ny))
                cx, cy = nx, ny

            if len(pts) >= 2:
                scaled = []
                for px, py in pts:
                    nx = round((px - x_min) / cap_h_px, 4)
                    ny = round((baseline_px - py) / cap_h_px, 4)
                    if not scaled or math.hypot(nx - scaled[-1][0], ny - scaled[-1][1]) >= 0.03:
                        scaled.append([nx, ny])
                if len(scaled) >= 2:
                    norm_strokes.append(scaled)

        SYSTEM_FONT_CACHE[cache_key] = norm_strokes
        return norm_strokes
    except Exception:
        return BUILTIN_GLYPHS.get(ch) or BUILTIN_GLYPHS.get(ch.upper(), [])


DRAFTMATE_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>DraftMate — Integrated Pen Plotter Studio</title>
<style>
  :root {
    --bg-canvas: #e5e7eb;
    --grid-line: rgba(148, 163, 184, 0.28);
    --surface: #ffffff;
    --surface-alt: #f8fafc;
    --border: #cbd5e1;
    --ink: #0f172a;
    --muted: #64748b;
    --primary: #0f766e;
    --primary-hover: #115e59;
    --accent: #d97706;
    --danger: #dc2626;
    --shadow-sm: 0 1px 3px rgba(15, 23, 42, 0.08);
    --shadow-lg: 0 12px 32px rgba(15, 23, 42, 0.14);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', 'Segoe UI', system-ui, sans-serif; }

  body {
    height: 100vh;
    display: flex;
    flex-direction: column;
    background-color: var(--bg-canvas);
    background-image:
      linear-gradient(to right, var(--grid-line) 1px, transparent 1px),
      linear-gradient(to bottom, var(--grid-line) 1px, transparent 1px);
    background-size: 24px 24px;
    color: var(--ink);
    overflow: hidden;
  }

  .top-ribbon {
    height: 56px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 18px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    box-shadow: var(--shadow-sm);
    z-index: 20;
  }

  .brand-group { display: flex; align-items: center; gap: 10px; }
  .logo-badge {
    width: 32px; height: 32px; border-radius: 8px;
    background: linear-gradient(135deg, #0f766e, #0d9488);
    color: #fff; display: grid; place-items: center;
    font-weight: 800; font-size: 16px;
  }
  .brand-title { font-weight: 800; font-size: 16px; letter-spacing: -0.3px; }
  .brand-tag {
    font-size: 11px; font-weight: 600; color: var(--primary);
    background: #ccfbf1; padding: 2px 8px; border-radius: 99px;
  }

  .ribbon-actions { display: flex; align-items: center; gap: 7px; }

  button, select, input[type="text"] {
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--ink);
    padding: 6px 11px;
    border-radius: 7px;
    font-size: 12.5px;
    font-weight: 600;
    transition: all 0.15s ease;
  }
  button { cursor: pointer; display: inline-flex; align-items: center; gap: 5px; }
  button:hover:not(:disabled) { background: var(--surface-alt); border-color: #94a3b8; }
  button:disabled { opacity: 0.45; cursor: not-allowed; }

  .btn-primary { background: var(--primary); color: #fff; border-color: var(--primary); }
  .btn-primary:hover:not(:disabled) { background: var(--primary-hover); border-color: var(--primary-hover); }

  .btn-accent { background: var(--accent); color: #fff; border-color: var(--accent); }
  .btn-accent:hover:not(:disabled) { background: #b45309; border-color: #b45309; }

  .btn-danger { background: var(--danger); color: #fff; border-color: var(--danger); }
  .btn-danger:hover:not(:disabled) { background: #b91c1c; }

  .status-pill {
    font-size: 12px; font-weight: 600; padding: 5px 12px;
    border-radius: 99px; background: #f1f5f9; border: 1px solid var(--border);
    color: var(--muted); display: flex; align-items: center; gap: 6px;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #94a3b8; }
  .dot.online { background: #16a34a; box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.2); }
  .dot.busy { background: #d97706; box-shadow: 0 0 0 3px rgba(217, 119, 6, 0.2); }

  .main-stage {
    flex: 1;
    display: grid;
    grid-template-columns: 350px 1fr 315px;
    gap: 14px;
    padding: 14px;
    min-height: 0;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: var(--shadow-sm);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .card-title {
    padding: 9px 14px;
    background: var(--surface-alt);
    border-bottom: 1px solid var(--border);
    font-size: 11.5px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--muted);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .hw-banner {
    margin: 10px 12px 0 12px;
    padding: 8px 10px;
    border-radius: 8px;
    background: #f0fdfa;
    border: 1px solid #99f6e4;
    display: flex;
    align-items: center;
    gap: 7px;
    font-size: 12px;
    font-weight: 700;
    color: #0f766e;
    cursor: pointer;
  }

  .drop-box {
    margin: 10px 12px;
    padding: 12px 10px;
    border: 2px dashed #94a3b8;
    border-radius: 10px;
    background: #f8fafc;
    text-align: center;
    font-size: 12px;
    color: var(--muted);
    cursor: pointer;
    transition: 0.15s;
  }
  .drop-box:hover, .drop-box.dragover {
    border-color: var(--primary);
    background: #f0fdfa;
    color: var(--primary);
  }

  .url-bar {
    display: flex; gap: 6px; padding: 0 12px 10px 12px;
  }
  .url-bar input { flex: 1; font-weight: 400; font-size: 12px; }

  .page-nav {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 12px 10px 12px; font-size: 12px; color: var(--muted);
  }

  textarea {
    flex: 1;
    border: none;
    border-top: 1px solid var(--border);
    padding: 12px 14px;
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 12.5px;
    line-height: 1.55;
    color: var(--ink);
    background: #ffffff;
    resize: none;
    outline: none;
  }

  .viewport-container {
    position: relative;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: #cbd5e1;
    box-shadow: inset 0 2px 8px rgba(15, 23, 42, 0.1);
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  canvas { display: block; width: 100%; height: 100%; cursor: grab; }
  canvas:active { cursor: grabbing; }

  .floating-toolbar {
    position: absolute;
    bottom: 14px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border);
    border-radius: 99px;
    padding: 6px 14px;
    display: flex;
    align-items: center;
    gap: 9px;
    box-shadow: var(--shadow-lg);
    font-size: 12px;
    white-space: nowrap;
  }

  .floating-toolbar button {
    border-radius: 99px;
    padding: 4px 10px;
    font-size: 11.5px;
  }

  .coord-chip {
    position: absolute;
    top: 12px;
    left: 12px;
    background: rgba(15, 23, 42, 0.84);
    color: #f8fafc;
    font-family: 'Consolas', monospace;
    font-size: 11.5px;
    padding: 5px 10px;
    border-radius: 6px;
    pointer-events: none;
  }

  .inspector-body {
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 11px;
    overflow-y: auto;
    flex: 1;
  }

  .param-block { display: flex; flex-direction: column; gap: 5px; }
  .param-hdr { display: flex; justify-content: space-between; font-size: 12px; font-weight: 600; }
  .param-val { color: var(--primary); font-family: 'Consolas', monospace; }
  input[type="range"] { width: 100%; accent-color: var(--primary); cursor: pointer; }

  .toggle-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 10px; background: var(--surface-alt);
    border: 1px solid var(--border); border-radius: 8px; font-size: 12.5px; font-weight: 600;
  }

  .hw-clone-box {
    background: #fffbeb;
    border: 1px solid #fde68a;
    border-radius: 10px;
    padding: 10px;
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .hw-clone-title {
    font-size: 11.5px; font-weight: 800; color: #92400e;
    display: flex; justify-content: space-between; align-items: center;
  }
  .hw-guide {
    font-family: 'Consolas', monospace;
    font-size: 10.5px;
    background: #ffffff;
    border: 1px solid #fcd34d;
    border-radius: 6px;
    padding: 6px;
    color: #451a03;
    line-height: 1.45;
  }

  .telemetry-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
    margin-top: auto; padding-top: 8px; border-top: 1px solid var(--border);
  }
  .stat-tile {
    background: var(--surface-alt); border: 1px solid var(--border);
    border-radius: 8px; padding: 8px 10px;
  }
  .stat-lbl { font-size: 10.5px; color: var(--muted); font-weight: 700; text-transform: uppercase; }
  .stat-num { font-size: 14px; font-weight: 800; color: var(--ink); margin-top: 2px; font-family: 'Consolas', monospace; }

  .prog-bar { height: 6px; background: #e2e8f0; border-radius: 99px; overflow: hidden; }
  .prog-fill { height: 100%; width: 0%; background: var(--primary); transition: width 0.15s; }
</style>
</head>
<body>

  <header class="top-ribbon">
    <div class="brand-group">
      <div class="logo-badge">✒</div>
      <div class="brand-title">DraftMate Studio</div>
      <span class="brand-tag">Integrated Python + Web Engine</span>
    </div>

    <div class="status-pill">
      <span id="statusDot" class="dot"></span>
      <span id="statusText">Ready • Connected to Python Extractor</span>
    </div>

    <div class="ribbon-actions">
      <select id="portSelect" style="min-width:115px;"></select>
      <button onclick="refreshPorts()" title="Refresh COM Ports">↻</button>
      <button id="btnUsb" onclick="toggleBackendUsb()">🔌 Connect COM</button>
      <button onclick="downloadGcode()">⬇ .GCODE</button>
      <button id="btnRun" class="btn-primary" onclick="startPlotter()">▶ Start Plotter</button>
      <button id="btnStop" class="btn-danger" onclick="stopPlotter()" disabled>■ Stop</button>
    </div>
  </header>

  <div class="main-stage">

    <section class="card">
      <div class="card-title">
        <span>1. Source Input</span>
        <button style="padding:3px 8px; font-size:11px;" onclick="document.getElementById('fileInput').click()">📂 Browse</button>
      </div>

      <input type="file" id="fileInput" accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.docx,.txt" style="display:none" onchange="uploadFile(this.files[0])" />

      <label class="hw-banner" title="Extracts human-written text and circuit diagrams from scanned notebook pages">
        <input type="checkbox" id="chkHandwrittenMode" onchange="reextractCurrentFile()" />
        <span>✍ Handwritten Notes + Circuit Diagrams Mode</span>
      </label>

      <div id="dropZone" class="drop-box" onclick="document.getElementById('fileInput').click()">
        <b>Drop PDF, Sketch, Word (.docx), or TXT</b><br/>
        or press <b>Ctrl + V</b> anywhere to paste an image
      </div>

      <div class="url-bar">
        <input type="text" id="urlInput" placeholder="Paste webpage URL to extract text..." />
        <button class="btn-primary" style="padding:6px 10px;" onclick="extractFromUrl()">Fetch</button>
      </div>

      <div class="page-nav">
        <button id="btnPrev" style="padding:3px 9px;" onclick="changePage(-1)" disabled>◀ Prev</button>
        <span id="lblPage">Page 1 of 1</span>
        <button id="btnNext" style="padding:3px 9px;" onclick="changePage(1)" disabled>Next ▶</button>
      </div>

      <div class="card-title" style="border-top:1px solid var(--border);">
        <span>Document Text (Editable)</span>
        <button style="padding:2px 8px; font-size:11px;" onclick="compilePaths()">Sync</button>
      </div>

      <textarea id="editor" oninput="scheduleCompile()">Experiment 1: Ohm's Law

Aim: To verify the relationship between voltage and current.

Formula: V = I * R</textarea>
    </section>

    <section class="viewport-container">
      <div id="coordHud" class="coord-chip">X: 0.0 mm  Y: 0.0 mm  |  100%</div>
      <canvas id="sheetCanvas"></canvas>

      <div class="floating-toolbar">
        <label style="display:flex; align-items:center; gap:4px; cursor:pointer; font-weight:600;">
          <input type="checkbox" id="chkShowTables" checked onchange="drawSheet()" /> Tables
        </label>
        <label style="display:flex; align-items:center; gap:4px; cursor:pointer; font-weight:600;">
          <input type="checkbox" id="chkShowDraw" checked onchange="drawSheet()" /> Diagrams & Circuits
        </label>
        <label style="display:flex; align-items:center; gap:4px; cursor:pointer; font-weight:600;">
          <input type="checkbox" id="chkShowText" checked onchange="drawSheet()" /> Text
        </label>
        <span style="color:#cbd5e1;">|</span>
        <button id="btnSim" class="btn-accent" onclick="togglePenSimulation()">▶ Simulate Pen</button>
        <span style="color:#cbd5e1;">|</span>
        <button onclick="zoomSheet(0.85)">−</button>
        <button onclick="zoomSheet(1.18)">+</button>
        <button onclick="resetSheetView()">Fit A4</button>
      </div>
    </section>

    <section class="card">
      <div class="card-title">
        <span>2. Fonts, Handwriting & Pen</span>
      </div>

      <div class="inspector-body">
        <label class="toggle-row" style="cursor:pointer;">
          <span>1:1 Exact Document Layout</span>
          <input type="checkbox" id="chkExactLayout" onchange="compilePaths()" />
        </label>

        <div class="param-block">
          <div class="param-hdr">
            <span>Font / Handwriting Style</span>
          </div>
          <select id="selFont" onchange="compilePaths()"></select>
        </div>

        <div class="hw-clone-box">
          <div class="hw-clone-title">
            <span>📸 Add My Handwriting (Photo)</span>
            <button style="padding:2px 7px; font-size:10.5px;" onclick="downloadSampleSheet()">⬇ Guide</button>
          </div>
          <div class="hw-guide">
            Write these 4 rows on white paper &amp; upload pic:<br/>
            <b>Row 1:</b> A B C D E F G H I J K L M N O P Q R S T U V W X Y Z<br/>
            <b>Row 2:</b> a b c d e f g h i j k l m n o p q r s t u v w x y z<br/>
            <b>Row 3:</b> 0 1 2 3 4 5 6 7 8 9<br/>
            <b>Row 4:</b> . , : ; - = + * / ( ) ! ? ' "
          </div>
          <div style="display:flex; gap:6px;">
            <input type="text" id="txtFontName" value="My_Handwriting" placeholder="Style name..." style="flex:1; font-size:11.5px; padding:5px 8px;" />
            <input type="file" id="hwSampleInput" accept="image/*" style="display:none" onchange="uploadHandwritingSample(this.files[0])" />
            <button class="btn-accent" style="padding:5px 9px; font-size:11.5px;" onclick="document.getElementById('hwSampleInput').click()">Upload Pic</button>
          </div>
        </div>

        <div class="param-block">
          <div class="param-hdr">
            <span>Letter Height</span>
            <span id="valHeight" class="param-val">6.0 mm</span>
          </div>
          <input type="range" id="sldHeight" min="3" max="14" step="0.5" value="6" oninput="updateParamLabels()" />
        </div>

        <div class="param-block">
          <div class="param-hdr">
            <span>Handwriting Slant</span>
            <span id="valSlant" class="param-val">15%</span>
          </div>
          <input type="range" id="sldSlant" min="0" max="100" step="5" value="15" oninput="updateParamLabels()" />
        </div>

        <div class="param-block">
          <div class="param-hdr">
            <span>Pen Down Servo Angle</span>
            <span id="valServo" class="param-val">45°</span>
          </div>
          <input type="range" id="sldServo" min="10" max="120" step="1" value="45" oninput="updateParamLabels()" />
        </div>

        <div class="prog-bar"><div id="progFill" class="prog-fill"></div></div>

        <div class="telemetry-grid">
          <div class="stat-tile">
            <div class="stat-lbl">G-Code Lines</div>
            <div id="statLines" class="stat-num">0</div>
          </div>
          <div class="stat-tile">
            <div class="stat-lbl">Est. Plot Time</div>
            <div id="statTime" class="stat-num">0m 0s</div>
          </div>
        </div>
      </div>
    </section>

  </div>

<script>
let previewPayload = { text_strokes: [], table_strokes: [], diagram_strokes: [] };
let currentPage = 0, totalPages = 1;
let viewZoom = 1.9, viewPanX = 80, viewPanY = 22;
let isPanning = false, panStartX = 0, panStartY = 0;
let compileDebounce = null;
let simTimer = null, simProgress = 1.0;

const canvas = document.getElementById('sheetCanvas');
const ctx = canvas.getContext('2d');

window.addEventListener('load', async () => {
  fitCanvasResolution();
  window.addEventListener('resize', fitCanvasResolution);
  resetSheetView();
  await refreshPorts();
  await loadFontStyles();
  await compilePaths();
  setInterval(pollPlotterStatus, 800);
});

function fitCanvasResolution() {
  canvas.width = canvas.parentElement.clientWidth;
  canvas.height = canvas.parentElement.clientHeight;
  drawSheet();
}

async function uploadHandwritingSample(file) {
  if (!file) return;
  const styleName = document.getElementById('txtFontName').value.trim() || 'My_Handwriting';
  setStatus(`Learning your handwriting from ${file.name}...`, 'busy');

  const formData = new FormData();
  formData.append('file', file);
  formData.append('style_name', styleName);

  const res = await fetch('/api/train_handwriting', { method: 'POST', body: formData });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    setStatus('Handwriting scan failed', 'idle');
    return;
  }

  await loadFontStyles(data.style_label);
  await compilePaths();
  alert(`Learned ${data.matched_count} characters from your handwriting! Selected "${data.style_label}".`);
}

function downloadSampleSheet() {
  const txt = [
    "DRAFTMATE HANDWRITING CALIBRATION SHEET",
    "Write each row clearly on plain white paper using a dark pen, leave small gaps between letters, then take a photo and click 'Upload Pic':",
    "",
    "Row 1:  A  B  C  D  E  F  G  H  I  J  K  L  M  N  O  P  Q  R  S  T  U  V  W  X  Y  Z",
    "",
    "Row 2:  a  b  c  d  e  f  g  h  i  j  k  l  m  n  o  p  q  r  s  t  u  v  w  x  y  z",
    "",
    "Row 3:  0  1  2  3  4  5  6  7  8  9",
    "",
    "Row 4:  .  ,  :  ;  -  =  +  *  /  (  )  !  ?  '  \""
  ].join("\n");
  const blob = new Blob([txt], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'handwriting_calibration_guide.txt';
  a.click();
}

window.addEventListener('paste', async (e) => {
  const items = (e.clipboardData || e.originalEvent.clipboardData).items;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      await uploadFile(item.getAsFile());
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
  if (e.dataTransfer.files.length > 0) await uploadFile(e.dataTransfer.files[0]);
});

async function uploadFile(file) {
  if (!file) return;
  stopPenSimulation();
  setStatus(`Extracting ${file.name}...`, 'busy');

  const formData = new FormData();
  formData.append('file', file);
  formData.append('handwritten_mode', document.getElementById('chkHandwrittenMode').checked ? '1' : '0');

  const res = await fetch('/api/upload', { method: 'POST', body: formData });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    setStatus('Extraction failed', 'idle');
    return;
  }
  applyExtractedDocument(data);
}

async function reextractCurrentFile() {
  const hw = document.getElementById('chkHandwrittenMode').checked ? '1' : '0';
  setStatus('Switching extraction mode...', 'busy');
  const res = await fetch(`/api/page/${currentPage}?hw=${hw}`);
  const data = await res.json();
  if (!data.error) {
    applyExtractedDocument(data);
  } else {
    setStatus('Ready • Upload a file to extract', 'idle');
  }
}

async function changePage(delta) {
  const target = currentPage + delta;
  if (target < 0 || target >= totalPages) return;
  const hw = document.getElementById('chkHandwrittenMode').checked ? '1' : '0';
  setStatus(`Loading page ${target + 1}...`, 'busy');
  const res = await fetch(`/api/page/${target}?hw=${hw}`);
  const data = await res.json();
  if (!data.error) applyExtractedDocument(data);
}

async function extractFromUrl() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return;
  setStatus('Fetching webpage text...', 'busy');
  const res = await fetch('/api/fetch_url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    setStatus('URL fetch failed', 'idle');
    return;
  }
  document.getElementById('chkExactLayout').checked = false;
  document.getElementById('editor').value = data.text;
  await compilePaths();
}

function applyExtractedDocument(data) {
  currentPage = data.current_page;
  totalPages = data.total_pages;
  document.getElementById('lblPage').innerText = `Page ${currentPage + 1} of ${totalPages}`;
  document.getElementById('btnPrev').disabled = currentPage <= 0;
  document.getElementById('btnNext').disabled = currentPage >= totalPages - 1;
  document.getElementById('chkExactLayout').checked = data.is_exact_layout;
  document.getElementById('editor').value = data.plain_text;
  compilePaths();
}

function updateParamLabels() {
  document.getElementById('valHeight').innerText = `${parseFloat(document.getElementById('sldHeight').value).toFixed(1)} mm`;
  document.getElementById('valSlant').innerText = `${document.getElementById('sldSlant').value}%`;
  document.getElementById('valServo').innerText = `${document.getElementById('sldServo').value}°`;
  scheduleCompile();
}

function scheduleCompile() {
  clearTimeout(compileDebounce);
  compileDebounce = setTimeout(compilePaths, 220);
}

async function compilePaths() {
  const payload = {
    text: document.getElementById('editor').value,
    style: document.getElementById('selFont').value,
    letter_h: parseFloat(document.getElementById('sldHeight').value),
    slant: parseFloat(document.getElementById('sldSlant').value),
    servo: parseInt(document.getElementById('sldServo').value),
    exact_mode: document.getElementById('chkExactLayout').checked,
    show_tables: document.getElementById('chkShowTables').checked,
    show_diagrams: document.getElementById('chkShowDraw').checked,
    show_text: document.getElementById('chkShowText').checked
  };

  const res = await fetch('/api/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  previewPayload = data;
  document.getElementById('statLines').innerText = data.gcode_count.toLocaleString();
  document.getElementById('statTime').innerText = data.est_time;
  setStatus(`Ready • ${data.gcode_count} G-code lines compiled`, data.connected ? 'online' : 'idle');

  simProgress = 1.0;
  drawSheet();
}

function drawSheet() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const wPx = 210 * viewZoom, hPx = 297 * viewZoom;

  ctx.fillStyle = 'rgba(15, 23, 42, 0.22)';
  ctx.fillRect(viewPanX + 8, viewPanY + 10, wPx, hPx);
  ctx.fillStyle = '#fffdf9';
  ctx.fillRect(viewPanX, viewPanY, wPx, hPx);
  ctx.strokeStyle = '#94a3b8';
  ctx.lineWidth = 1.5;
  ctx.strokeRect(viewPanX, viewPanY, wPx, hPx);

  if (!document.getElementById('chkExactLayout').checked) {
    ctx.strokeStyle = '#fda4af';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(viewPanX + 20 * viewZoom, viewPanY);
    ctx.lineTo(viewPanX + 20 * viewZoom, viewPanY + hPx);
    ctx.stroke();

    ctx.strokeStyle = '#bae6fd';
    for (let y = 30; y < 290; y += 10) {
      ctx.beginPath();
      ctx.moveTo(viewPanX, viewPanY + y * viewZoom);
      ctx.lineTo(viewPanX + wPx, viewPanY + y * viewZoom);
      ctx.stroke();
    }
  }

  const combined = [];
  if (document.getElementById('chkShowTables').checked) combined.push(...previewPayload.table_strokes);
  if (document.getElementById('chkShowDraw').checked) combined.push(...previewPayload.diagram_strokes);
  if (document.getElementById('chkShowText').checked) combined.push(...previewPayload.text_strokes);

  const limitCount = Math.floor(combined.length * simProgress);

  ctx.strokeStyle = '#0f172a';
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(0.85, 0.52 * viewZoom);

  ctx.beginPath();
  for (let idx = 0; idx < limitCount; idx++) {
    const s = combined[idx];
    if (s.length < 2) continue;
    ctx.moveTo(viewPanX + s[0][0] * viewZoom, viewPanY + s[0][1] * viewZoom);
    for (let i = 1; i < s.length; i++) {
      ctx.lineTo(viewPanX + s[i][0] * viewZoom, viewPanY + s[i][1] * viewZoom);
    }
  }
  ctx.stroke();

  if (simProgress < 1.0 && limitCount > 0) {
    const lastStroke = combined[limitCount - 1];
    const tip = lastStroke[lastStroke.length - 1];
    ctx.fillStyle = '#d97706';
    ctx.beginPath();
    ctx.arc(viewPanX + tip[0] * viewZoom, viewPanY + tip[1] * viewZoom, 5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function togglePenSimulation() {
  if (simTimer) {
    stopPenSimulation();
    simProgress = 1.0;
    drawSheet();
    return;
  }
  simProgress = 0.0;
  document.getElementById('btnSim').innerText = '■ Stop Sim';
  simTimer = setInterval(() => {
    simProgress += 0.015;
    if (simProgress >= 1.0) {
      stopPenSimulation();
      simProgress = 1.0;
    }
    drawSheet();
  }, 25);
}

function stopPenSimulation() {
  if (simTimer) { clearInterval(simTimer); simTimer = null; }
  document.getElementById('btnSim').innerText = '▶ Simulate Pen';
}

function zoomSheet(factor) { viewZoom = Math.max(0.7, Math.min(12, viewZoom * factor)); drawSheet(); }
function resetSheetView() {
  const stageH = canvas.parentElement.clientHeight || 600;
  const stageW = canvas.parentElement.clientWidth || 700;
  viewZoom = Math.max(1.2, (stageH - 80) / 297.0);
  viewPanX = (stageW - 210 * viewZoom) / 2;
  viewPanY = 24;
  drawSheet();
}

canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.15 : 0.87;
  const oldZ = viewZoom;
  viewZoom = Math.max(0.7, Math.min(12, viewZoom * factor));
  viewPanX = e.offsetX - (e.offsetX - viewPanX) * (viewZoom / oldZ);
  viewPanY = e.offsetY - (e.offsetY - viewPanY) * (viewZoom / oldZ);
  drawSheet();
});

canvas.addEventListener('mousedown', (e) => { isPanning = true; panStartX = e.clientX - viewPanX; panStartY = e.clientY - viewPanY; });
window.addEventListener('mouseup', () => { isPanning = false; });
canvas.addEventListener('mousemove', (e) => {
  if (isPanning) { viewPanX = e.clientX - panStartX; viewPanY = e.clientY - panStartY; drawSheet(); }
  const mx = ((e.offsetX - viewPanX) / viewZoom).toFixed(1);
  const my = ((e.offsetY - viewPanY) / viewZoom).toFixed(1);
  document.getElementById('coordHud').innerText = `X: ${mx} mm  Y: ${my} mm  |  ${Math.round((viewZoom / 1.9) * 100)}%`;
});

async function refreshPorts() {
  const res = await fetch('/api/ports');
  const data = await res.json();
  const sel = document.getElementById('portSelect');
  sel.innerHTML = data.ports.map(p => `<option value="${p}">${p}</option>`).join('');
}

async function loadFontStyles(selectStyle = null) {
  const res = await fetch('/api/styles');
  const data = await res.json();
  const sel = document.getElementById('selFont');
  sel.innerHTML = data.styles.map(s => `<option value="${s}">${s}</option>`).join('');
  if (selectStyle && data.styles.includes(selectStyle)) {
    sel.value = selectStyle;
  }
}

async function toggleBackendUsb() {
  const port = document.getElementById('portSelect').value;
  const res = await fetch('/api/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port })
  });
  const data = await res.json();
  document.getElementById('btnUsb').innerText = data.connected ? '🔌 Disconnect' : '🔌 Connect COM';
  setStatus(data.message, data.connected ? 'online' : 'idle');
}

function downloadGcode() {
  window.location.href = '/api/download_gcode';
}

async function startPlotter() {
  const res = await fetch('/api/start', { method: 'POST' });
  const data = await res.json();
  if (data.error) {
    alert(data.error);
    return;
  }
  document.getElementById('btnRun').disabled = true;
  document.getElementById('btnStop').disabled = false;
}

async function stopPlotter() {
  await fetch('/api/stop', { method: 'POST' });
}

async function pollPlotterStatus() {
  const res = await fetch('/api/status');
  const data = await res.json();
  document.getElementById('progFill').style.width = `${data.progress}%`;
  if (data.is_plotting) {
    setStatus(data.status, 'busy');
  } else {
    document.getElementById('btnRun').disabled = false;
    document.getElementById('btnStop').disabled = true;
  }
}

function setStatus(text, mode) {
  document.getElementById('statusText').innerText = text;
  const dot = document.getElementById('statusDot');
  dot.className = 'dot' + (mode === 'online' ? ' online' : (mode === 'busy' ? ' busy' : ''));
}
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(DRAFTMATE_HTML)


@app.route("/api/ports")
def api_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return jsonify({"ports": ports if ports else ["No COM Ports"]})


@app.route("/api/styles")
def api_styles():
    custom_files = sorted(glob.glob(os.path.join(FONTS_DIR, "*.json")))
    custom_labels = [f"[My Handwriting] {os.path.splitext(os.path.basename(f))[0]}" for f in custom_files]

    word_labels = [
        f"[Word / System] {label}"
        for label in WORD_SYSTEM_FONTS
        if _find_windows_font_path(label) is not None
    ]

    vector_labels = ["[Vector] Built-in Neat Script"]
    if HAS_HERSHEY:
        vector_labels.extend([
            "[Hershey] Cursive Script (scriptc)",
            "[Hershey] Flowing Script (scripts)",
            "[Hershey] Clean Sans (futural)",
            "[Hershey] Classic Serif (timesr)",
        ])

    return jsonify({"styles": custom_labels + word_labels + vector_labels})


@app.route("/api/train_handwriting", methods=["POST"])
def api_train_handwriting():
    if "file" not in request.files:
        return jsonify({"error": "No image received"})
    f = request.files["file"]
    style_name = request.form.get("style_name", "My_Handwriting").strip() or "My_Handwriting"
    ext = os.path.splitext(f.filename or "sample.png")[1].lower() or ".png"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    f.save(tmp.name)
    tmp.close()

    try:
        saved_name, matched_count = HandwritingFontBuilder.build_font_from_image(
            tmp.name, style_name, fonts_dir=FONTS_DIR
        )
        return jsonify({
            "style_label": f"[My Handwriting] {saved_name}",
            "matched_count": matched_count
        })
    except Exception as e:
        return jsonify({"error": str(e)})
    finally:
        if os.path.exists(tmp.name):
            os.remove(tmp.name)


@app.route("/api/connect", methods=["POST"])
def api_connect():
    if state["serial_conn"] and state["serial_conn"].is_open:
        state["serial_conn"].close()
        state["serial_conn"] = None
        return jsonify({"connected": False, "message": "Disconnected from USB"})

    port = request.json.get("port", "")
    if "No COM" in port or not port:
        return jsonify({"connected": False, "message": "Select a valid COM port first"})

    try:
        conn = serial.Serial(port, 115200, timeout=2)
        time.sleep(2)
        conn.flushInput()
        state["serial_conn"] = conn
        return jsonify({"connected": True, "message": f"Arduino Connected ({port})"})
    except Exception as e:
        return jsonify({"connected": False, "message": f"Error: {str(e)[:38]}"})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file received"})
    f = request.files["file"]
    hw_mode = request.form.get("handwritten_mode", "0") == "1"
    ext = os.path.splitext(f.filename or "clipboard.png")[1].lower() or ".png"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    f.save(tmp.name)
    tmp.close()

    state["temp_file_path"] = tmp.name
    return _load_page(0, hw_mode=hw_mode)


@app.route("/api/page/<int:page_num>")
def api_page(page_num):
    hw_mode = request.args.get("hw", "0") == "1"
    return _load_page(page_num, hw_mode=hw_mode)


def _load_page(page_num, hw_mode=False):
    path = state.get("temp_file_path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "No document loaded"})
    try:
        if hw_mode:
            data = UniversalDocumentExtractor.extract_handwritten_notes(path, page_num)
        else:
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
        return jsonify({"text": "\n\n".join(clean_lines[:45])})
    except Exception as e:
        return jsonify({"error": f"Could not extract webpage: {e}"})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    cfg = request.json
    letter_h = float(cfg.get("letter_h", 6.0))
    slant_amt = float(cfg.get("slant", 15.0)) / 100.0
    pen_down_angle = int(cfg.get("servo", 45))
    selected = cfg.get("style", "[Vector] Built-in Neat Script")

    custom_glyphs = {}
    is_custom = selected.startswith("[My Handwriting] ")
    is_word_font = selected.startswith("[Word / System] ")
    is_hershey = selected.startswith("[Hershey] ") and HAS_HERSHEY

    hf = None
    if is_custom:
        style_name = selected.replace("[My Handwriting] ", "", 1)
        json_path = os.path.join(FONTS_DIR, f"{style_name}.json")
        if os.path.exists(json_path):
            with open(json_path, "r") as f:
                custom_glyphs = json.load(f)
    elif is_hershey:
        hf = HersheyFonts()
        code = selected.split("(")[-1].rstrip(")")
        hf.load_default_font(code)

    def get_glyph(ch, h_mm):
        if is_custom:
            raw = custom_glyphs.get(ch) or BUILTIN_GLYPHS.get(ch) or BUILTIN_GLYPHS.get(ch.upper(), [])
            return [[(x * h_mm, y * h_mm) for x, y in s] for s in raw]
        elif is_word_font:
            word_label = selected.replace("[Word / System] ", "", 1)
            raw = _get_word_font_glyph(word_label, ch)
            return [[(x * h_mm, y * h_mm) for x, y in s] for s in raw]
        elif is_hershey and hf is not None:
            hf.normalize_rendering(h_mm)
            raw_strokes = list(hf.strokes_for_text(ch))
            if not raw_strokes:
                return []
            min_x = min(p[0] for s in raw_strokes for p in s)
            return [[(p[0] - min_x, -p[1]) for p in s] for s in raw_strokes]
        else:
            raw = BUILTIN_GLYPHS.get(ch) or BUILTIN_GLYPHS.get(ch.upper(), [])
            return [[(x * h_mm, y * h_mm) for x, y in s] for s in raw]

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
            glyph = get_glyph(ch, h_mm)
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
            glyph = get_glyph(ch, letter_h)
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
    connected = bool(state["serial_conn"] and state["serial_conn"].is_open)

    return jsonify({
        "text_strokes": text_strokes,
        "table_strokes": table_strokes,
        "diagram_strokes": diagram_strokes,
        "gcode_count": len(gcode),
        "est_time": f"{mins}m {secs}s",
        "connected": connected
    })


@app.route("/api/download_gcode")
def api_download_gcode():
    content = "\n".join(state["gcode_lines"])
    return app.response_class(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": "attachment;filename=draftmate_a4_plot.gcode"}
    )


@app.route("/api/start", methods=["POST"])
def api_start():
    if not state["serial_conn"] or not state["serial_conn"].is_open:
        return jsonify({"error": "Connect your Arduino COM port in the top ribbon first!"})
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
            state["status"] = "Emergency Stopped"
            break
        conn.write((line + "\n").encode())
        while True:
            resp = conn.readline().decode().strip()
            if resp == "ok" or "error" in resp:
                break
        state["progress"] = int(((i + 1) / total) * 100)
        state["status"] = f"Plotting • {state['progress']}% ({i + 1}/{total})"
    else:
        state["status"] = "Completed A4 Plot!"
    state["is_plotting"] = False


if __name__ == "__main__":
    threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False)