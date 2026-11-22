import json
import os
import tkinter as tk
import customtkinter as ctk

CHARS_TO_RECORD = list(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789.,!?'-():;/+=*"
)

class HandwritingRecorder(ctk.CTkToplevel):
    def __init__(self, parent, font_file="my_handwriting.json"):
        super().__init__(parent)
        self.title("Record Your Personal Handwriting")
        self.geometry("520x560")
        self.font_file = font_file
        self.grab_set()  # Keep focus on this popup

        self.glyphs = {}
        if os.path.exists(self.font_file):
            with open(self.font_file, "r") as f:
                self.glyphs = json.load(f)

        self.current_idx = 0
        self.current_strokes = []
        self.active_stroke = []

        # Header
        self.char_label = ctk.CTkLabel(self, text="Draw character: 'a'", font=("Arial", 24, "bold"))
        self.char_label.pack(pady=(15, 5))

        self.progress_label = ctk.CTkLabel(self, text="1 / 86", text_color="gray")
        self.progress_label.pack(pady=(0, 10))

        # Drawing Canvas (300 x 300 px)
        self.canvas = tk.Canvas(self, width=300, height=300, bg="#fdfbf7", highlightthickness=2, highlightbackground="#444")
        self.canvas.pack(pady=5)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # Control Buttons
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=15)

        ctk.CTkButton(btn_frame, text="◀ Prev", width=80, command=self.prev_char).grid(row=0, column=0, padx=5)
        ctk.CTkButton(btn_frame, text="Clear", width=80, fg_color="#a83232", command=self.clear_canvas).grid(row=0, column=1, padx=5)
        ctk.CTkButton(btn_frame, text="Save & Next ▶", width=120, fg_color="green", command=self.save_and_next).grid(row=0, column=2, padx=5)

        ctk.CTkButton(self, text="Finish & Save Font File", command=self.finish_all).pack(pady=5)

        self.load_char()

    def _draw_guides(self):
        self.canvas.delete("all")
        # Top cap line (y=60), X-height dashed line (y=130), Baseline red (y=220), Descender line (y=270)
        self.canvas.create_line(0, 60, 300, 60, fill="#cfd8dc", dash=(4, 4))
        self.canvas.create_line(0, 130, 300, 130, fill="#cfd8dc", dash=(4, 4))
        self.canvas.create_line(0, 220, 300, 220, fill="#ef9a9a", width=2)   # Baseline
        self.canvas.create_line(0, 270, 300, 270, fill="#cfd8dc", dash=(4, 4))
        self.canvas.create_line(70, 0, 70, 300, fill="#e0e0e0", dash=(2, 4))     # Left start guide

    def load_char(self):
        char = CHARS_TO_RECORD[self.current_idx]
        status = " (Saved)" if char in self.glyphs else ""
        self.char_label.configure(text=f"Draw character:  '{char}'{status}")
        self.progress_label.configure(text=f"Character {self.current_idx + 1} of {len(CHARS_TO_RECORD)}")
        
        self._draw_guides()
        self.current_strokes = []

        # Redraw existing saved strokes if editing
        if char in self.glyphs:
            for stroke in self.glyphs[char]:
                # Denormalize back to canvas coordinates
                pts = [(x * 160 + 70, 220 - y * 160) for x, y in stroke]
                self.current_strokes.append(pts)
                if len(pts) > 1:
                    self.canvas.create_line(pts, fill="#1a237e", width=3, capstyle=tk.ROUND, smooth=True)

    def _on_press(self, event):
        self.active_stroke = [(event.x, event.y)]

    def _on_drag(self, event):
        self.active_stroke.append((event.x, event.y))
        if len(self.active_stroke) > 1:
            self.canvas.create_line(self.active_stroke[-2:], fill="#1a237e", width=3, capstyle=tk.ROUND)

    def _on_release(self, event):
        if len(self.active_stroke) > 1:
            self.current_strokes.append(self.active_stroke)
        self.active_stroke = []

    def clear_canvas(self):
        self.current_strokes = []
        self._draw_guides()

    def _save_current_glyph(self):
        if not self.current_strokes:
            return
        char = CHARS_TO_RECORD[self.current_idx]
        # Normalize coordinates relative to baseline (y=220) and cap height (160px)
        min_x = min(pt[0] for stroke in self.current_strokes for pt in stroke)
        norm_strokes = []
        for stroke in self.current_strokes:
            norm_pts = [((x - min_x) / 160.0, (220.0 - y) / 160.0) for x, y in stroke]
            norm_strokes.append(norm_pts)
        self.glyphs[char] = norm_strokes

    def save_and_next(self):
        self._save_current_glyph()
        if self.current_idx < len(CHARS_TO_RECORD) - 1:
            self.current_idx += 1
            self.load_char()
        else:
            self.finish_all()

    def prev_char(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self.load_char()

    def finish_all(self):
        self._save_current_glyph()
        with open(self.font_file, "w") as f:
            json.dump(self.glyphs, f)
        self.destroy()