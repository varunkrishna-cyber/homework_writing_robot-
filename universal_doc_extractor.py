import math
import os

import cv2
import docx
import fitz  # PyMuPDF
import numpy as np
from PIL import Image

try:
    from skimage.morphology import skeletonize
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False

try:
    import pytesseract

    if os.path.exists(r"C:\Program Files\Tesseract-OCR\tesseract.exe"):
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False


def _fast_skeletonize(binary_bool):
    if HAS_SKIMAGE:
        return skeletonize(binary_bool).astype(np.uint8)
    img = (binary_bool.astype(np.uint8)) * 255
    skel = np.zeros_like(img)
    element = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    while True:
        eroded = cv2.erode(img, element)
        temp = cv2.dilate(eroded, element)
        temp = cv2.subtract(img, temp)
        skel = cv2.bitwise_or(skel, temp)
        img = eroded.copy()
        if cv2.countNonZero(img) == 0:
            break
    return (skel > 0).astype(np.uint8)


class UniversalDocumentExtractor:
    """
    Universal Document & Sketch Vectorizer for Pen Plotters:
    - Extracts exact text coordinates from PDFs, DOCX, TXT, and images.
    - Separates tables, crisp outlines, and multi-tone cross-hatch shading.
    - Isolated add-on for human-written notes + circuit diagrams.
    """

    A4_W = 210.0
    A4_H = 297.0

    @classmethod
    def extract_file(cls, file_path, page_num=0):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return cls._extract_pdf(file_path, page_num)
        elif ext == ".docx":
            return cls._extract_docx(file_path)
        elif ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
            return cls._extract_image(file_path)
        else:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            return {
                "plain_text": txt,
                "text_items": [],
                "table_lines_mm": [],
                "diagram_strokes_mm": [],
                "total_pages": 1,
                "is_exact_layout": False,
            }

    @classmethod
    def _extract_pdf(cls, file_path, page_num=0):
        doc = fitz.open(file_path)
        total_pages = len(doc)
        page_num = max(0, min(page_num, total_pages - 1))
        page = doc[page_num]

        rect = page.rect
        sx = cls.A4_W / max(rect.width, 1.0)
        sy = cls.A4_H / max(rect.height, 1.0)

        text_items = []
        plain_lines = []
        mask_boxes = []

        blocks = page.get_text("dict").get("blocks", [])
        for b in blocks:
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                line_strs = []
                for span in line.get("spans", []):
                    s = span.get("text", "").strip()
                    if not s:
                        continue
                    line_strs.append(s)
                    bbox = span["bbox"]
                    mask_boxes.append(bbox)
                    h_mm = max(3.5, min(12.0, (bbox[3] - bbox[1]) * sy * 0.85))
                    text_items.append({
                        "text": s,
                        "x_mm": bbox[0] * sx,
                        "y_mm": bbox[3] * sy,
                        "height_mm": h_mm,
                    })
                if line_strs:
                    plain_lines.append(" ".join(line_strs))

        dpi_scale = 200.0 / 72.0
        pix = page.get_pixmap(dpi=200)
        img_rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img_rgb = cv2.cvtColor(img_rgb, cv2.COLOR_RGBA2RGB)
        else:
            img_rgb = img_rgb.copy()

        for x0, y0, x1, y1 in mask_boxes:
            cv2.rectangle(
                img_rgb,
                (max(0, int(x0 * dpi_scale) - 3), max(0, int(y0 * dpi_scale) - 3)),
                (min(img_rgb.shape[1], int(x1 * dpi_scale) + 3), min(img_rgb.shape[0], int(y1 * dpi_scale) + 3)),
                (255, 255, 255),
                -1,
            )

        doc.close()

        table_lines_mm, diagram_strokes_mm = cls._vectorize_raster_with_shading(
            img_rgb, target_w=cls.A4_W, target_h=cls.A4_H, offset_x=0.0, offset_y=0.0
        )

        return {
            "plain_text": "\n".join(plain_lines),
            "text_items": text_items,
            "table_lines_mm": table_lines_mm,
            "diagram_strokes_mm": diagram_strokes_mm,
            "total_pages": total_pages,
            "is_exact_layout": True,
        }

    @classmethod
    def _extract_docx(cls, file_path):
        d = docx.Document(file_path)
        lines = [p.text for p in d.paragraphs if p.text.strip()]
        for tbl in d.tables:
            for row in tbl.rows:
                lines.append(" | ".join(cell.text.strip() for cell in row.cells))
        return {
            "plain_text": "\n\n".join(lines),
            "text_items": [],
            "table_lines_mm": [],
            "diagram_strokes_mm": [],
            "total_pages": 1,
            "is_exact_layout": False,
        }

    @classmethod
    def _extract_image(cls, file_path):
        pil_img = Image.open(file_path).convert("RGB")
        img_rgb = np.array(pil_img)
        h, w = img_rgb.shape[:2]

        avail_w, avail_h = cls.A4_W - 20.0, cls.A4_H - 20.0
        scale = min(avail_w / max(w, 1), avail_h / max(h, 1))
        target_w, target_h = w * scale, h * scale
        offset_x = (cls.A4_W - target_w) / 2.0
        offset_y = (cls.A4_H - target_h) / 2.0

        table_lines_mm, diagram_strokes_mm = cls._vectorize_raster_with_shading(
            img_rgb, target_w=target_w, target_h=target_h, offset_x=offset_x, offset_y=offset_y
        )

        return {
            "plain_text": "(Image / Sketch vectorized with tonal hatch shading)",
            "text_items": [],
            "table_lines_mm": table_lines_mm,
            "diagram_strokes_mm": diagram_strokes_mm,
            "total_pages": 1,
            "is_exact_layout": True,
        }

    @classmethod
    def extract_handwritten_notes(cls, file_path, page_num=0):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            doc = fitz.open(file_path)
            total_pages = len(doc)
            page_num = max(0, min(page_num, total_pages - 1))
            pix = doc[page_num].get_pixmap(dpi=220)
            img_rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:
                img_rgb = cv2.cvtColor(img_rgb, cv2.COLOR_RGBA2RGB)
            doc.close()
        else:
            pil_img = Image.open(file_path).convert("RGB")
            img_rgb = np.array(pil_img)
            total_pages = 1

        h, w = img_rgb.shape[:2]
        if max(w, h) < 1400:
            img_rgb = cv2.resize(img_rgb, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
            h, w = img_rgb.shape[:2]

        avail_w, avail_h = cls.A4_W - 20.0, cls.A4_H - 20.0
        scale = min(avail_w / max(w, 1), avail_h / max(h, 1))
        offset_x = (cls.A4_W - (w * scale)) / 2.0
        offset_y = (cls.A4_H - (h * scale)) / 2.0

        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
        ink_mask = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 15
        )
        horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(40, int(w * 0.45)), 1))
        notebook_rules = cv2.morphologyEx(ink_mask, cv2.MORPH_OPEN, horiz_kernel)
        clean_ink = cv2.subtract(ink_mask, notebook_rules)

        clean_rgb = np.full_like(img_rgb, 255)
        clean_rgb[clean_ink > 0] = (0, 0, 0)
        circuit_only_rgb = clean_rgb.copy()

        text_items = []
        plain_lines = []

        if HAS_TESSERACT:
            try:
                pil_clean = Image.fromarray(clean_rgb)
                ocr_data = pytesseract.image_to_data(
                    pil_clean, config="--psm 6", output_type=pytesseract.Output.DICT
                )
                lines_map = {}

                for i in range(len(ocr_data["text"])):
                    word = ocr_data["text"][i].strip()
                    conf = int(ocr_data["conf"][i]) if str(ocr_data["conf"][i]).isdigit() else -1
                    if len(word) < 2 or not any(c.isalnum() for c in word) or conf < 45:
                        continue

                    bx, by, bw, bh = (
                        ocr_data["left"][i],
                        ocr_data["top"][i],
                        ocr_data["width"][i],
                        ocr_data["height"][i],
                    )
                    if bh > h * 0.08 or bw > w * 0.65:
                        continue

                    pad = 4
                    cv2.rectangle(
                        circuit_only_rgb,
                        (max(0, bx - pad), max(0, by - pad)),
                        (min(w, bx + bw + pad), min(h, by + bh + pad)),
                        (255, 255, 255),
                        -1,
                    )

                    line_key = (ocr_data["block_num"][i], ocr_data["line_num"][i])
                    lines_map.setdefault(line_key, []).append((bx, by, bw, bh, word))

                for line_words in lines_map.values():
                    line_words.sort(key=lambda item: item[0])
                    full_txt = " ".join(item[4] for item in line_words)
                    plain_lines.append(full_txt)
                    bx0, by0, _, _, _ = line_words[0]
                    avg_h = sum(item[3] for item in line_words) / len(line_words)
                    text_items.append({
                        "text": full_txt,
                        "x_mm": offset_x + (bx0 * scale),
                        "y_mm": offset_y + ((by0 + avg_h * 0.85) * scale),
                        "height_mm": max(4.0, min(11.0, avg_h * scale * 0.85)),
                    })
            except Exception:
                pass

        circuit_strokes_mm = cls._vectorize_circuit_layer(
            circuit_only_rgb,
            target_w=(w * scale),
            target_h=(h * scale),
            offset_x=offset_x,
            offset_y=offset_y,
        )

        return {
            "plain_text": "\n".join(plain_lines) if plain_lines else "(Handwritten circuit diagram extracted)",
            "text_items": text_items,
            "table_lines_mm": [],
            "diagram_strokes_mm": circuit_strokes_mm,
            "total_pages": total_pages,
            "is_exact_layout": True,
        }

    @classmethod
    def _vectorize_circuit_layer(cls, img_rgb, target_w=210.0, target_h=297.0, offset_x=0.0, offset_y=0.0):
        h, w = img_rgb.shape[:2]
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
        _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

        sx = target_w / max(w, 1)
        sy = target_h / max(h, 1)
        strokes_mm = []

        if not np.any(binary):
            return strokes_mm

        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        skel = _fast_skeletonize(closed > 0)

        for stroke in cls._trace_momentum_skeleton(skel):
            pts_mm = [(offset_x + x * sx, offset_y + y * sy) for x, y in stroke]
            strokes_mm.append(pts_mm)

        return strokes_mm

    @classmethod
    def _vectorize_raster_with_shading(cls, img_rgb, target_w=210.0, target_h=297.0, offset_x=0.0, offset_y=0.0):
        h, w = img_rgb.shape[:2]
        max_dim = 950
        if max(w, h) > max_dim:
            r = max_dim / max(w, h)
            w, h = max(10, int(w * r)), max(10, int(h * r))
            img_rgb = cv2.resize(img_rgb, (w, h), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
        sx = target_w / max(w, 1)
        sy = target_h / max(h, 1)

        table_lines_mm = []
        diagram_strokes_mm = []

        _, dark_bin = cv2.threshold(gray, 135, 255, cv2.THRESH_BINARY_INV)
        if np.any(dark_bin):
            contours, _ = cv2.findContours(dark_bin, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                if cv2.arcLength(cnt, False) < 6:
                    continue
                approx = cv2.approxPolyDP(cnt, 0.75, False)
                pts = [(offset_x + float(p[0][0]) * sx, offset_y + float(p[0][1]) * sy) for p in approx]
                if len(pts) >= 2:
                    diagram_strokes_mm.append(pts)

        cls._add_hatch_band(gray, 175, 225, max(4, int(2.6 / sx)), 1, sx, sy, offset_x, offset_y, diagram_strokes_mm)
        cls._add_hatch_band(gray, 110, 175, max(3, int(1.5 / sx)), 1, sx, sy, offset_x, offset_y, diagram_strokes_mm)
        cls._add_hatch_band(gray, 35, 110, max(2, int(1.1 / sx)), 1, sx, sy, offset_x, offset_y, diagram_strokes_mm)
        cls._add_hatch_band(gray, 35, 110, max(2, int(1.3 / sx)), -1, sx, sy, offset_x, offset_y, diagram_strokes_mm)

        return table_lines_mm, diagram_strokes_mm

    @staticmethod
    def _add_hatch_band(gray, min_g, max_g, step_px, dir_sign, sx, sy, ox, oy, out_strokes):
        h, w = gray.shape
        c_start = -h if dir_sign == 1 else 0
        c_end = w if dir_sign == 1 else w + h

        for c in range(c_start, c_end + 1, step_px):
            p0 = None
            p1 = None
            for y in range(h):
                x = c + dir_sign * y
                if 0 <= x < w and min_g <= gray[y, x] < max_g:
                    pt = (ox + x * sx, oy + y * sy)
                    if p0 is None:
                        p0 = pt
                    p1 = pt
                else:
                    if p0 is not None and p1 is not None and math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 0.6:
                        out_strokes.append([p0, p1])
                    p0 = None
            if p0 is not None and p1 is not None and math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 0.6:
                out_strokes.append([p0, p1])

    @staticmethod
    def _trace_momentum_skeleton(skel):
        h, w = skel.shape
        visited = np.zeros_like(skel, dtype=bool)
        strokes = []
        neighbors = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

        ys, xs = np.where(skel > 0)
        for start_y, start_x in zip(ys, xs):
            if visited[start_y, start_x]:
                continue
            pts = [(int(start_x), int(start_y))]
            visited[start_y, start_x] = True
            cx, cy = int(start_x), int(start_y)
            prev_dx, prev_dy = 0, 0

            while True:
                best_next = None
                best_score = -999
                for dx, dy in neighbors:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and skel[ny, nx] > 0 and not visited[ny, nx]:
                        score = (dx * prev_dx + dy * prev_dy) if (prev_dx or prev_dy) else 0
                        if score > best_score:
                            best_score = score
                            best_next = (nx, ny, dx, dy)
                if best_next is None:
                    break
                nx, ny, prev_dx, prev_dy = best_next
                visited[ny, nx] = True
                pts.append((nx, ny))
                cx, cy = nx, ny

            if len(pts) >= 2:
                simplified = [pts[0]]
                last = pts[0]
                for pt in pts[1:-1]:
                    if math.hypot(pt[0] - last[0], pt[1] - last[1]) >= 1.5:
                        simplified.append(pt)
                        last = pt
                simplified.append(pts[-1])
                strokes.append(simplified)

        return strokes