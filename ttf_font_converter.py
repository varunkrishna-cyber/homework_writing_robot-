import glob
import json
import os
from tkinter import filedialog, messagebox
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from skimage.morphology import skeletonize

CHARS_TO_CONVERT = (
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789.,!?'-():;/+=*&@#%\"[]"
)

COMMON_WORD_HANDWRITING = [
    ("Segoe Script", "segoesc.ttf"),
    ("Segoe Print", "segoepr.ttf"),
    ("Lucida Handwriting", "LHANDW.TTF"),
    ("Bradley Hand ITC", "BRADHITC.TTF"),
    ("Kristen ITC", "ITCKRIST.TTF"),
    ("Freestyle Script", "FREESCPT.TTF"),
    ("French Script MT", "FRSCRIPT.TTF"),
    ("Brush Script MT", "BRUSHSCI.TTF"),
    ("Mistral", "MISTRAL.TTF"),
    ("Comic Sans MS", "comic.ttf"),
    ("Gabriola", "Gabriola.ttf"),
    ("Ink Free", "Inkfree.ttf"),
]


class TTFFontConverter(ctk.CTkToplevel):
    def __init__(self, parent, fonts_dir="fonts"):
        super().__init__(parent)
        self.title("Import Microsoft Word / TTF Handwriting Font")
        self.geometry("540x480")
        self.fonts_dir = fonts_dir
        self.grab_set()

        self.available_fonts = self._find_system_fonts()
        self.custom_ttf_path = None

        self._build_ui()

    def _find_system_fonts(self):
        win_fonts = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
        found = {}

        for label, filename in COMMON_WORD_HANDWRITING:
            full_path = os.path.join(win_fonts, filename)
            if os.path.exists(full_path):
                found[f"★ {label}"] = full_path

        if os.path.exists(win_fonts):
            for path in sorted(glob.glob(os.path.join(win_fonts, "*.ttf"))):
                name = os.path.splitext(os.path.basename(path))[0]
                if path not in found.values():
                    found[name] = path

        return found

    def _build_ui(self):
        ctk.CTkLabel(
            self,
            text="Convert Word / System Font to Single-Line Plotter Style",
            font=("Arial", 16, "bold")
        ).pack(pady=(15, 5))

        ctk.CTkLabel(
            self,
            text="★ Starred fonts are classic Microsoft Word handwriting fonts.",
            text_color="gray"
        ).pack(pady=(0, 12))

        frame = ctk.CTkFrame(self)
        frame.pack(fill="both", expand=True, padx=20, pady=5)

        ctk.CTkLabel(frame, text="1. Choose an Installed Font:", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(12, 4))

        font_names = list(self.available_fonts.keys()) if self.available_fonts else ["No System Fonts Found"]
        self.font_var = ctk.StringVar(value=font_names[0])
        self.font_dropdown = ctk.CTkOptionMenu(frame, variable=self.font_var, values=font_names, width=340)
        self.font_dropdown.pack(padx=15, pady=4)

        ctk.CTkButton(
            frame,
            text="Or Browse Any .TTF / .OTF File...",
            fg_color="#455a64",
            hover_color="#37474f",
            command=self.browse_ttf
        ).pack(padx=15, pady=(6, 12))

        self.selected_file_lbl = ctk.CTkLabel(frame, text="", text_color="#4fc3f7")
        self.selected_file_lbl.pack()

        ctk.CTkLabel(frame, text="2. Style Name in App:", font=("Arial", 13, "bold")).pack(anchor="w", padx=15, pady=(8, 4))
        self.name_entry = ctk.CTkEntry(frame, width=340, placeholder_text="Leave blank to use font's own name")
        self.name_entry.pack(padx=15, pady=4)

        self.status_lbl = ctk.CTkLabel(self, text="Ready to convert.", text_color="gray")
        self.status_lbl.pack(pady=6)

        ctk.CTkButton(
            self,
            text="⚡ Convert & Add to Styles",
            fg_color="#00897b",
            hover_color="#00695c",
            height=42,
            font=("Arial", 14, "bold"),
            command=self.convert_font
        ).pack(fill="x", padx=20, pady=(4, 16))

    def browse_ttf(self):
        path = filedialog.askopenfilename(
            title="Select TrueType / OpenType Font",
            filetypes=[("Font Files", "*.ttf *.otf *.TTF *.OTF")]
        )
        if path:
            self.custom_ttf_path = path
            self.selected_file_lbl.configure(text=f"Selected file: {os.path.basename(path)}")

    def convert_font(self):
        if self.custom_ttf_path:
            ttf_path = self.custom_ttf_path
            default_name = os.path.splitext(os.path.basename(ttf_path))[0]
        else:
            choice = self.font_var.get()
            ttf_path = self.available_fonts.get(choice)
            default_name = choice.replace("★ ", "").strip()

        if not ttf_path or not os.path.exists(ttf_path):
            messagebox.showerror("Error", "Please select a valid .ttf font file.")
            return

        raw_name = self.name_entry.get().strip() or default_name
        clean_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in raw_name)

        self.status_lbl.configure(text="Extracting single-line centerline strokes...", text_color="orange")
        self.update()

        try:
            pil_font = ImageFont.truetype(ttf_path, 120)
        except Exception as e:
            messagebox.showerror("Font Error", f"Could not load font:\n{e}")
            return

        ascent, descent = pil_font.getmetrics()
        cap_height = max(ascent, 1)

        glyphs = {}
        for char in CHARS_TO_CONVERT:
            canvas_w, canvas_h = 220, 220
            img = Image.new("L", (canvas_w, canvas_h), 0)
            draw = ImageDraw.Draw(img)

            baseline_y = 150
            draw.text((30, baseline_y - ascent), char, fill=255, font=pil_font)

            arr = np.array(img) > 128
            if not np.any(arr):
                continue

            skel = skeletonize(arr).astype(np.uint8)
            strokes = self._trace_skeleton(skel)
            if not strokes:
                continue

            min_x = min(pt[0] for s in strokes for pt in s)
            norm_strokes = []
            for s in strokes:
                norm_pts = [((x - min_x) / cap_height, (baseline_y - y) / cap_height) for x, y in s]
                norm_strokes.append(norm_pts)

            glyphs[char] = norm_strokes

        out_path = os.path.join(self.fonts_dir, f"{clean_name}.json")
        with open(out_path, "w") as f:
            json.dump(glyphs, f)

        self.created_style_name = clean_name
        messagebox.showinfo("Done!", f"Converted '{clean_name}' ({len(glyphs)} characters) into single-line plotter strokes!")
        self.destroy()

    def _trace_skeleton(self, skel):
        visited = np.zeros_like(skel, dtype=bool)
        strokes = []
        h, w = skel.shape
        ys, xs = np.where(skel > 0)
        if len(xs) == 0:
            return []

        def neighbors(cx, cy):
            nbs = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and skel[ny, nx] > 0 and not visited[ny, nx]:
                        nbs.append((nx, ny))
            return nbs

        endpoints = []
        for x, y in zip(xs, ys):
            count = sum(
                1 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dx != 0 or dy != 0) and 0 <= x + dx < w and 0 <= y + dy < h and skel[y + dy, x + dx] > 0
            )
            if count == 1:
                endpoints.append((x, y))

        for sx, sy in endpoints + list(zip(xs, ys)):
            if visited[sy, sx]:
                continue
            stroke = [(int(sx), int(sy))]
            visited[sy, sx] = True
            cx, cy = sx, sy

            while True:
                nbs = neighbors(cx, cy)
                if not nbs:
                    break
                nx, ny = nbs[0]
                visited[ny, nx] = True
                stroke.append((int(nx), int(ny)))
                cx, cy = nx, ny

            if len(stroke) >= 3:
                strokes.append(stroke[::3] + [stroke[-1]])

        return strokes