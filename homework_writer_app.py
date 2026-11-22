import glob
import json
import os
import random
import threading
import time
import tkinter as tk
import customtkinter as ctk
import serial
import serial.tools.list_ports
from HersheyFonts import HersheyFonts
from handwriting_recorder import HandwritingRecorder
from photo_handwriting_extractor import PhotoHandwritingExtractor
from ttf_font_converter import TTFFontConverter

# Appearance setup
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

FONTS_DIR = "fonts"


def render_style_sample(canvas, style_label, sample_text="Abc 123", height_mm=16.0, start_x=10, baseline_y=26):
    """Draws a small single-line stroke preview of any custom or built-in style onto a tk.Canvas."""
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
                canvas.create_line(pts, fill="#1a237e", width=1.4, smooth=True)

        cursor_x += (max_x if max_x > 0 else height_mm * 0.45) + 2.5


class StylePickerPopup(ctk.CTkToplevel):
    """Scrollable visual gallery showing each handwriting style name beside a live stroke preview."""
    def __init__(self, parent, all_styles, current_style, on_select):
        super().__init__(parent)
        self.title("Select Handwriting Style")
        self.geometry("520x540")
        self.grab_set()
        self.on_select = on_select

        ctk.CTkLabel(
            self,
            text="Click a Handwriting Style to Select:",
            font=("Arial", 16, "bold")
        ).pack(pady=(12, 8))

        scroll = ctk.CTkScrollableFrame(self, width=480, height=460)
        scroll.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for style in all_styles:
            is_selected = (style == current_style)
            row = ctk.CTkFrame(
                scroll,
                fg_color="#2b3a4a" if is_selected else "#1e2229",
                border_width=2 if is_selected else 1,
                border_color="#4fc3f7" if is_selected else "#333333"
            )
            row.pack(fill="x", pady=4, padx=4)

            name_lbl = ctk.CTkLabel(
                row,
                text=style,
                font=("Arial", 13, "bold" if is_selected else "normal"),
                width=210,
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
        self.geometry("1120x780")

        os.makedirs(FONTS_DIR, exist_ok=True)
        if os.path.exists("my_handwriting.json") and not os.path.exists(os.path.join(FONTS_DIR, "My_Handwriting.json")):
            os.replace("my_handwriting.json", os.path.join(FONTS_DIR, "My_Handwriting.json"))

        if not glob.glob(os.path.join(FONTS_DIR, "*.json")):
            with open(os.path.join(FONTS_DIR, "My_Handwriting.json"), "w") as f:
                json.dump({}, f)

        self.serial_conn = None
        self.is_plotting = False
        self.stop_requested = False
        self.gcode_lines = []
        self.preview_strokes = []
        self.all_styles = []
        self.current_style = "[Custom] My_Handwriting"

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()
        self.refresh_ports()
        self.refresh_font_list()

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=305, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        ctk.CTkLabel(self.sidebar, text="Plotter Controls", font=("Arial", 20, "bold")).pack(pady=(10, 6))

        # --- 1. Hardware Connection ---
        ctk.CTkLabel(self.sidebar, text="1. Hardware Connection", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(6, 2))
        
        port_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        port_frame.pack(fill="x", padx=15, pady=3)
        
        self.port_menu = ctk.CTkOptionMenu(port_frame, values=["No Ports"], width=175)
        self.port_menu.pack(side="left", padx=(0, 5))
        
        ctk.CTkButton(port_frame, text="↻", width=40, command=self.refresh_ports).pack(side="left")

        self.connect_btn = ctk.CTkButton(self.sidebar, text="Connect Arduino", fg_color="green", command=self.toggle_connection)
        self.connect_btn.pack(fill="x", padx=15, pady=3)

        # --- 2. Handwriting Styles + Live Preview ---
        ctk.CTkLabel(self.sidebar, text="2. Handwriting Style", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(10, 2))

        style_row = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        style_row.pack(fill="x", padx=15, pady=3)

        self.style_btn = ctk.CTkButton(
            style_row,
            text="[Custom] My_Handwriting ▾",
            width=185,
            fg_color="#2b3a4a",
            hover_color="#37474f",
            anchor="w",
            command=self.open_style_picker
        )
        self.style_btn.pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            style_row,
            text="+ New",
            width=55,
            fg_color="#00897b",
            hover_color="#00695c",
            command=self.create_new_style
        ).pack(side="left")

        # Live Mini-Preview Strip right under style name
        self.style_preview_canvas = tk.Canvas(
            self.sidebar,
            width=265,
            height=38,
            bg="#fdfbf7",
            highlightthickness=1,
            highlightbackground="#555"
        )
        self.style_preview_canvas.pack(padx=15, pady=(2, 6))
        self.style_preview_canvas.bind("<Button-1>", lambda e: self.open_style_picker())
        self.style_preview_canvas.configure(cursor="hand2")

        ctk.CTkButton(
            self.sidebar,
            text="🗚 Import Word / TTF Font",
            fg_color="#00838f",
            hover_color="#006064",
            command=self.open_ttf_importer
        ).pack(fill="x", padx=15, pady=(2, 3))

        ctk.CTkButton(
            self.sidebar,
            text="📷 Import Photo to Selected Style",
            fg_color="#5e35b1",
            hover_color="#4527a0",
            command=self.open_photo_importer
        ).pack(fill="x", padx=15, pady=(0, 3))

        ctk.CTkButton(
            self.sidebar,
            text="✎ Draw / Edit Selected Style",
            fg_color="#3949ab",
            hover_color="#283593",
            command=self.open_manual_recorder
        ).pack(fill="x", padx=15, pady=(0, 6))

        # Font Size Slider
        self.size_label = ctk.CTkLabel(self.sidebar, text="Letter Height: 7.0 mm")
        self.size_label.pack(anchor="w", padx=15)
        self.size_slider = ctk.CTkSlider(
            self.sidebar, from_=4, to=14,
            command=lambda v: self.size_label.configure(text=f"Letter Height: {v:.1f} mm")
        )
        self.size_slider.set(7.0)
        self.size_slider.pack(fill="x", padx=15, pady=2)

        # Human Messiness Slider
        self.chaos_label = ctk.CTkLabel(self.sidebar, text="Human Imperfection: 30%")
        self.chaos_label.pack(anchor="w", padx=15)
        self.chaos_slider = ctk.CTkSlider(
            self.sidebar, from_=0, to=100,
            command=lambda v: self.chaos_label.configure(text=f"Human Imperfection: {int(v)}%")
        )
        self.chaos_slider.set(30)
        self.chaos_slider.pack(fill="x", padx=15, pady=2)

        # Pen Servo Angle
        self.servo_label = ctk.CTkLabel(self.sidebar, text="Pen Down Servo Angle: 45°")
        self.servo_label.pack(anchor="w", padx=15)
        self.servo_slider = ctk.CTkSlider(
            self.sidebar, from_=10, to=120,
            command=lambda v: self.servo_label.configure(text=f"Pen Down Servo Angle: {int(v)}°")
        )
        self.servo_slider.set(45)
        self.servo_slider.pack(fill="x", padx=15, pady=2)

        # --- 3. Execution ---
        ctk.CTkLabel(self.sidebar, text="3. Execution", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(10, 3))

        ctk.CTkButton(self.sidebar, text="Generate & Preview", command=self.generate_paths).pack(fill="x", padx=15, pady=3)
        
        self.start_btn = ctk.CTkButton(self.sidebar, text="▶ Start Writing", fg_color="#1f6aa5", command=self.start_plotting_thread)
        self.start_btn.pack(fill="x", padx=15, pady=3)

        self.stop_btn = ctk.CTkButton(self.sidebar, text="■ Emergency Stop", fg_color="#a83232", state="disabled", command=self.stop_plotting)
        self.stop_btn.pack(fill="x", padx=15, pady=3)

        # Progress Bar
        self.progress = ctk.CTkProgressBar(self.sidebar)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=15, pady=(10, 3))

        self.status_label = ctk.CTkLabel(self.sidebar, text="Status: Idle", text_color="gray", wraplength=265)
        self.status_label.pack(pady=3)

    def _build_main_area(self):
        self.main_frame = ctk.CTkFrame(self)
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 10), pady=10)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)
        self.main_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self.main_frame, text="Paste Homework Text Below:", font=("Arial", 14, "bold")).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        ctk.CTkLabel(self.main_frame, text="A4 Page Preview (210 x 297 mm):", font=("Arial", 14, "bold")).grid(row=0, column=1, sticky="w", padx=10, pady=5)

        self.textbox = ctk.CTkTextbox(self.main_frame, font=("Consolas", 14), wrap="word")
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.textbox.insert("1.0", "Experiment 1: Ohm's Law\n\nAim: To verify the relationship between voltage and current.\n\nFormula: V = I * R")

        self.canvas = tk.Canvas(self.main_frame, width=420, height=594, bg="#fdfbf7", highlightthickness=1, highlightbackground="#555")
        self.canvas.grid(row=1, column=1, padx=10, pady=(0, 10))
        self._draw_ruled_lines()

    def _draw_ruled_lines(self):
        self.canvas.delete("all")
        self.canvas.create_line(40, 0, 40, 594, fill="#f28b82", width=1.5)
        for y in range(60, 580, 20):
            self.canvas.create_line(0, y, 420, y, fill="#d2e3fc", width=1)

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
            text="Enter a name for the new handwriting style\n(e.g. Fast_Cursive, Neat_Notes):",
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

        label = f"[Custom] {clean_name}"
        self.refresh_font_list(select_name=label)
        self.status_label.configure(text=f"Created '{clean_name}'. Now import a photo, Word font, or draw letters!", text_color="#4caf50")

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
            self.connect_btn.configure(text="Connect Arduino", fg_color="green")
            self.status_label.configure(text="Status: Disconnected", text_color="gray")
            return

        port = self.port_menu.get()
        if "No Ports" in port:
            self.status_label.configure(text="Status: Select a valid COM port!", text_color="orange")
            return

        try:
            self.serial_conn = serial.Serial(port, 115200, timeout=2)
            time.sleep(2)
            self.serial_conn.flushInput()
            self.connect_btn.configure(text="Disconnect", fg_color="#a83232")
            self.status_label.configure(text=f"Status: Connected to {port}", text_color="#4caf50")
        except Exception as e:
            self.status_label.configure(text=f"Error: {str(e)[:30]}", text_color="red")

    def generate_paths(self):
        text = self.textbox.get("1.0", "end-1c")
        letter_h = self.size_slider.get()
        chaos = self.chaos_slider.get() / 100.0
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
            if not custom_glyphs:
                self.status_label.configure(
                    text="This custom style is empty! Import a Word font, photo, or draw letters (showing fallback).",
                    text_color="orange"
                )
        else:
            fallback_font_name = selected.replace("[Built-in] ", "", 1)

        font = HersheyFonts()
        font.load_default_font(fallback_font_name)
        font.normalize_rendering(letter_h)

        self.gcode_lines = [
            "G21",
            "G90",
            "M3 S0",
            "G0 X0 Y0 F3000"
        ]
        self.preview_strokes = []

        margin_left = 22.0
        margin_right = 195.0
        cursor_x = margin_left
        cursor_y = 30.0
        line_spacing = 10.0

        for char in text:
            if char == '\n':
                cursor_x = margin_left + random.uniform(-0.8, 0.8) * chaos
                cursor_y += line_spacing
                continue
            if char == ' ':
                cursor_x += (letter_h * 0.55) + random.uniform(-0.6, 0.8) * chaos
                continue

            y_jitter = random.uniform(-0.4, 0.4) * chaos
            scale_jitter = 1.0 + random.uniform(-0.06, 0.06) * chaos
            slant = random.uniform(0.05, 0.18) * chaos

            if is_custom and char in custom_glyphs:
                strokes = [[(x * letter_h, y * letter_h) for x, y in s] for s in custom_glyphs[char]]
            else:
                strokes = list(font.strokes_for_text(char))

            max_x = 0

            for stroke in strokes:
                if not stroke:
                    continue

                canvas_pts = []
                for idx, (x, y) in enumerate(stroke):
                    px = cursor_x + (x * scale_jitter) + (y * slant)
                    py = cursor_y - (y * scale_jitter) + y_jitter

                    canvas_pts.append((px * 2, py * 2))

                    if idx == 0:
                        self.gcode_lines.append("M3 S0")
                        self.gcode_lines.append(f"G0 X{px:.2f} Y{py:.2f} F3500")
                        self.gcode_lines.append(f"M3 S{pen_down_angle}")
                    else:
                        self.gcode_lines.append(f"G1 X{px:.2f} Y{py:.2f} F1800")

                    if x * scale_jitter > max_x:
                        max_x = x * scale_jitter

                if len(canvas_pts) > 1:
                    self.preview_strokes.append(canvas_pts)

            cursor_x += (max_x if max_x > 0 else letter_h * 0.4) + 1.0 + random.uniform(-0.3, 0.4) * chaos

            if cursor_x > margin_right:
                cursor_x = margin_left + random.uniform(-0.8, 0.8) * chaos
                cursor_y += line_spacing

        self.gcode_lines.append("M3 S0")
        self.gcode_lines.append("G0 X0 Y0 F3500")

        self._draw_ruled_lines()
        for pts in self.preview_strokes:
            self.canvas.create_line(pts, fill="#1a237e", width=1.4, smooth=True)

        if not is_custom or custom_glyphs:
            self.status_label.configure(
                text=f"Ready ({selected}): {len(self.gcode_lines)} G-code lines",
                text_color="#64b5f6"
            )

    def start_plotting_thread(self):
        if not self.gcode_lines:
            self.generate_paths()

        if not self.serial_conn or not self.serial_conn.is_open:
            self.status_label.configure(text="Connect Arduino first!", text_color="orange")
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
        self.status_label.configure(text=msg, text_color="#4caf50")

    def stop_plotting(self):
        self.stop_requested = True


if __name__ == "__main__":
    app = HomeworkWriterApp()
    app.mainloop()