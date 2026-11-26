import json
import math
import os

import cv2
import numpy as np
from PIL import Image

from universal_doc_extractor import _fast_skeletonize


class HandwritingFontBuilder:
    """
    Converts a photo/scan of a user's handwriting sample sheet into a normalized
    single-stroke vector font JSON saved in fonts/<style_name>.json.
    """

    CHAR_ROWS = [
        list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        list("abcdefghijklmnopqrstuvwxyz"),
        list("0123456789"),
        list(".,:;-=+*/()!?'\"")
    ]

    @classmethod
    def build_font_from_image(cls, image_path, style_name, fonts_dir="fonts"):
        os.makedirs(fonts_dir, exist_ok=True)
        safe_name = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in style_name.strip())
        if not safe_name:
            safe_name = "My_Handwriting"

        pil_img = Image.open(image_path).convert("RGB")
        img_rgb = np.array(pil_img)
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)

        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        _, binary = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        h, w = binary.shape
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, int(w * 0.45)), 1))
        rules = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel)
        binary = cv2.subtract(binary, rules)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

        num_labels, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        raw_boxes = []
        for idx in range(1, num_labels):
            x, y, bw, bh, area = stats[idx]
            if area < 18 or bw > w * 0.25 or bh > h * 0.25:
                continue
            raw_boxes.append([x, y, x + bw, y + bh])

        if not raw_boxes:
            raise ValueError("No clear handwriting strokes detected. Use dark pen on white paper.")

        merged_boxes = cls._merge_vertical_parts(raw_boxes)
        rows = cls._cluster_into_rows(merged_boxes)

        glyphs = {}
        matched_count = 0

        for row_idx, target_chars in enumerate(cls.CHAR_ROWS):
            if row_idx >= len(rows):
                break
            row_boxes = sorted(rows[row_idx], key=lambda b: b[0])
            row_y_min = min(b[1] for b in row_boxes)
            row_y_max = max(b[3] for b in row_boxes)
            row_h = max(12.0, float(row_y_max - row_y_min))

            for col_idx, ch in enumerate(target_chars):
                if col_idx >= len(row_boxes):
                    break
                x0, y0, x1, y1 = row_boxes[col_idx]
                pad = 2
                crop = binary[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)]
                if crop.size == 0 or not np.any(crop):
                    continue

                strokes = cls._vectorize_char_crop(crop, x0 - pad, y0 - pad, row_y_max, row_h)
                if strokes:
                    glyphs[ch] = strokes
                    matched_count += 1

        if matched_count < 10:
            raise ValueError(
                f"Only detected {matched_count} characters. Write the 4 rows clearly with spaces between letters."
            )

        out_path = os.path.join(fonts_dir, f"{safe_name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(glyphs, f, indent=2)

        return safe_name, matched_count

    @staticmethod
    def _merge_vertical_parts(boxes):
        boxes = sorted(boxes, key=lambda b: b[0])
        merged = []
        for box in boxes:
            x0, y0, x1, y1 = box
            cx = (x0 + x1) * 0.5
            placed = False
            for m in merged:
                mcx = (m[0] + m[2]) * 0.5
                mw = max(10, m[2] - m[0])
                v_gap = max(0, max(y0 - m[3], m[1] - y1))
                if abs(cx - mcx) < mw * 0.55 and v_gap < max(25, (m[3] - m[1]) * 0.9):
                    m[0] = min(m[0], x0)
                    m[1] = min(m[1], y0)
                    m[2] = max(m[2], x1)
                    m[3] = max(m[3], y1)
                    placed = True
                    break
            if not placed:
                merged.append([x0, y0, x1, y1])
        return merged

    @staticmethod
    def _cluster_into_rows(boxes):
        boxes = sorted(boxes, key=lambda b: (b[1] + b[3]) * 0.5)
        rows = []
        for b in boxes:
            cy = (b[1] + b[3]) * 0.5
            bh = b[3] - b[1]
            if not rows:
                rows.append([b])
                continue
            last_row = rows[-1]
            last_cy = sum((item[1] + item[3]) * 0.5 for item in last_row) / len(last_row)
            last_h = sum((item[3] - item[1]) for item in last_row) / len(last_row)
            if abs(cy - last_cy) < max(bh, last_h) * 0.85:
                last_row.append(b)
            else:
                rows.append([b])
        return rows

    @staticmethod
    def _vectorize_char_crop(crop, ox, oy, baseline_y, row_h):
        closed = cv2.morphologyEx(crop, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        skel = _fast_skeletonize(closed > 0)

        h, w = skel.shape
        visited = np.zeros_like(skel, dtype=bool)
        neighbors = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]
        norm_strokes = []

        ys, xs = np.where(skel > 0)
        for sy, sx in zip(ys, xs):
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
                    nx = round(px / row_h, 4)
                    ny = round((baseline_y - (oy + py)) / row_h, 4)
                    if not scaled or math.hypot(nx - scaled[-1][0], ny - scaled[-1][1]) >= 0.03:
                        scaled.append([nx, ny])
                if len(scaled) >= 2:
                    norm_strokes.append(scaled)

        return norm_strokes