import math
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
    1:1 Exact Drawing + Tonal Shading Extractor:
    - Traces crisp outlines and fine sketch lines using directional momentum.
    - Converts gray shadows and pencil shading into multi-density 45° / -45° cross-hatching
      so a pen plotter reproduces light, medium, and dark shading faithfully.
    """

    A4_W = 210.0
    A4_H = 297.0

    @classmethod
    def extract_file(cls, file_path, page_num=0):
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return cls._extract_pdf(file_path, page_num)
        elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tiff"):
            return cls._extract_image(file_path)
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
    # 1. PDF EXTRACTOR
    # ==========================================================
    @classmethod
    def _extract_pdf(cls, pdf_path, page_num=0):
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        page_num = max(0, min(page_num, total_pages - 1))
        page = doc[page_num]

        pdf_w = max(page.rect.width, 1)
        pdf_h = max(page.rect.height, 1)
        scale_x = cls.A4_W / pdf_w
        scale_y = cls.A4_H / pdf_h

        text_items = []
        plain_lines = []
        text_dict = page.get_text("dict")

        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                full_line_text = "".join(s["text"] for s in spans).strip()
                if not full_line_text:
                    continue
                plain_lines.append(full_line_text)
                first_span = spans[0]
                ox, oy = first_span["origin"]
                avg_pt = sum(s.get("size", 11.0) for s in spans) / len(spans)
                text_items.append({
                    "text": full_line_text,
                    "x_mm": ox * scale_x,
                    "y_mm": oy * scale_y,
                    "height_mm": max(3.4, min(12.0, avg_pt * 0.3528 * scale_x))
                })

        table_lines_mm = []
        table_bboxes = []
        try:
            tabs = page.find_tables()
            for tab in tabs.tables:
                table_bboxes.append(fitz.Rect(tab.bbox))
                for cell in tab.cells:
                    if cell is None:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    x0, y0 = cx0 * scale_x, cy0 * scale_y
                    x1, y1 = cx1 * scale_x, cy1 * scale_y
                    table_lines_mm.append([(x0, y0), (x1, y0)])
                    table_lines_mm.append([(x1, y0), (x1, y1)])
                    table_lines_mm.append([(x1, y1), (x0, y1)])
                    table_lines_mm.append([(x0, y1), (x0, y0)])
        except Exception:
            pass

        table_lines_mm = cls._deduplicate_segments(table_lines_mm)

        diagram_strokes_mm = []
        for path in page.get_drawings():
            has_stroke = path.get("color") is not None
            path_rect = path.get("rect")
            if path_rect and any(tb.contains(path_rect) for tb in table_bboxes):
                continue

            for item in path.get("items", []):
                op = item[0]
                if op == "l" and has_stroke:
                    p1, p2 = item[1], item[2]
                    x1, y1 = p1.x * scale_x, p1.y * scale_y
                    x2, y2 = p2.x * scale_x, p2.y * scale_y
                    diagram_strokes_mm.append([(x1, y1), (x2, y2)])

                elif op == "re" and has_stroke:
                    rect = item[1]
                    x0, y0 = rect.x0 * scale_x, rect.y0 * scale_y
                    x1, y1 = rect.x1 * scale_x, rect.y1 * scale_y
                    if abs(x1 - x0) > 202 and abs(y1 - y0) > 288:
                        continue
                    diagram_strokes_mm.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])

                elif op == "c" and has_stroke:
                    p0, p1, p2, p3 = item[1], item[2], item[3], item[4]
                    curve_pts = []
                    for step in range(21):
                        t = step / 20.0
                        mt = 1.0 - t
                        bx = (mt**3)*p0.x + 3*(mt**2)*t*p1.x + 3*mt*(t**2)*p2.x + (t**3)*p3.x
                        by = (mt**3)*p0.y + 3*(mt**2)*t*p1.y + 3*mt*(t**2)*p2.y + (t**3)*p3.y
                        curve_pts.append((bx * scale_x, by * scale_y))
                    diagram_strokes_mm.append(curve_pts)

        for img_info in page.get_images(full=True):
            xref = img_info[0]
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            if rect.width < 25 or rect.height < 25:
                continue

            base_img = doc.extract_image(xref)
            img_arr = np.frombuffer(base_img["image"], dtype=np.uint8)
            bgr = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)
            if bgr is None:
                continue
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

            r_diags = cls._vectorize_shaded_sketch(
                rgb,
                target_w=rect.width * scale_x,
                target_h=rect.height * scale_y,
                offset_x=rect.x0 * scale_x,
                offset_y=rect.y0 * scale_y
            )
            diagram_strokes_mm.extend(r_diags)

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
    # 2. IMAGE / SKETCH EXTRACTOR (Outlines + Multi-Tone Shading)
    # ==========================================================
    @classmethod
    def _extract_image(cls, img_path):
        pil_img = Image.open(img_path).convert("RGB")
        img_rgb = np.array(pil_img)
        h, w = img_rgb.shape[:2]

        if max(w, h) < 1200:
            img_rgb = cv2.resize(img_rgb, (w * 2, h * 2), interpolation=cv2.INTER_LANCZOS4)
            pil_img = Image.fromarray(img_rgb)
            h, w = img_rgb.shape[:2]

        avail_w = cls.A4_W - 20.0
        avail_h = cls.A4_H - 20.0
        scale = min(avail_w / max(w, 1), avail_h / max(h, 1))
        offset_x = (cls.A4_W - (w * scale)) / 2.0
        offset_y = (cls.A4_H - (h * scale)) / 2.0

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
                    if len(word) < 2 or not any(c.isalpha() for c in word) or conf < 78:
                        continue

                    bx, by, bw, bh = (
                        ocr_data["left"][i],
                        ocr_data["top"][i],
                        ocr_data["width"][i],
                        ocr_data["height"][i],
                    )

                    pad = 2
                    y1, y2 = max(0, by - pad), min(h, by + bh + pad)
                    x1, x2 = max(0, bx - pad), min(w, bx + bw + pad)
                    bg_color = tuple(int(c) for c in np.median(img_rgb[y1:y2, x1:x2].reshape(-1, 3), axis=0))
                    cv2.rectangle(graphics_mask_img, (x1, y1), (x2, y2), bg_color, -1)

                    line_key = (ocr_data["block_num"][i], ocr_data["line_num"][i])
                    lines_map.setdefault(line_key, []).append((bx, by, bw, bh, word))

                for line_words in lines_map.values():
                    line_words.sort(key=lambda item: item[0])
                    full_txt = " ".join(item[4] for item in line_words)
                    plain_lines.append(full_txt)
                    bx0, by0, _, bh0, _ = line_words[0]
                    avg_h = sum(item[3] for item in line_words) / len(line_words)
                    text_items.append({
                        "text": full_txt,
                        "x_mm": offset_x + (bx0 * scale),
                        "y_mm": offset_y + ((by0 + avg_h * 0.85) * scale),
                        "height_mm": max(3.5, min(12.0, avg_h * scale * 0.9))
                    })
            except Exception:
                pass

        diagram_strokes_mm = cls._vectorize_shaded_sketch(
            graphics_mask_img,
            target_w=(w * scale),
            target_h=(h * scale),
            offset_x=offset_x,
            offset_y=offset_y
        )

        return {
            "plain_text": "\n".join(plain_lines) if plain_lines else "(Sketch outlines + tonal shading vectorized)",
            "text_items": text_items,
            "table_lines_mm": [],
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
    # 4. LINE-ART + TONAL HATCH SHADING VECTORIZER
    # ==========================================================
    @staticmethod
    def _deduplicate_segments(segments, tol=0.4):
        unique = []
        for seg in segments:
            (x1, y1), (x2, y2) = seg[0], seg[-1]
            if math.hypot(x2 - x1, y2 - y1) < 1.0:
                continue
            p_a = (round(x1 / tol), round(y1 / tol))
            p_b = (round(x2 / tol), round(y2 / tol))
            key = tuple(sorted([p_a, p_b]))
            if not any(u[0] == key for u in unique):
                unique.append((key, [(x1, y1), (x2, y2)]))
        return [u[1] for u in unique]

    @classmethod
    def _vectorize_shaded_sketch(cls, img_rgb, target_w=210.0, target_h=297.0, offset_x=0.0, offset_y=0.0):
        """
        1. Extracts all dark outlines and fine sketch lines first.
        2. Detects light, medium, and dark gray shading regions and fills them with
           clipped 45° / -45° cross-hatch strokes so the pen plotter shades shadows naturally.
        """
        h, w = img_rgb.shape[:2]
        gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)

        # Normalize paper background brightness so off-white paper doesn't trigger false shading
        bg_estimate = cv2.GaussianBlur(gray, (51, 51), 0)
        norm_gray = np.clip(255.0 * (gray.astype(np.float32) / np.maximum(bg_estimate.astype(np.float32), 1.0)), 0, 255).astype(np.uint8)

        sx = target_w / max(w, 1)
        sy = target_h / max(h, 1)
        strokes_mm = []

        # --- PASS 1: Crisp Outlines & Fine Pencil Lines (Adaptive Local Contrast) ---
        line_binary = cv2.adaptiveThreshold(
            norm_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 21, 12
        )

        # Separate solid dark fills (> 7px thick) from thin pencil lines
        solid_core = cv2.erode(line_binary, np.ones((7, 7), np.uint8))
        solid_regions = cv2.dilate(solid_core, np.ones((9, 9), np.uint8))
        solid_regions = cv2.bitwise_and(line_binary, solid_regions)
        thin_lines = cv2.subtract(line_binary, solid_regions)

        if np.any(solid_regions):
            contours, _ = cv2.findContours(solid_regions, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            for cnt in contours:
                if cv2.arcLength(cnt, True) < 8:
                    continue
                approx = cv2.approxPolyDP(cnt, epsilon=0.75, closed=True)
                if len(approx) >= 3:
                    pts_mm = [(offset_x + float(p[0][0]) * sx, offset_y + float(p[0][1]) * sy) for p in approx]
                    pts_mm.append(pts_mm[0])
                    strokes_mm.append(pts_mm)

        if np.any(thin_lines):
            line_closed = cv2.morphologyEx(thin_lines, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            skel = skeletonize(line_closed > 0).astype(np.uint8)
            for stroke in cls._trace_momentum_skeleton(skel):
                pts_mm = [(offset_x + x * sx, offset_y + y * sy) for x, y in stroke]
                strokes_mm.append(pts_mm)

        # --- PASS 2: Multi-Tone Hatch Shading for Gray Shadows ---
        # Smooth out fine pencil texture to capture clean shadow zones
        tone_map = cv2.GaussianBlur(norm_gray, (7, 7), 0)

        # Tone 1: Light Shading (175 <= tone < 228) -> wide 45° hatches (2.5 mm spacing)
        light_mask = ((tone_map >= 175) & (tone_map < 228)).astype(np.uint8) * 255
        light_mask = cv2.morphologyEx(light_mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        strokes_mm.extend(cls._generate_hatch_lines(light_mask, spacing_mm=2.5, angle_deg=45, sx=sx, sy=sy, ox=offset_x, oy=offset_y))

        # Tone 2: Medium Shading (110 <= tone < 175) -> medium 45° hatches (1.35 mm spacing)
        med_mask = ((tone_map >= 110) & (tone_map < 175)).astype(np.uint8) * 255
        med_mask = cv2.morphologyEx(med_mask, cv2.MORPH_OPEN, np.ones((4, 4), np.uint8))
        strokes_mm.extend(cls._generate_hatch_lines(med_mask, spacing_mm=1.35, angle_deg=45, sx=sx, sy=sy, ox=offset_x, oy=offset_y))

        # Tone 3: Dark Shadow / Solid Fill (tone < 110) -> cross-hatched 45° + -45° (0.95 mm spacing)
        dark_mask = (tone_map < 110).astype(np.uint8) * 255
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        strokes_mm.extend(cls._generate_hatch_lines(dark_mask, spacing_mm=0.95, angle_deg=45, sx=sx, sy=sy, ox=offset_x, oy=offset_y))
        strokes_mm.extend(cls._generate_hatch_lines(dark_mask, spacing_mm=1.15, angle_deg=-45, sx=sx, sy=sy, ox=offset_x, oy=offset_y))

        return strokes_mm

    @staticmethod
    def _generate_hatch_lines(mask, spacing_mm, angle_deg, sx, sy, ox, oy):
        """
        Generates parallel diagonal hatch strokes clipped strictly inside the white pixels
        of `mask` at the requested millimeter spacing and angle.
        """
        if not np.any(mask):
            return []

        h, w = mask.shape
        # Rotate mask so diagonal hatching becomes simple horizontal scanlines
        center = (w / 2.0, h / 2.0)
        rot_mat = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
        inv_rot_mat = cv2.getRotationMatrix2D(center, -angle_deg, 1.0)

        diag = int(math.hypot(w, h)) + 4
        rot_mat[0, 2] += (diag - w) / 2.0
        rot_mat[1, 2] += (diag - h) / 2.0
        inv_rot_mat[0, 2] = w / 2.0 - (diag / 2.0) * inv_rot_mat[0, 0] - (diag / 2.0) * inv_rot_mat[0, 1]
        inv_rot_mat[1, 2] = h / 2.0 - (diag / 2.0) * inv_rot_mat[1, 0] - (diag / 2.0) * inv_rot_mat[1, 1]

        rot_mask = cv2.warpAffine(mask, rot_mat, (diag, diag), flags=cv2.INTER_NEAREST)

        step_px = max(2, int(round(spacing_mm / max(sy, 1e-4))))
        hatch_strokes = []

        for y in range(0, diag, step_px):
            row = rot_mask[y, :]
            if not np.any(row):
                continue

            # Find contiguous runs of shaded pixels along scanline y
            padded = np.concatenate(([0], (row > 0).astype(np.uint8), [0]))
            diffs = np.diff(padded)
            starts = np.where(diffs == 1)[0]
            ends = np.where(diffs == -1)[0]

            for x_start, x_end in zip(starts, ends):
                if (x_end - x_start) < 3:  # Skip tiny 1-2px noise dots
                    continue

                # Map endpoints back to original image coordinates
                p0_rot = np.array([x_start, y, 1.0])
                p1_rot = np.array([x_end - 1, y, 1.0])
                x0, y0 = inv_rot_mat @ p0_rot
                x1, y1 = inv_rot_mat @ p1_rot

                # Clip safely inside image bounds
                x0 = max(0.0, min(w - 1.0, float(x0)))
                y0 = max(0.0, min(h - 1.0, float(y0)))
                x1 = max(0.0, min(w - 1.0, float(x1)))
                y1 = max(0.0, min(h - 1.0, float(y1)))

                hatch_strokes.append([
                    (ox + x0 * sx, oy + y0 * sy),
                    (ox + x1 * sx, oy + y1 * sy)
                ])

        return hatch_strokes

    @staticmethod
    def _trace_momentum_skeleton(skel):
        h, w = skel.shape
        visited = np.zeros_like(skel, dtype=bool)
        strokes = []

        ys, xs = np.where(skel > 0)
        if len(xs) == 0:
            return []

        def get_unvisited_neighbors(cx, cy):
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
            cnt = sum(
                1 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dx != 0 or dy != 0) and 0 <= x + dx < w and 0 <= y + dy < h and skel[y + dy, x + dx] > 0
            )
            if cnt == 1:
                endpoints.append((x, y))

        for sx, sy in endpoints + list(zip(xs, ys)):
            if visited[sy, sx]:
                continue

            stroke = [(float(sx), float(sy))]
            visited[sy, sx] = True
            cx, cy = sx, sy
            dir_x, dir_y = 0.0, 0.0

            while True:
                nbs = get_unvisited_neighbors(cx, cy)
                if not nbs:
                    break

                if len(nbs) == 1 or (dir_x == 0.0 and dir_y == 0.0):
                    nx, ny = nbs[0]
                else:
                    best_nb = nbs[0]
                    best_score = -999.0
                    for cand_x, cand_y in nbs:
                        vx = float(cand_x - cx)
                        vy = float(cand_y - cy)
                        norm = math.hypot(vx, vy) or 1.0
                        score = (vx / norm) * dir_x + (vy / norm) * dir_y
                        if score > best_score:
                            best_score = score
                            best_nb = (cand_x, cand_y)
                    nx, ny = best_nb

                step_x = float(nx - cx)
                step_y = float(ny - cy)
                step_len = math.hypot(step_x, step_y) or 1.0
                dir_x = 0.7 * dir_x + 0.3 * (step_x / step_len)
                dir_y = 0.7 * dir_y + 0.3 * (step_y / step_len)

                visited[ny, nx] = True
                stroke.append((float(nx), float(ny)))
                cx, cy = nx, ny

            if len(stroke) < 2:
                continue

            if len(stroke) >= 4:
                smoothed = [stroke[0]]
                for i in range(1, len(stroke) - 1):
                    mx = 0.25 * stroke[i - 1][0] + 0.5 * stroke[i][0] + 0.25 * stroke[i + 1][0]
                    my = 0.25 * stroke[i - 1][1] + 0.5 * stroke[i][1] + 0.25 * stroke[i + 1][1]
                    smoothed.append((mx, my))
                smoothed.append(stroke[-1])
            else:
                smoothed = stroke

            pts_arr = np.array(smoothed, dtype=np.float32).reshape(-1, 1, 2)
            approx = cv2.approxPolyDP(pts_arr, epsilon=0.65, closed=False)
            clean_pts = [(float(p[0][0]), float(p[0][1])) for p in approx]
            if len(clean_pts) >= 2:
                strokes.append(clean_pts)

        return strokes