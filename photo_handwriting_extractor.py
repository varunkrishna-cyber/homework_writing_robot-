import json
import os
from tkinter import filedialog, messagebox
import customtkinter as ctk
import cv2
import numpy as np
from PIL import Image
from skimage.morphology import skeletonize


class PhotoHandwritingExtractor(ctk.CTkToplevel):
    def __init__(self, parent, font_file="my_handwriting.json"):
        super().__init__(parent)
        self.title("Import Handwriting from Photo")
        self.geometry("760x620")
        self.font_file = font_file
        self.grab_set()

        self.glyphs = {}
        if os.path.exists(self.font_file):
            with open(self.font_file, "r") as f:
                self.glyphs = json.load(f)

        self.extracted_items = []
        self.current_idx = 0

        self._build_ui()

    def _build_ui(self):
        top_frame = ctk.CTkFrame(self)
        top_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkButton(
            top_frame,
            text="📁 Upload Photo of Handwriting",
            fg_color="#1f6aa5",
            command=self.upload_and_process
        ).pack(side="left", padx=10, pady=10)

        self.info_label = ctk.CTkLabel(
            top_frame,
            text="Tip: Write letters clearly with black/blue pen on plain white paper.",
            text_color="gray"
        )
        self.info_label.pack(side="left", padx=10)

        mid_frame = ctk.CTkFrame(self)
        mid_frame.pack(fill="both", expand=True, padx=15, pady=5)

        self.preview_label = ctk.CTkLabel(mid_frame, text="Upload an image to detect characters", width=260, height=260)
        self.preview_label.pack(pady=15)

        self.count_label = ctk.CTkLabel(mid_frame, text="Detected Character: 0 / 0", font=("Arial", 14))
        self.count_label.pack(pady=5)

        input_row = ctk.CTkFrame(mid_frame, fg_color="transparent")
        input_row.pack(pady=10)

        ctk.CTkLabel(input_row, text="Which character is this? ", font=("Arial", 15, "bold")).pack(side="left", padx=5)
        self.char_entry = ctk.CTkEntry(input_row, width=70, font=("Arial", 20, "bold"), justify="center")
        self.char_entry.pack(side="left", padx=5)
        self.char_entry.bind("<Return>", lambda e: self.save_and_next())

        nav_frame = ctk.CTkFrame(mid_frame, fg_color="transparent")
        nav_frame.pack(pady=10)

        ctk.CTkButton(nav_frame, text="◀ Prev", width=90, command=self.prev_item).grid(row=0, column=0, padx=5)
        ctk.CTkButton(nav_frame, text="Skip (Noise)", width=100, fg_color="#757575", command=self.skip_item).grid(row=0, column=1, padx=5)
        ctk.CTkButton(nav_frame, text="Save & Next ▶", width=130, fg_color="green", command=self.save_and_next).grid(row=0, column=2, padx=5)

        ctk.CTkButton(
            self,
            text="✔ Save All Extracted Letters to My Handwriting",
            fg_color="#5e35b1",
            height=40,
            command=self.finish_and_save
        ).pack(fill="x", padx=15, pady=15)

    def upload_and_process(self):
        file_path = filedialog.askopenfilename(
            title="Select Handwriting Image",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp")]
        )
        if not file_path:
            return

        self.info_label.configure(text="Processing ink skeleton... please wait.", text_color="orange")
        self.update()

        img = cv2.imread(file_path)
        h, w = img.shape[:2]
        if w > 1400:
            scale = 1400 / w
            img = cv2.resize(img, (int(w * scale), int(h * scale)))

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur = cv2.GaussianBlur(gray, (5, 5), 0)

        binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12
        )

        kernel = np.ones((2, 2), np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        merged = cv2.dilate(binary, np.ones((12, 8), np.uint8), iterations=1)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(merged)

        boxes = []
        for i in range(1, num_labels):
            x, y, bw, bh, area = stats[i]
            if bw > 8 and bh > 12 and area > 60:
                boxes.append((x, y, bw, bh))

        boxes.sort(key=lambda b: (b[1] // 60, b[0]))

        self.extracted_items = []
        for (x, y, bw, bh) in boxes:
            crop_bin = binary[y:y + bh, x:x + bw]
            crop_orig = img[y:y + bh, x:x + bw].copy()

            skel = skeletonize(crop_bin > 0).astype(np.uint8)
            strokes = self._trace_skeleton(skel)

            if not strokes:
                continue

            for stroke in strokes:
                for idx in range(len(stroke) - 1):
                    cv2.line(crop_orig, stroke[idx], stroke[idx + 1], (0, 220, 0), 2)

            norm_strokes = []
            max_dim = max(bh, 1)
            for stroke in strokes:
                norm_pts = [(pt[0] / max_dim, (bh - pt[1]) / max_dim) for pt in stroke]
                norm_strokes.append(norm_pts)

            rgb_crop = cv2.cvtColor(crop_orig, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(rgb_crop)
            pil_img.thumbnail((220, 220))

            self.extracted_items.append({
                "img": pil_img,
                "strokes": norm_strokes,
                "char": ""
            })

        if not self.extracted_items:
            messagebox.showwarning("No Text Found", "Could not detect clear letters. Try higher contrast lighting.")
            return

        self.current_idx = 0
        self.info_label.configure(text=f"Detected {len(self.extracted_items)} characters!", text_color="#4caf50")
        self.show_current_item()

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

        start_candidates = endpoints + list(zip(xs, ys))

        for sx, sy in start_candidates:
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
                strokes.append(stroke[::2] + [stroke[-1]])

        return strokes

    def show_current_item(self):
        if not self.extracted_items:
            return
        item = self.extracted_items[self.current_idx]
        ctk_img = ctk.CTkImage(light_image=item["img"], dark_image=item["img"], size=item["img"].size)
        self.preview_label.configure(image=ctk_img, text="")
        self.count_label.configure(text=f"Character {self.current_idx + 1} of {len(self.extracted_items)}")

        self.char_entry.delete(0, "end")
        if item["char"]:
            self.char_entry.insert(0, item["char"])
        self.char_entry.focus()

    def save_and_next(self):
        if not self.extracted_items:
            return
        typed = self.char_entry.get().strip()
        if typed:
            char = typed[0]
            self.extracted_items[self.current_idx]["char"] = char
            self.glyphs[char] = self.extracted_items[self.current_idx]["strokes"]

        if self.current_idx < len(self.extracted_items) - 1:
            self.current_idx += 1
            self.show_current_item()
        else:
            self.info_label.configure(text="Reached last character! Click Save below.", text_color="#64b5f6")

    def skip_item(self):
        if self.current_idx < len(self.extracted_items) - 1:
            self.current_idx += 1
            self.show_current_item()

    def prev_item(self):
        if self.current_idx > 0:
            self.current_idx -= 1
            self.show_current_item()

    def finish_and_save(self):
        typed = self.char_entry.get().strip()
        if typed and self.extracted_items:
            self.glyphs[typed[0]] = self.extracted_items[self.current_idx]["strokes"]

        with open(self.font_file, "w") as f:
            json.dump(self.glyphs, f)
        messagebox.showinfo("Saved!", f"Saved {len(self.glyphs)} characters to {self.font_file}!")
        self.destroy()