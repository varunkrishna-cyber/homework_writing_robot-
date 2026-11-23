import glob
import json
import math
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import serial
import serial.tools.list_ports
from HersheyFonts import HersheyFonts
from handwriting_recorder import HandwritingRecorder
from photo_handwriting_extractor import PhotoHandwritingExtractor
from ttf_font_converter import TTFFontConverter
from universal_doc_extractor import UniversalDocumentExtractor

# Modern Slate-Dark Theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

FONTS_DIR = "fonts"
INK_COLOR = "#111111"  # Crisp black pen ink


def render_style_sample(canvas, style_label, sample_text="Abc 123", height_mm=16.0, start_x=10, baseline_y=26):
    """Draws a smooth single-line stroke preview of any custom or built-in style onto a tk.Canvas."""
    canvas.delete("all")
    canvas.create_line(0, baseline_y, 300, baseline_y, fill="#d2e3fc", width=1)

    is_custom = style_label.startswith("[Custom] ")
    custom_glyphs = {}
    fallback_name = "scripts"

    if is_custom:
        style_name = style_label.replace("[Custom] ", "", 1)
        json_path = os.path.join(FONTS_DIR, f"{style_name}.json")
        if os.path.exists(json_path):
            with open(json_path, "r") as f:
                custom_glyphs = json.load(f)
    else:
        fallback_name = style_label.replace("[Built-in] ", "", 1)

    font = HersheyFonts()
    font.load_default_font(fallback_name)
    font.normalize_rendering(height_mm)

    cursor_x = start_x
    for char in sample_text:
        if char == " ":
            cursor_x += height_mm * 0.5
            continue

        if is_custom and char in custom_glyphs:
            strokes = [[(x * height_mm, y * height_mm) for x, y in s] for s in custom_glyphs[char]]
        else:
            strokes = list(font.strokes_for_text(char))

        max_x = 0
        for stroke in strokes:
            if not stroke:
                continue
            pts = []
            for x, y in stroke:
                px = cursor_x + x
                py = baseline_y - y
                pts.append((px, py))
                if x > max_x:
                    max_x = x
            if len(pts) > 1:
                canvas.create_line(pts, fill=INK_COLOR, width=1.4, smooth=True, capstyle=tk.ROUND)

        cursor_x += (max_x if max_x > 0 else height_mm * 0.45) + 2.5


class StylePickerPopup(ctk.CTkToplevel):
    """Scrollable visual gallery showing each handwriting style name beside a live stroke preview."""
    def __init__(self, parent, all_styles, current_style, on_select):
        super().__init__(parent)
        self.title("Select Handwriting Style")
        self.geometry("540x560")
        self.grab_set()
        self.on_select = on_select

        ctk.CTkLabel(
            self,
            text="Click a Handwriting Style to Select:",
            font=("Segoe UI", 16, "bold")
        ).pack(pady=(12, 8))

        scroll = ctk.CTkScrollableFrame(self, width=500, height=480)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for style in all_styles:
            is_selected = (style == current_style)
            row = ctk.CTkFrame(
                scroll,
                fg_color="#233142" if is_selected else "#181c24",
                border_width=2 if is_selected else 1,
                border_color="#38bdf8" if is_selected else "#2d333b"
            )
            row.pack(fill="x", pady=4, padx=4)

            name_lbl = ctk.CTkLabel(
                row,
                text=style,
                font=("Segoe UI", 13, "bold" if is_selected else "normal"),
                width=220,
                anchor="w"
            )
            name_lbl.pack(side="left", padx=(12, 6), pady=8)

            preview_canvas = tk.Canvas(
                row,
                width=220,
                height=36,
                bg="#fdfbf7",
                highlightthickness=1,
                highlightbackground="#666"
            )
            preview_canvas.pack(side="right", padx=10, pady=6)
            render_style_sample(preview_canvas, style, sample_text="Abc 123", height_mm=15.0, start_x=8, baseline_y=26)

            for widget in (row, name_lbl, preview_canvas):
                widget.bind("<Button-1>", lambda e, s=style: self._pick(s))
                widget.configure(cursor="hand2")

    def _pick(self, style):
        self.on_select(style)
        self.destroy()


class HomeworkWriterApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Auto-Homework Pen Plotter Studio")
        self.geometry("1280x820")

        os.makedirs(FONTS_DIR, exist_ok=True)
        if os.path.exists("my_handwriting.json") and not os.path.exists(os.path.join(FONTS_DIR, "My_Handwriting.json")):
            os.replace("my_handwriting.json", os.path.join(FONTS_DIR, "My_Handwriting.json"))

        if not glob.glob(os.path.join(FONTS_DIR, "*.json")):
            with open(os.path.join(FONTS_DIR, "My_Handwriting.json"), "w") as f:
                json.dump({}, f)

        # Hardware & Plotter State
        self.serial_conn = None
        self.is_plotting = False
        self.stop_requested = False
        self.gcode_lines = []
        self.all_styles = []
        self.current_style = "[Custom] My_Handwriting"

        # Extracted Document State
        self.current_file_path = None
        self.current_page = 0
        self.total_pages = 0
        self.doc_text_items = []
        self.doc_table_lines_mm = []
        self.doc_diagram_strokes_mm = []

        # Layer & View Controls
        self.exact_layout_mode = tk.BooleanVar(value=False)
        self.show_text_layer = tk.BooleanVar(value=True)
        self.show_table_layer = tk.BooleanVar(value=True)
        self.show_diagram_layer = tk.BooleanVar(value=True)
        self.show_grid_rulers = tk.BooleanVar(value=True)

        # Zoom & Pan Viewport State (pixels per mm; base = 2.0 px/mm)
        self.zoom_scale = 2.0
        self.pan_x = 36.0
        self.pan_y = 28.0
        self._drag_start = None

        # Cached strokes in mm for fast redraw on zoom/pan
        self.mm_text_strokes = []
        self.mm_table_strokes = []
        self.mm_diagram_strokes = []

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()
        self.refresh_ports()
        self.refresh_font_list()
        self.generate_paths()

    # ==========================================================
    # LEFT SIDEBAR (CONTROLS)
    # ==========================================================
    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=305, corner_radius=12, fg_color="#141820")
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(self.sidebar, text="✒ Plotter Studio", font=("Segoe UI", 21, "bold"), text_color="#38bdf8").pack(pady=(12, 6))

        # --- 1. Hardware Connection ---
        ctk.CTkLabel(self.sidebar, text="1. HARDWARE CONNECTION", font=("Segoe UI", 11, "bold"), text_color="#94a3b8").pack(anchor="w", padx=16, pady=(6, 2))
        
        port_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        port_frame.pack(fill="x", padx=15, pady=3)
        
        self.port_menu = ctk.CTkOptionMenu(port_frame, values=["No Ports"], width=180, fg_color="#1e293b")
        self.port_menu.pack(side="left", padx=(0, 5))
        
        ctk.CTkButton(port_frame, text="↻", width=40, fg_color="#334155", command=self.refresh_ports).pack(side="left")

        self.connect_btn = ctk.CTkButton(self.sidebar, text="Connect Arduino", fg_color="#16a34a", hover_color="#15803d", command=self.toggle_connection)
        self.connect_btn.pack(fill="x", padx=15, pady=3)

        # --- 2. Handwriting Styles + Live Preview ---
        ctk.CTkLabel(self.sidebar, text="2. HANDWRITING STYLE", font=("Segoe UI", 11, "bold"), text_color="#94a3b8").pack(anchor="w", padx=16, pady=(10, 2))

        style_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        style_row.pack(fill="x", padx=15, pady=3)

        self.style_btn = ctk.CTkButton(
            style_row,
            text="[Custom] My_Handwriting ▾",
            width=185,
            fg_color="#1e293b",
            hover_color="#334155",
            anchor="w",
            command=self.open_style_picker
        )
        self.style_btn.pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            style_row,
            text="+ New",
            width=55,
            fg_color="#0d9488",
            hover_color="#0f766e",
            command=self.create_new_style
        ).pack(side="left")

        self.style_preview_canvas = tk.Canvas(
            self.sidebar,
            width=265,
            height=38,
            bg="#fdfbf7",
            highlightthickness=1,
            highlightbackground="#475569"
        )
        self.style_preview_canvas.pack(padx=15, pady=(2, 6))
        self.style_preview_canvas.bind("<Button-1>", lambda e: self.open_style_picker())
        self.style_preview_canvas.configure(cursor="hand2")

        ctk.CTkButton(
            self.sidebar,
            text="🗚 Import Word / TTF Font",
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self.open_ttf_importer
        ).pack(fill="x", padx=15, pady=(2, 3))

        ctk.CTkButton(
            self.sidebar,
            text="📷 Import Handwriting Photo",
            fg_color="#6d28d9",
            hover_color="#5b21b6",
            command=self.open_photo_importer
        ).pack(fill="x", padx=15, pady=(0, 3))

        ctk.CTkButton(
            self.sidebar,
            text="✎ Draw / Edit Letters",
            fg_color="#4338ca",
            hover_color="#3730a3",
            command=self.open_manual_recorder
        ).pack(fill="x", padx=15, pady=(0, 6))

        # Sliders
        self.size_label = ctk.CTkLabel(self.sidebar, text="Letter Height: 6.0 mm", font=("Segoe UI", 12))
        self.size_label.pack(anchor="w", padx=16)
        self.size_slider = ctk.CTkSlider(
            self.sidebar, from_=3.0, to=14.0,
            command=lambda v: self.size_label.configure(text=f"Letter Height: {v:.1f} mm")
        )
        self.size_slider.set(6.0)
        self.size_slider.pack(fill="x", padx=15, pady=2)

        self.chaos_label = ctk.CTkLabel(self.sidebar, text="Handwriting Slant: 15%", font=("Segoe UI", 12))
        self.chaos_label.pack(anchor="w", padx=16)
        self.chaos_slider = ctk.CTkSlider(
            self.sidebar, from_=0, to=100,
            command=lambda v: self.chaos_label.configure(text=f"Handwriting Slant: {int(v)}%")
        )
        self.chaos_slider.set(15)
        self.chaos_slider.pack(fill="x", padx=15, pady=2)

        self.servo_label = ctk.CTkLabel(self.sidebar, text="Pen Down Servo Angle: 45°", font=("Segoe UI", 12))
        self.servo_label.pack(anchor="w", padx=16)
        self.servo_slider = ctk.CTkSlider(
            self.sidebar, from_=10, to=120,
            command=lambda v: self.servo_label.configure(text=f"Pen Down Servo Angle: {int(v)}°")
        )
        self.servo_slider.set(45)
        self.servo_slider.pack(fill="x", padx=15, pady=2)

        # --- 3. Execution ---
        ctk.CTkLabel(self.sidebar, text="3. EXECUTION", font=("Segoe UI", 11, "bold"), text_color="#94a3b8").pack(anchor="w", padx=16, pady=(10, 3))

        ctk.CTkButton(self.sidebar, text="⟳ Regenerate Preview", fg_color="#334155", hover_color="#475569", command=self.generate_paths).pack(fill="x", padx=15, pady=3)
        
        self.start_btn = ctk.CTkButton(self.sidebar, text="▶ Start Writing on Plotter", fg_color="#2563eb", hover_color="#1d4ed8", font=("Segoe UI", 13, "bold"), command=self.start_plotting_thread)
        self.start_btn.pack(fill="x", padx=15, pady=3)

        self.stop_btn = ctk.CTkButton(self.sidebar, text="■ Emergency Stop", fg_color="#dc2626", state="disabled", command=self.stop_plotting)
        self.stop_btn.pack(fill="x", padx=15, pady=3)

        self.progress = ctk.CTkProgressBar(self.sidebar)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=15, pady=(10, 3))

        self.status_label = ctk.CTkLabel(self.sidebar, text="Status: Ready", text_color="#94a3b8", font=("Segoe UI", 11), wraplength=265)
        self.status_label.pack(pady=3)

    # ==========================================================
    # RIGHT WORKSPACE (EDITOR + HIGH-PRECISION A4 VIEWER)
    # ==========================================================
    def _build_main_area(self):
        self.main_frame = ctk.CTkFrame(self, corner_radius=12, fg_color="#141820")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)
        self.main_frame.grid_columnconfigure(0, weight=4)
        self.main_frame.grid_columnconfigure(1, weight=6)
        self.main_frame.grid_rowconfigure(2, weight=1)

        # --- Top Universal Document Import Bar ---
        doc_bar = ctk.CTkFrame(self.main_frame, fg_color="#1e293b", corner_radius=8)
        doc_bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))

        ctk.CTkButton(
            doc_bar,
            text="📂 Import File (PDF / Image / Word / TXT)",
            fg_color="#ea580c",
            hover_color="#c2410c",
            font=("Segoe UI", 13, "bold"),
            command=self.upload_any_file
        ).pack(side="left", padx=8, pady=7)

        self.prev_page_btn = ctk.CTkButton(doc_bar, text="◀", width=34, state="disabled", command=self.prev_page)
        self.prev_page_btn.pack(side="left", padx=2)

        self.page_lbl = ctk.CTkLabel(doc_bar, text="No document loaded", text_color="#94a3b8", font=("Segoe UI", 12))
        self.page_lbl.pack(side="left", padx=8)

        self.next_page_btn = ctk.CTkButton(doc_bar, text="▶", width=34, state="disabled", command=self.next_page)
        self.next_page_btn.pack(side="left", padx=2)

        self.exact_chk = ctk.CTkCheckBox(
            doc_bar,
            text="1:1 Exact Layout Mode",
            variable=self.exact_layout_mode,
            font=("Segoe UI", 12, "bold"),
            command=self.generate_paths
        )
        self.exact_chk.pack(side="right", padx=12)

        # --- Column Headers ---
        ctk.CTkLabel(self.main_frame, text="Extracted / Editable Text:", font=("Segoe UI", 13, "bold"), text_color="#e2e8f0").grid(row=1, column=0, sticky="w", padx=12, pady=2)

        # Preview Toolbar (Layer Toggles + Zoom Controls)
        preview_hdr = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        preview_hdr.grid(row=1, column=1, sticky="ew", padx=10, pady=2)

        ctk.CTkCheckBox(preview_hdr, text="Text", width=55, variable=self.show_text_layer, command=self.generate_paths).pack(side="left", padx=3)
        ctk.CTkCheckBox(preview_hdr, text="Tables", width=65, variable=self.show_table_layer, command=self.generate_paths).pack(side="left", padx=3)
        ctk.CTkCheckBox(preview_hdr, text="Diagrams", width=75, variable=self.show_diagram_layer, command=self.generate_paths).pack(side="left", padx=3)
        ctk.CTkCheckBox(preview_hdr, text="Rulers", width=60, variable=self.show_grid_rulers, command=self._redraw_viewport).pack(side="left", padx=3)

        ctk.CTkButton(preview_hdr, text="Reset", width=48, height=24, fg_color="#334155", command=self.reset_zoom).pack(side="right", padx=2)
        ctk.CTkButton(preview_hdr, text="+", width=28, height=24, fg_color="#334155", command=lambda: self.adjust_zoom(1.25)).pack(side="right", padx=2)
        ctk.CTkButton(preview_hdr, text="-", width=28, height=24, fg_color="#334155", command=lambda: self.adjust_zoom(0.8)).pack(side="right", padx=2)

        # --- Left Pane: Text Editor ---
        self.textbox = ctk.CTkTextbox(self.main_frame, font=("Consolas", 13), wrap="word", fg_color="#0f172a", border_width=1, border_color="#334155")
        self.textbox.grid(row=2, column=0, sticky="nsew", padx=(10, 6), pady=(0, 6))
        self.textbox.insert("1.0", "Experiment 1: Ohm's Law\n\nAim: To verify the relationship between voltage and current.\n\nFormula: V = I * R")

        # --- Right Pane: Zoomable A4 Canvas ---
        canvas_container = ctk.CTkFrame(self.main_frame, fg_color="#0b0f19", corner_radius=8, border_width=1, border_color="#334155")
        canvas_container.grid(row=2, column=1, sticky="nsew", padx=(6, 10), pady=(0, 6))
        canvas_container.grid_rowconfigure(0, weight=1)
        canvas_container.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(canvas_container, bg="#0b0f19", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)

        # Interactive Zoom & Pan Bindings
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<ButtonPress-3>", self._on_pan_start)
        self.canvas.bind("<B3-Motion>", self._on_pan_move)
        self.canvas.bind("<Motion>", self._on_mouse_move)
        self.canvas.bind("<Configure>", lambda e: self._redraw_viewport())

        # --- Bottom Telemetry Status Bar ---
        telemetry_bar = ctk.CTkFrame(self.main_frame, fg_color="#1e293b", height=28, corner_radius=6)
        telemetry_bar.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

        self.coord_lbl = ctk.CTkLabel(telemetry_bar, text="Cursor: (0.0 mm, 0.0 mm)  |  Zoom: 100%", font=("Consolas", 11), text_color="#38bdf8")
        self.coord_lbl.pack(side="left", padx=10, pady=2)

        self.stats_lbl = ctk.CTkLabel(telemetry_bar, text="Tables: 0 lines | Diagrams: 0 paths | Est. Time: 0s", font=("Consolas", 11), text_color="#cbd5e1")
        self.stats_lbl.pack(side="right", padx=10, pady=2)

    # ==========================================================
    # VIEWPORT ZOOM, PAN & A4 RENDERING
    # ==========================================================
    def adjust_zoom(self, factor):
        self.zoom_scale = max(1.0, min(12.0, self.zoom_scale * factor))
        self._redraw_viewport()

    def reset_zoom(self):
        self.zoom_scale = 2.0
        self.pan_x = 36.0
        self.pan_y = 28.0
        self._redraw_viewport()

    def _on_mousewheel(self, event):
        factor = 1.15 if event.delta > 0 else 0.87
        old_zoom = self.zoom_scale
        new_zoom = max(1.0, min(12.0, old_zoom * factor))

        self.pan_x = event.x - (event.x - self.pan_x) * (new_zoom / old_zoom)
        self.pan_y = event.y - (event.y - self.pan_y) * (new_zoom / old_zoom)
        self.zoom_scale = new_zoom
        self._redraw_viewport()

    def _on_pan_start(self, event):
        self._drag_start = (event.x - self.pan_x, event.y - self.pan_y)

    def _on_pan_move(self, event):
        if self._drag_start:
            self.pan_x = event.x - self._drag_start[0]
            self.pan_y = event.y - self._drag_start[1]
            self._redraw_viewport()

    def _on_mouse_move(self, event):
        mm_x = (event.x - self.pan_x) / self.zoom_scale
        mm_y = (event.y - self.pan_y) / self.zoom_scale
        zoom_pct = int((self.zoom_scale / 2.0) * 100)
        self.coord_lbl.configure(text=f"Cursor: ({mm_x:5.1f} mm, {mm_y:5.1f} mm)  |  Zoom: {zoom_pct}%  (Wheel: Zoom, Right-Drag: Pan)")

    def _redraw_viewport(self):
        self.canvas.delete("all")
        z = self.zoom_scale
        ox, oy = self.pan_x, self.pan_y
        a4_w_px = 210.0 * z
        a4_h_px = 297.0 * z

        # 1. Soft Paper Drop Shadow
        self.canvas.create_rectangle(ox + 6, oy + 6, ox + a4_w_px + 6, oy + a4_h_px + 6, fill="#04060b", outline="")

        # 2. Crisp Ivory A4 Sheet
        self.canvas.create_rectangle(ox, oy, ox + a4_w_px, oy + a4_h_px, fill="#fdfbf7", outline="#64748b", width=1.5)

        # 3. Precision Millimeter Rulers & Optional Ruled Lines
        if self.show_grid_rulers.get():
            for mm in range(0, 211, 10):
                px = ox + mm * z
                tick_len = 8 if mm % 50 == 0 else 4
                self.canvas.create_line(px, oy - tick_len, px, oy, fill="#64748b", width=1)
                if mm % 50 == 0:
                    self.canvas.create_text(px, oy - 14, text=f"{mm}", fill="#94a3b8", font=("Consolas", 8))

            for mm in range(0, 298, 10):
                py = oy + mm * z
                tick_len = 8 if mm % 50 == 0 else 4
                self.canvas.create_line(ox - tick_len, py, ox, py, fill="#64748b", width=1)
                if mm % 50 == 0:
                    self.canvas.create_text(ox - 18, py, text=f"{mm}", fill="#94a3b8", font=("Consolas", 8))

            if not self.exact_layout_mode.get():
                self.canvas.create_line(ox + 20 * z, oy, ox + 20 * z, oy + a4_h_px, fill="#fca5a5", width=1.2)
                for y_mm in range(30, 290, 10):
                    py = oy + y_mm * z
                    self.canvas.create_line(ox, py, ox + a4_w_px, py, fill="#dbeafe", width=1)

        stroke_w = max(1.0, 0.58 * z)

        # 4. Draw Table Borders Layer (Ruler-Straight Black Ink)
        if self.show_table_layer.get():
            for seg in self.mm_table_strokes:
                pts = [(ox + x * z, oy + y * z) for x, y in seg]
                if len(pts) > 1:
                    self.canvas.create_line(
                        pts,
                        fill=INK_COLOR,
                        width=stroke_w * 1.15,
                        capstyle=tk.ROUND,
                        joinstyle=tk.MITER
                    )

        # 5. Draw Diagrams Layer (Crisp Straight Vectors in Black Ink)
        if self.show_diagram_layer.get():
            for stroke in self.mm_diagram_strokes:
                pts = [(ox + x * z, oy + y * z) for x, y in stroke]
                if len(pts) > 1:
                    self.canvas.create_line(
                        pts,
                        fill=INK_COLOR,
                        width=stroke_w,
                        smooth=False,
                        capstyle=tk.ROUND,
                        joinstyle=tk.MITER
                    )

        # 6. Draw Handwriting Layer (Smooth Cursive Black Pen Ink)
        if self.show_text_layer.get():
            for stroke in self.mm_text_strokes:
                pts = [(ox + x * z, oy + y * z) for x, y in stroke]
                if len(pts) > 1:
                    self.canvas.create_line(
                        pts,
                        fill=INK_COLOR,
                        width=stroke_w,
                        smooth=True,
                        capstyle=tk.ROUND
                    )

    # ==========================================================
    # UNIVERSAL FILE UPLOAD (PDF, IMAGE, DOCX, TXT)
    # ==========================================================
    def upload_any_file(self):
        path = filedialog.askopenfilename(
            title="Select Homework File (PDF, Image, Word, or Text)",
            filetypes=[
                ("All Supported Homework Files", "*.pdf *.png *.jpg *.jpeg *.bmp *.webp *.docx *.txt"),
                ("PDF Documents", "*.pdf"),
                ("Images & Photos", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("Word Documents", "*.docx"),
                ("Text Files", "*.txt"),
            ]
        )
        if not path:
            return

        self.current_file_path = path
        self.current_page = 0
        self._load_document_page(0)

    def _load_document_page(self, page_num):
        if not self.current_file_path:
            return

        self.status_label.configure(text="Extracting text, tables & diagrams...", text_color="#f59e0b")
        self.update()

        try:
            data = UniversalDocumentExtractor.extract_file(self.current_file_path, page_num)
            self.total_pages = data["total_pages"]
            self.current_page = page_num
            self.doc_text_items = data["text_items"]
            self.doc_table_lines_mm = data["table_lines_mm"]
            self.doc_diagram_strokes_mm = data["diagram_strokes_mm"]

            self.exact_layout_mode.set(data["is_exact_layout"])

            self.textbox.delete("1.0", "end")
            self.textbox.insert("1.0", data["plain_text"])

            fname = os.path.basename(self.current_file_path)
            self.page_lbl.configure(
                text=f"{fname[:18]} | Pg {page_num + 1}/{self.total_pages}",
                text_color="#38bdf8"
            )
            self.prev_page_btn.configure(state="normal" if page_num > 0 else "disabled")
            self.next_page_btn.configure(state="normal" if page_num < self.total_pages - 1 else "disabled")

            self.generate_paths()
        except Exception as e:
            messagebox.showerror("Import Error", f"Could not extract file:\n{e}")

    def prev_page(self):
        if self.current_page > 0:
            self._load_document_page(self.current_page - 1)

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self._load_document_page(self.current_page + 1)

    # ==========================================================
    # FONT & STYLE MANAGEMENT
    # ==========================================================
    def refresh_font_list(self, select_name=None):
        custom_files = sorted(glob.glob(os.path.join(FONTS_DIR, "*.json")))
        custom_labels = [f"[Custom] {os.path.splitext(os.path.basename(f))[0]}" for f in custom_files]

        hf = HersheyFonts()
        builtin_labels = [f"[Built-in] {name}" for name in sorted(hf.default_font_names)]

        self.all_styles = custom_labels + builtin_labels

        if select_name and select_name in self.all_styles:
            self.set_selected_style(select_name)
        elif self.current_style not in self.all_styles and self.all_styles:
            self.set_selected_style(self.all_styles[0])
        else:
            self.set_selected_style(self.current_style)

    def set_selected_style(self, style_label):
        self.current_style = style_label
        self.style_btn.configure(text=f"{style_label} ▾")
        render_style_sample(
            self.style_preview_canvas,
            style_label,
            sample_text="Sample Abc 123",
            height_mm=16.0,
            start_x=10,
            baseline_y=28
        )

    def open_style_picker(self):
        StylePickerPopup(self, self.all_styles, self.current_style, on_select=self._on_style_picked)

    def _on_style_picked(self, style_label):
        self.set_selected_style(style_label)
        self.generate_paths()

    def create_new_style(self):
        dialog = ctk.CTkInputDialog(
            text="Enter a name for the new handwriting style:",
            title="New Handwriting Style"
        )
        raw_name = dialog.get_input()
        if not raw_name:
            return

        clean_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in raw_name.strip())
        if not clean_name:
            return

        file_path = os.path.join(FONTS_DIR, f"{clean_name}.json")
        if not os.path.exists(file_path):
            with open(file_path, "w") as f:
                json.dump({}, f)

        self.refresh_font_list(select_name=f"[Custom] {clean_name}")

    def _get_selected_custom_path(self):
        if self.current_style.startswith("[Custom] "):
            style_name = self.current_style.replace("[Custom] ", "", 1)
            return os.path.join(FONTS_DIR, f"{style_name}.json")
        custom_files = sorted(glob.glob(os.path.join(FONTS_DIR, "*.json")))
        return custom_files[0] if custom_files else os.path.join(FONTS_DIR, "My_Handwriting.json")

    def open_ttf_importer(self):
        win = TTFFontConverter(self, fonts_dir=FONTS_DIR)
        self.wait_window(win)
        if hasattr(win, "created_style_name"):
            self.refresh_font_list(select_name=f"[Custom] {win.created_style_name}")
            self.generate_paths()

    def open_photo_importer(self):
        target_file = self._get_selected_custom_path()
        win = PhotoHandwritingExtractor(self, font_file=target_file)
        self.wait_window(win)
        style_name = os.path.splitext(os.path.basename(target_file))[0]
        self.refresh_font_list(select_name=f"[Custom] {style_name}")

    def open_manual_recorder(self):
        target_file = self._get_selected_custom_path()
        win = HandwritingRecorder(self, font_file=target_file)
        self.wait_window(win)
        style_name = os.path.splitext(os.path.basename(target_file))[0]
        self.refresh_font_list(select_name=f"[Custom] {style_name}")

    # ==========================================================
    # ARDUINO SERIAL CONNECTION
    # ==========================================================
    def refresh_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        if ports:
            self.port_menu.configure(values=ports)
            self.port_menu.set(ports[0])
        else:
            self.port_menu.configure(values=["No Ports Found"])
            self.port_menu.set("No Ports Found")

    def toggle_connection(self):
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            self.connect_btn.configure(text="Connect Arduino", fg_color="#16a34a")
            self.status_label.configure(text="Status: Disconnected", text_color="#94a3b8")
            return

        port = self.port_menu.get()
        if "No Ports" in port:
            self.status_label.configure(text="Status: Select a valid COM port!", text_color="#f59e0b")
            return

        try:
            self.serial_conn = serial.Serial(port, 115200, timeout=2)
            time.sleep(2)
            self.serial_conn.flushInput()
            self.connect_btn.configure(text="Disconnect", fg_color="#dc2626")
            self.status_label.configure(text=f"Status: Connected ({port})", text_color="#22c55e")
        except Exception as e:
            self.status_label.configure(text=f"Error: {str(e)[:30]}", text_color="#ef4444")

    # ==========================================================
    # STEADY STROKE & G-CODE GENERATION
    # ==========================================================
    def _build_text_run_mm(self, text_str, start_x, baseline_y, letter_h, slant_amt, is_custom, custom_glyphs, font):
        strokes_out = []
        cursor_x = start_x
        slant = 0.14 * slant_amt

        for char in text_str:
            if char == " ":
                cursor_x += letter_h * 0.48
                continue

            if is_custom and char in custom_glyphs:
                strokes = [[(x * letter_h, y * letter_h) for x, y in s] for s in custom_glyphs[char]]
            else:
                font.normalize_rendering(letter_h)
                strokes = list(font.strokes_for_text(char))

            max_x = 0
            for stroke in strokes:
                if not stroke:
                    continue
                mm_pts = []
                for x, y in stroke:
                    px = cursor_x + x + (y * slant)
                    py = baseline_y - y
                    mm_pts.append((px, py))
                    if x > max_x:
                        max_x = x
                if len(mm_pts) > 1:
                    strokes_out.append(mm_pts)

            cursor_x += (max_x if max_x > 0 else letter_h * 0.35) + (letter_h * 0.12)
        return strokes_out

    def generate_paths(self):
        letter_h = self.size_slider.get()
        slant_amt = self.chaos_slider.get() / 100.0
        pen_down_angle = int(self.servo_slider.get())
        selected = self.current_style

        custom_glyphs = {}
        is_custom = selected.startswith("[Custom] ")
        fallback_font_name = "scripts"

        if is_custom:
            json_path = self._get_selected_custom_path()
            if os.path.exists(json_path):
                with open(json_path, "r") as f:
                    custom_glyphs = json.load(f)
        else:
            fallback_font_name = selected.replace("[Built-in] ", "", 1)

        font = HersheyFonts()
        font.load_default_font(fallback_font_name)
        font.normalize_rendering(letter_h)

        self.mm_text_strokes = []
        self.mm_table_strokes = []
        self.mm_diagram_strokes = []

        # MODE A: 1:1 Exact Document Layout (PDF / Image / DOCX)
        if self.exact_layout_mode.get() and (self.doc_text_items or self.doc_table_lines_mm or self.doc_diagram_strokes_mm):
            if self.show_table_layer.get():
                self.mm_table_strokes = list(self.doc_table_lines_mm)

            if self.show_diagram_layer.get():
                self.mm_diagram_strokes = list(self.doc_diagram_strokes_mm)

            if self.show_text_layer.get():
                for item in self.doc_text_items:
                    scaled_h = item["height_mm"] * (letter_h / 6.0)
                    run_strokes = self._build_text_run_mm(
                        item["text"],
                        item["x_mm"],
                        item["y_mm"],
                        scaled_h,
                        slant_amt,
                        is_custom,
                        custom_glyphs,
                        font
                    )
                    self.mm_text_strokes.extend(run_strokes)

        # MODE B: Standard Ruled Notebook Auto-Wrap Mode
        else:
            if self.show_text_layer.get():
                text = self.textbox.get("1.0", "end-1c")
                margin_left = 22.0
                margin_right = 195.0
                cursor_x = margin_left
                cursor_y = 30.0
                line_spacing = 10.0
                slant = 0.14 * slant_amt

                for char in text:
                    if char == '\n':
                        cursor_x = margin_left
                        cursor_y += line_spacing
                        continue
                    if char == ' ':
                        cursor_x += letter_h * 0.52
                        continue

                    if is_custom and char in custom_glyphs:
                        strokes = [[(x * letter_h, y * letter_h) for x, y in s] for s in custom_glyphs[char]]
                    else:
                        strokes = list(font.strokes_for_text(char))

                    max_x = 0
                    for stroke in strokes:
                        if not stroke:
                            continue
                        mm_pts = []
                        for x, y in stroke:
                            px = cursor_x + x + (y * slant)
                            py = cursor_y - y
                            mm_pts.append((px, py))
                            if x > max_x:
                                max_x = x
                        if len(mm_pts) > 1:
                            self.mm_text_strokes.append(mm_pts)

                    cursor_x += (max_x if max_x > 0 else letter_h * 0.4) + (letter_h * 0.14)
                    if cursor_x > margin_right:
                        cursor_x = margin_left
                        cursor_y += line_spacing

        # Build Unified G-Code
        self.gcode_lines = ["G21", "G90", "M3 S0", "G0 X0.000 Y0.000 F3500"]
        total_dist_mm = 0.0

        def append_strokes_to_gcode(stroke_list, feed_rate):
            nonlocal total_dist_mm
            for stroke in stroke_list:
                if not stroke or len(stroke) < 2:
                    continue
                x0, y0 = stroke[0]
                self.gcode_lines.append("M3 S0")
                self.gcode_lines.append(f"G0 X{x0:.3f} Y{y0:.3f} F3500")
                self.gcode_lines.append(f"M3 S{pen_down_angle}")

                last_x, last_y = x0, y0
                for px, py in stroke[1:]:
                    seg_len = math.hypot(px - last_x, py - last_y)
                    if seg_len < 0.05:
                        continue
                    total_dist_mm += seg_len
                    self.gcode_lines.append(f"G1 X{px:.3f} Y{py:.3f} F{feed_rate}")
                    last_x, last_y = px, py

        append_strokes_to_gcode(self.mm_table_strokes, 1400)
        append_strokes_to_gcode(self.mm_diagram_strokes, 1600)
        append_strokes_to_gcode(self.mm_text_strokes, 1800)

        self.gcode_lines.append("M3 S0")
        self.gcode_lines.append("G0 X0.000 Y0.000 F3500")

        # Update Viewport & Telemetry
        self._redraw_viewport()
        est_seconds = int((total_dist_mm / 28.0) + (len(self.gcode_lines) * 0.015))
        mins, secs = divmod(est_seconds, 60)

        self.stats_lbl.configure(
            text=f"Tables: {len(self.mm_table_strokes)} | Diagrams: {len(self.mm_diagram_strokes)} | Est. Time: {mins}m {secs}s"
        )
        self.status_label.configure(
            text=f"Ready: {len(self.gcode_lines)} G-code commands",
            text_color="#38bdf8"
        )

    # ==========================================================
    # G-CODE STREAMING THREAD
    # ==========================================================
    def start_plotting_thread(self):
        if not self.gcode_lines:
            self.generate_paths()

        if not self.serial_conn or not self.serial_conn.is_open:
            self.status_label.configure(text="Connect Arduino first!", text_color="#f59e0b")
            return

        self.is_plotting = True
        self.stop_requested = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(target=self._stream_gcode, daemon=True).start()

    def _stream_gcode(self):
        total = len(self.gcode_lines)
        for i, line in enumerate(self.gcode_lines):
            if self.stop_requested:
                self.serial_conn.write(b"M3 S0\n")
                break

            self.serial_conn.write((line + "\n").encode())
            while True:
                resp = self.serial_conn.readline().decode().strip()
                if resp == "ok" or "error" in resp:
                    break

            prog = (i + 1) / total
            self.after(0, lambda p=prog, idx=i: self._update_progress(p, idx, total))

        self.after(0, self._finish_plotting)

    def _update_progress(self, prog, idx, total):
        self.progress.set(prog)
        self.status_label.configure(text=f"Writing: {int(prog * 100)}% ({idx}/{total})")

    def _finish_plotting(self):
        self.is_plotting = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        msg = "Stopped by user." if self.stop_requested else "Finished writing homework!"
        self.status_label.configure(text=msg, text_color="#22c55e")

    def stop_plotting(self):
        self.stop_requested = True


if __name__ == "__main__":
    app = HomeworkWriterApp()
    app.mainloop()