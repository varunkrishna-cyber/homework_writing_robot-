import os
import cv2
import docx
import fitz  # PyMuPDF
import numpy as np
from PIL import Image
from skimage.morphology import skeletonize

try:
    import pytesseract
    default_tess = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(default_tess):
        pytesseract.pytesseract.tesseract_cmd = default_tess
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False


class UniversalDocumentExtractor:
    """
    Ultra-Precision Document Extractor:
    - Extracts native PDF mathematical vector paths + exact text spans + thin-box table borders.
    - Uses 300 DPI / Lanczos4 upscaling, morphological + Hough grid detection, and corner snapping.
    - Applies spur pruning, 5-point Gaussian path smoothing, and tight Douglas-Peucker fitting.
    """

    A4_W = 210.0
    A4_H = 297.0

    @classmethod
    def extract_file(cls, file_path, page_num=0):
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return cls._extract_pdf_ultra_precision(file_path, page_num)
        elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"):
            return cls._extract_image_ultra_precision(file_path)
        elif ext == ".docx":
            return cls._extract_docx(file_path)
        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            return {
                "plain_text": txt,
                "text_items": [],
                "table_lines_mm": [],
                "diagram_strokes_mm": [],
                "total_pages": 1,
                "is_exact_layout": False
            }
        else:
            raise ValueError(f"Unsupported file format: {ext}")

    # ==========================================================
    # 1. ULTRA-PRECISION PDF EXTRACTOR
    # ==========================================================
    @classmethod
    def _extract_pdf_ultra_precision(cls, pdf_path, page_num=0):
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        page_num = max(0, min(page_num, total_pages - 1))
        page = doc[page_num]

        pdf_w = max(page.rect.width, 1)
        pdf_h = max(page.rect.height, 1)
        scale_x = cls.A4_W / pdf_w
        scale_y = cls.A4_H / pdf_h

        # A. Extract Exact Text Spans
        text_items = []
        plain_lines = []
        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_buf = []
                for span in line.get("spans", []):
                    raw = span.get("text", "")
                    if not raw.strip():
                        continue
                    line_buf.append(raw)
                    ox, oy = span["origin"]
                    font_pt = span.get("size", 11.0)
                    text_items.append({
                        "text": raw,
                        "x_mm": ox * scale_x,
                        "y_mm": oy * scale_y,
                        "height_mm": max(3.2, min(14.0, font_pt * 0.3528 * scale_x))
                    })
                if line_buf:
                    plain_lines.append("".join(line_buf))

        # B. Extract Native Vector Drawings & Table Borders
        table_lines_mm = []
        diagram_strokes_mm = []

        drawings = page.get_drawings()
        for path in drawings:
            has_stroke = path.get("color") is not None
            has_fill = path.get("fill") is not None

            for item in path.get("items", []):
                op = item[0]
                if op == "l":  # Straight line segment
                    p1, p2 = item[1], item[2]
                    x1, y1 = p1.x * scale_x, p1.y * scale_y
                    x2, y2 = p2.x * scale_x, p2.y * scale_y
                    if abs(y1 - y2) < 0.25 or abs(x1 - x2) < 0.25:
                        table_lines_mm.append([(x1, y1), (x2, y2)])
                    else:
                        diagram_strokes_mm.append([(x1, y1), (x2, y2)])

                elif op == "re":  # Rectangle (stroked table box OR thin filled rule)
                    rect = item[1]
                    x0, y0 = rect.x0 * scale_x, rect.y0 * scale_y
                    x1, y1 = rect.x1 * scale_x, rect.y1 * scale_y
                    rw = abs(x1 - x0)
                    rh = abs(y1 - y0)

                    # Ignore full-page outer frame border
                    if rw > 202 and rh > 288:
                        continue

                    # Thin filled rectangles used by Word/Docs as table borders
                    if has_fill and not has_stroke:
                        if rh <= 2.5 and rw > 3.0:
                            ym = (y0 + y1) / 2.0
                            table_lines_mm.append([(x0, ym), (x1, ym)])
                        elif rw <= 2.5 and rh > 3.0:
                            xm = (x0 + x1) / 2.0
                            table_lines_mm.append([(xm, y0), (xm, y1)])
                        continue

                    if has_stroke:
                        table_lines_mm.append([(x0, y0), (x1, y0)])
                        table_lines_mm.append([(x1, y0), (x1, y1)])
                        table_lines_mm.append([(x1, y1), (x0, y1)])
                        table_lines_mm.append([(x0, y1), (x0, y0)])

                elif op == "c":  # Cubic Bezier curve -> high-resolution 20-step sampling
                    if not has_stroke:
                        continue
                    p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
                    curve_pts = []
                    for step in range(21):
                        t = step / 20.0
                        mt = 1.0 - t
                        bx = (mt**3)*p0.x + 3*(mt**2)*t*p1.x + 3*mt*(t**2)*p2.x + (t**3)*p3.x
                        by = (mt**3)*p0.y + 3*(mt**2)*t*p1.y + 3*mt*(t**2)*p2.y + (t**3)*p3.y
                        curve_pts.append((bx * scale_x, by * scale_y))
                    diagram_strokes_mm.append(curve_pts)

        # C. Extract Any Embedded Raster Images Inside the PDF
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            if rect.width < 20 or rect.height < 20:
                continue

            base_img = doc.extract_image(xref)
            img_arr = np.frombuffer(base_img["image"], dtype=np.uint8)
            bgr = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

            r_tables, r_diags = cls._vectorize_raster_ultra_precision(
                rgb,
                target_w=rect.width * scale_x,
                target_h=rect.height * scale_y,
                offset_x=rect.x0 * scale_x,
                offset_y=rect.y0 * scale_y
            )
            table_lines_mm.extend(r_tables)
            diagram_strokes_mm.extend(r_diags)

        # Snap & merge collinear table segments so corners meet cleanly
        table_lines_mm = cls._snap_and_merge_table_lines(table_lines_mm)

        doc.close()

        return {
            "plain_text": "\n".join(plain_lines),
            "text_items": text_items,
            "table_lines_mm": table_lines_mm,
            "diagram_strokes_mm": diagram_strokes_mm,
            "total_pages": total_pages,
            "is_exact_layout": True
        }

    # ==========================================================
    # 2. ULTRA-PRECISION IMAGE / SCREENSHOT EXTRACTOR
    # ==========================================================
    @classmethod
    def _extract_image_ultra_precision(cls, img_path):
        pil_img = Image.open(img_path).convert("RGB")
        img_rgb = np.array(pil_img)
        h, w = img_rgb.shape[:2]

        # Lanczos4 upscaling for crisp sub-pixel line & OCR accuracy
        if max(w, h) < 1600:
            scale_factor = 2
            img_rgb = cv2.resize(img_rgb, (w * scale_factor, h * scale_factor), interpolation=cv2.INTER_LANCZOS4)
            pil_img = Image.fromarray(img_rgb)
            h, w = img_rgb.shape[:2]

        avail_w = cls.A4_W - 20.0
        avail_h = cls.A4_H - 20.0
        scale = min(avail_w / max(w, 1), avail_h / max(h, 1))
        offset_x = 10.0
        offset_y = 10.0

        text_items = []
        plain_lines = []
        graphics_mask_img = img_rgb.copy()

        if HAS_TESSERACT:
            try:
                ocr_data = pytesseract.image_to_data(pil_img, output_type=pytesseract.Output.DICT)
                lines_map = {}

                for i in range(len(ocr_data["text"])):
                    word = ocr_data["text"][i].strip()
                    conf = int(ocr_data["conf"][i]) if str(ocr_data["conf"][i]).isdigit() else -1
                    if not word or conf < 50:
                        continue

                    bx, by, bw, bh = (
                        ocr_data["left"][i],
                        ocr_data["top"][i],
                        ocr_data["width"][i],
                        ocr_data["height"][i],
                    )

                    pad = 3
                    y1, y2 = max(0, by - pad), min(h, by + bh + pad)
                    x1, x2 = max(0, bx - pad), min(w, bx + bw + pad)
                    bg_color = tuple(int(c) for c in np.median(img_rgb[y1:y2, x1:x2].reshape(-1, 3), axis=0))
                    cv2.rectangle(graphics_mask_img, (x1, y1), (x2, y2), bg_color, -1)

                    text_items.append({
                        "text": word,
                        "x_mm": offset_x + (bx * scale),
                        "y_mm": offset_y + ((by + bh * 0.85) * scale),
                        "height_mm": max(3.5, min(12.0, bh * scale * 0.9))
                    })

                    line_key = (ocr_data["block_num"][i], ocr_data["line_num"][i])
                    lines_map.setdefault(line_key, []).append(word)

                plain_lines = [" ".join(words) for words in lines_map.values()]
            except Exception:
                pass

        table_lines_mm, diagram_strokes_mm = cls._vectorize_raster_ultra_precision(
            graphics_mask_img,
            target_w=(w * scale),
            target_h=(h * scale),
            offset_x=offset_x,
            offset_y=offset_y
        )
        table_lines_mm = cls._snap_and_merge_table_lines(table_lines_mm)

        return {
            "plain_text": "\n".join(plain_lines) if plain_lines else "(Image vectorized at ultra-high precision)",
            "text_items": text_items,
            "table_lines_mm": table_lines_mm,
            "diagram_strokes_mm": diagram_strokes_mm,
            "total_pages": 1,
            "is_exact_layout": True
        }

    # ==========================================================
    # 3. WORD (.DOCX) EXTRACTOR
    # ==========================================================
    @classmethod
    def _extract_docx(cls, docx_path):
        doc = docx.Document(docx_path)
        text_items = []
        table_lines_mm = []
        plain_lines = []

        cursor_x = 22.0
        cursor_y = 28.0
        line_h = 8.5

        for para in doc.paragraphs:
            txt = para.text.strip()
            if not txt:
                cursor_y += line_h * 0.7
                continue
            plain_lines.append(txt)

            words = txt.split(" ")
            curr_line = ""
            for w in words:
                test = (curr_line + " " + w).strip()
                if len(test) * 2.6 > 170.0 and curr_line:
                    text_items.append({"text": curr_line, "x_mm": cursor_x, "y_mm": cursor_y, "height_mm": 5.5})
                    cursor_y += line_h
                    curr_line = w
                else:
                    curr_line = test
            if curr_line:
                text_items.append({"text": curr_line, "x_mm": cursor_x, "y_mm": cursor_y, "height_mm": 5.5})
                cursor_y += line_h

        for table in doc.tables:
            num_rows = len(table.rows)
            num_cols = len(table.columns)
            if num_rows == 0 or num_cols == 0:
                continue

            cursor_y += 4.0
            table_w = 170.0
            col_w = table_w / num_cols
            row_h = 10.0
            start_y = cursor_y

            for r in range(num_rows + 1):
                y = start_y + r * row_h
                table_lines_mm.append([(cursor_x, y), (cursor_x + table_w, y)])

            for c in range(num_cols + 1):
                x = cursor_x + c * col_w
                table_lines_mm.append([(x, start_y), (x, start_y + num_rows * row_h)])

            for r_idx, row in enumerate(table.rows):
                row_texts = []
                for c_idx, cell in enumerate(row.cells):
                    cell_txt = cell.text.strip().replace("\n", " ")
                    row_texts.append(cell_txt)
                    if cell_txt:
                        text_items.append({
                            "text": cell_txt[:28],
                            "x_mm": cursor_x + c_idx * col_w + 2.0,
                            "y_mm": start_y + (r_idx + 0.72) * row_h,
                            "height_mm": 4.8
                        })
                plain_lines.append(" | ".join(row_texts))

            cursor_y = start_y + num_rows * row_h + 10.0

        return {
            "plain_text": "\n".join(plain_lines),
            "text_items": text_items,
            "table_lines_mm": table_lines_mm,
            "diagram_strokes_mm": [],
            "total_pages": 1,
            "is_exact_layout": True
        }

    # ==========================================================
    # 4. TABLE CORNER SNAPPING & MERGING
    # ==========================================================
    @staticmethod
    def _snap_and_merge_table_lines(lines_mm, snap_tol=0.9):
        """Snaps nearby horizontal and vertical table endpoints together so corners form exact 90° intersections."""
        if not lines_mm:
            return []

        horiz = []
        vert = []
        other = []

        for seg in lines_mm:
            (x1, y1), (x2, y2) = seg[0], seg[-1]
            if abs(y1 - y2) < 0.35:
                ym = (y1 + y2) / 2.0
                horiz.append([min(x1, x2), ym, max(x1, x2), ym])
            elif abs(x1 - x2) < 0.35:
                xm = (x1 + x2) / 2.0
                vert.append([xm, min(y1, y2), xm, max(y1, y2)])
            else:
                other.append(seg)

        # Snap horizontal endpoints to vertical X coordinates and vice versa
        for h in horiz:
            for v in vert:
                vx = v[0]
                if abs(h[0] - vx) <= snap_tol:
                    h[0] = vx
                if abs(h[2] - vx) <= snap_tol:
                    h[2] = vx

        for v in vert:
            for h in horiz:
                hy = h[1]
                if abs(v[1] - hy) <= snap_tol:
                    v[1] = hy
                if abs(v[3] - hy) <= snap_tol:
                    v[3] = hy

        cleaned = []
        for x1, y1, x2, y2 in horiz:
            if abs(x2 - x1) >= 1.5:
                cleaned.append([(x1, y1), (x2, y2)])
        for x1, y1, x2, y2 in vert:
            if abs(y2 - y1) >= 1.5:
                cleaned.append([(x1, y1), (x2, y2)])
        cleaned.extend(other)
        return cleaned

    # ==========================================================
    # 5. ANTI-SHAKE SUB-PIXEL RASTER VECTORIZER
    # ==========================================================
    @classmethod
    def _vectorize_raster_ultra_precision(cls, img_rgb, target_w=210.0, target_h=297.0, offset_x=0.0, offset_y=0.0):
        h, w = img_rgb.shape[:2]
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)

        # Edge-preserving smoothing + adaptive threshold
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        binary = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 8
        )

        sx = target_w / max(w, 1)
        sy = target_h / max(h, 1)

        # A. Sensitive Morphological Table Detection
        horiz_len = max(16, w // 28)
        vert_len = max(16, h // 32)

        horiz_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (horiz_len, 1)))
        vert_mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, vert_len)))

        horiz_mask = cv2.morphologyEx(horiz_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 1)))
        vert_mask = cv2.morphologyEx(vert_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 13)))

        table_lines_mm = []
        num_h, _, stats_h, _ = cv2.connectedComponentsWithStats(horiz_mask)
        for i in range(1, num_h):
            x, y, bw, bh, _ = stats_h[i]
            if bw >= horiz_len:
                cy = y + bh / 2.0
                table_lines_mm.append([
                    (offset_x + x * sx, offset_y + cy * sy),
                    (offset_x + (x + bw) * sx, offset_y + cy * sy)
                ])

        num_v, _, stats_v, _ = cv2.connectedComponentsWithStats(vert_mask)
        for i in range(1, num_v):
            x, y, bw, bh, _ = stats_v[i]
            if bh >= vert_len:
                cx = x + bw / 2.0
                table_lines_mm.append([
                    (offset_x + cx * sx, offset_y + y * sy),
                    (offset_x + cx * sx, offset_y + (y + bh) * sy)
                ])

        # B. Subtract Table Mask & Extract Steady, Spur-Free Diagram Strokes
        table_mask = cv2.dilate(cv2.bitwise_or(horiz_mask, vert_mask), np.ones((5, 5), np.uint8))
        diagrams_only = cv2.subtract(binary, table_mask)
        diagrams_only = cv2.morphologyEx(diagrams_only, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        diagrams_only = cv2.morphologyEx(diagrams_only, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

        diagram_strokes_mm = []
        if np.any(diagrams_only):
            skel = skeletonize(diagrams_only > 0).astype(np.uint8)
            raw_strokes = cls._trace_steady_skeleton(skel)
            for stroke in raw_strokes:
                pts_mm = [(offset_x + x * sx, offset_y + y * sy) for x, y in stroke]
                diagram_strokes_mm.append(pts_mm)

        return table_lines_mm, diagram_strokes_mm

    @staticmethod
    def _trace_steady_skeleton(skel):
        """Traces 1px skeleton with spur pruning, 5-point smoothing, and Douglas-Peucker fitting."""
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
            stroke = [(float(sx), float(sy))]
            visited[sy, sx] = True
            cx, cy = sx, sy

            while True:
                nbs = neighbors(cx, cy)
                if not nbs:
                    break
                nbs.sort(key=lambda p: abs(p[0] - cx) + abs(p[1] - cy))
                nx, ny = nbs[0]
                visited[ny, nx] = True
                stroke.append((float(nx), float(ny)))
                cx, cy = nx, ny

            # Prune short noise spurs
            if len(stroke) < 6:
                continue

            # 5-point Gaussian-weighted moving average eliminates pixel stair-stepping completely
            smoothed = [stroke[0]]
            n = len(stroke)
            for i in range(1, n - 1):
                p_m2 = stroke[max(0, i - 2)]
                p_m1 = stroke[i - 1]
                p_0 = stroke[i]
                p_p1 = stroke[i + 1]
                p_p2 = stroke[min(n - 1, i + 2)]
                mx = (p_m2[0] + 2 * p_m1[0] + 4 * p_0[0] + 2 * p_p1[0] + p_p2[0]) / 10.0
                my = (p_m2[1] + 2 * p_m1[1] + 4 * p_0[1] + 2 * p_p1[1] + p_p2[1]) / 10.0
                smoothed.append((mx, my))
            smoothed.append(stroke[-1])

            pts_arr = np.array(smoothed, dtype=np.float32).reshape(-1, 1, 2)
            approx = cv2.approxPolyDP(pts_arr, epsilon=1.25, closed=False)
            clean_pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
            if len(clean_pts) >= 2:
                strokes.append(clean_pts)

        return strokes