#!/usr/bin/env python3
"""Extract Indian voter-list pages to XLSX/CSV using PaddleOCR.

The script is tuned for voter-list pages laid out as repeated voter cards, such
as the provided sample image: 3 columns of boxed entries, with fields like
`Name`, `Fathers Name`/`Husbands Name`, `House Number`, `Age`, `Gender`, serial
number, and EPIC/voter ID.

Accuracy-oriented design:
* renders PDFs at high DPI before OCR;
* deskews/thresholds pages for cleaner text;
* detects each voter card by page ruling/contours, with a safe grid fallback;
* runs OCR per card so nearby boxes do not mix;
* parses normalized OCR text with tolerant regexes for common OCR variants;
* emits confidence and raw OCR text so low-confidence rows can be reviewed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Iterator, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.parser_utils import clean_value, parse_card_text, parse_metadata

import cv2
import numpy as np
import pandas as pd

SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_PDFS = {".pdf"}


@dataclass(frozen=True)
class PageImage:
    source: Path
    page_number: int
    image: np.ndarray


@dataclass
class VoterRecord:
    source_file: str
    page_number: int
    card_index: int
    constituency_no: str | None = None
    constituency_name: str | None = None
    part_no: str | None = None
    section_no: str | None = None
    section_name: str | None = None
    serial_no: str | None = None
    epic_no: str | None = None
    voter_name: str | None = None
    relation_type: str | None = None
    relation_name: str | None = None
    house_number: str | None = None
    age: int | None = None
    gender: str | None = None
    ocr_confidence: float | None = None
    raw_text: str | None = None


def natural_key(path: Path) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def collect_inputs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    files = [
        p
        for p in input_path.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGES.union(SUPPORTED_PDFS)
    ]
    return sorted(files, key=natural_key)


def render_pdf(path: Path, dpi: int) -> Iterator[PageImage]:
    import pypdfium2 as pdfium

    scale = dpi / 72.0
    with pdfium.PdfDocument(str(path)) as pdf:
        for index, page in enumerate(pdf, start=1):
            bitmap = page.render(scale=scale, rotation=0)
            pil_image = bitmap.to_pil().convert("RGB")
            yield PageImage(path, index, cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR))


def load_pages(paths: Iterable[Path], dpi: int) -> Iterator[PageImage]:
    for path in paths:
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_PDFS:
            yield from render_pdf(path, dpi)
        elif suffix in SUPPORTED_IMAGES:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Unable to read image: {path}")
            yield PageImage(path, 1, image)


def rotate_bound(image: np.ndarray, angle: float) -> np.ndarray:
    height, width = image.shape[:2]
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_width = int((height * sin) + (width * cos))
    new_height = int((height * cos) + (width * sin))
    matrix[0, 2] += (new_width / 2) - center[0]
    matrix[1, 2] += (new_height / 2) - center[1]
    return cv2.warpAffine(image, matrix, (new_width, new_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def deskew(image: np.ndarray, max_angle: float = 4.0) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=180, minLineLength=gray.shape[1] // 3, maxLineGap=20)
    if lines is None:
        return image
    angles: list[float] = []
    for line in lines[:, 0]:
        x1, y1, x2, y2 = line
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(angle) <= max_angle:
            angles.append(angle)
    if not angles:
        return image
    angle = float(median(angles))
    if abs(angle) < 0.1:
        return image
    return rotate_bound(image, angle)


def preprocess_for_lines(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, 8, 7, 21)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)


def merge_close_boxes(boxes: Sequence[tuple[int, int, int, int]], tolerance: int = 12) -> list[tuple[int, int, int, int]]:
    merged: list[tuple[int, int, int, int]] = []
    for x, y, w, h in sorted(boxes, key=lambda b: (b[1], b[0])):
        consumed = False
        for idx, (mx, my, mw, mh) in enumerate(merged):
            if abs(x - mx) < tolerance and abs(y - my) < tolerance and abs((x + w) - (mx + mw)) < tolerance and abs((y + h) - (my + mh)) < tolerance:
                nx = min(x, mx)
                ny = min(y, my)
                nr = max(x + w, mx + mw)
                nb = max(y + h, my + mh)
                merged[idx] = (nx, ny, nr - nx, nb - ny)
                consumed = True
                break
        if not consumed:
            merged.append((x, y, w, h))
    return merged


def detect_card_boxes(image: np.ndarray, expected_columns: int = 3) -> list[tuple[int, int, int, int]]:
    """Find voter-card rectangles, falling back to a regular 3-column grid."""
    height, width = image.shape[:2]
    binary = preprocess_for_lines(image)

    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, width // 28), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, height // 40)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel, iterations=1)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel, iterations=1)
    ruling = cv2.dilate(cv2.add(horizontal, vertical), np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(ruling, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_w = width * 0.20
    max_w = width * 0.38
    min_h = height * 0.045
    max_h = height * 0.16
    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if min_w <= w <= max_w and min_h <= h <= max_h and y > height * 0.02 and y + h < height * 0.985:
            boxes.append((x, y, w, h))

    boxes = merge_close_boxes(boxes)
    if len(boxes) >= expected_columns * 2:
        return sorted(boxes, key=lambda b: (round(b[1] / max(1, height // 100)), b[0]))

    return fallback_grid_boxes(image, expected_columns=expected_columns)


def fallback_grid_boxes(image: np.ndarray, expected_columns: int = 3) -> list[tuple[int, int, int, int]]:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(gray, 245, 255, cv2.THRESH_BINARY_INV)[1]
    row_density = binary.mean(axis=1)
    content_rows = np.where(row_density > max(1.5, np.percentile(row_density, 70)))[0]
    if len(content_rows) == 0:
        top, bottom = int(height * 0.05), int(height * 0.965)
    else:
        top = max(0, int(content_rows[0] - height * 0.005))
        bottom = min(height, int(content_rows[-1] + height * 0.005))

    # Avoid common header/footer bands if present.
    top = max(top, int(height * 0.035))
    bottom = min(bottom, int(height * 0.965))

    card_width = width / expected_columns
    estimated_card_height = card_width * 0.39
    rows = max(1, round((bottom - top) / estimated_card_height))
    card_height = (bottom - top) / rows
    boxes = []
    for row in range(rows):
        for col in range(expected_columns):
            x = int(col * card_width)
            y = int(top + row * card_height)
            w = int(card_width)
            h = int(card_height)
            pad_x = int(width * 0.006)
            pad_y = int(height * 0.003)
            boxes.append((x + pad_x, y + pad_y, max(1, w - 2 * pad_x), max(1, h - 2 * pad_y)))
    return boxes


def crop_box(image: np.ndarray, box: tuple[int, int, int, int], pad: int = 6) -> np.ndarray:
    x, y, w, h = box
    height, width = image.shape[:2]
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(width, x + w + pad)
    y2 = min(height, y + h + pad)
    return image[y1:y2, x1:x2]


def build_ocr(args: argparse.Namespace) -> Any:
    from paddleocr import PaddleOCR

    common = {
        "lang": args.lang,
        "ocr_version": args.ocr_version,
    }
    if args.high_accuracy:
        # PaddleOCR 3.x names. Prefer server models when available; PaddleOCR will
        # download them on first run unless local model dirs are configured.
        common.update(
            {
                "text_detection_model_name": "PP-OCRv5_server_det",
                "text_recognition_model_name": "PP-OCRv5_server_rec",
            }
        )

    try:
        return PaddleOCR(
            **common,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=args.use_textline_orientation,
        )
    except TypeError:
        # Compatibility with PaddleOCR 2.x installations.
        return PaddleOCR(
            lang=args.lang,
            use_angle_cls=args.use_textline_orientation,
            show_log=False,
        )


def normalize_ocr_result(result: Any) -> tuple[str, float | None]:
    lines: list[str] = []
    confidences: list[float] = []

    def visit(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, dict):
            if "rec_texts" in node:
                lines.extend(str(text) for text in node.get("rec_texts") or [])
                confidences.extend(float(score) for score in node.get("rec_scores") or [] if score is not None)
            elif "text" in node:
                lines.append(str(node["text"]))
                if "confidence" in node and node["confidence"] is not None:
                    confidences.append(float(node["confidence"]))
            else:
                for value in node.values():
                    visit(value)
            return
        if isinstance(node, (list, tuple)):
            if len(node) >= 2 and isinstance(node[1], (list, tuple)) and len(node[1]) >= 2 and isinstance(node[1][0], str):
                lines.append(node[1][0])
                try:
                    confidences.append(float(node[1][1]))
                except (TypeError, ValueError):
                    pass
                return
            for item in node:
                visit(item)

    visit(result)
    text = "\n".join(line.strip() for line in lines if str(line).strip())
    confidence = round(float(np.mean(confidences)), 4) if confidences else None
    return text, confidence


def run_ocr(ocr: Any, image: np.ndarray) -> tuple[str, float | None]:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    try:
        result = ocr.predict(rgb)
    except AttributeError:
        result = ocr.ocr(rgb, cls=True)
    return normalize_ocr_result(result)


def extract_records(args: argparse.Namespace) -> list[VoterRecord]:
    input_files = collect_inputs(Path(args.input))
    if not input_files:
        raise FileNotFoundError(f"No PDF/image files found under: {args.input}")

    ocr = build_ocr(args)
    records: list[VoterRecord] = []
    for page in load_pages(input_files, args.dpi):
        image = deskew(page.image) if args.deskew else page.image
        metadata_text, _ = run_ocr(ocr, image[: max(1, int(image.shape[0] * 0.07)), :])
        metadata = parse_metadata(metadata_text)
        boxes = detect_card_boxes(image, expected_columns=args.columns)
        for card_index, box in enumerate(boxes, start=1):
            crop = crop_box(image, box)
            text, confidence = run_ocr(ocr, crop)
            parsed = parse_card_text(text)
            if not parsed.get("voter_name") and not parsed.get("epic_no") and not parsed.get("serial_no"):
                continue
            records.append(
                VoterRecord(
                    source_file=str(page.source),
                    page_number=page.page_number,
                    card_index=card_index,
                    **metadata,
                    **parsed,
                    ocr_confidence=confidence,
                    raw_text=text,
                )
            )
    return records


def write_outputs(records: Sequence[VoterRecord], output: Path, write_csv: bool = True, raw_json: Path | None = None) -> None:
    rows = [asdict(record) for record in records]
    df = pd.DataFrame(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(output, index=False)
    if write_csv:
        csv_path = output.with_suffix(".csv")
        df.to_csv(csv_path, index=False, quoting=csv.QUOTE_MINIMAL)
    if raw_json is not None:
        raw_json.parent.mkdir(parents=True, exist_ok=True)
        raw_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract voter-list PDF/image data into XLSX using PaddleOCR.")
    parser.add_argument("--input", "-i", required=True, help="Input PDF/image file or directory containing PDFs/images.")
    parser.add_argument("--output", "-o", default="output/voter_list.xlsx", help="Output XLSX path.")
    parser.add_argument("--dpi", type=int, default=350, help="PDF render DPI. 300-400 is recommended for small voter-list text.")
    parser.add_argument("--columns", type=int, default=3, help="Number of voter-card columns on each page.")
    parser.add_argument("--lang", default="en", help="PaddleOCR language code. Use 'en' for English electoral rolls.")
    parser.add_argument("--ocr-version", default="PP-OCRv5", help="PaddleOCR OCR version. PP-OCRv5 is recommended for accuracy.")
    parser.add_argument("--high-accuracy", action=argparse.BooleanOptionalAction, default=True, help="Prefer PP-OCRv5 server detection/recognition models when PaddleOCR supports model-name selection.")
    parser.add_argument("--deskew", action=argparse.BooleanOptionalAction, default=True, help="Deskew pages before card detection/OCR.")
    parser.add_argument("--use-textline-orientation", action=argparse.BooleanOptionalAction, default=True, help="Enable text-line orientation classifier.")
    parser.add_argument("--no-csv", action="store_true", help="Do not also write a CSV next to the XLSX file.")
    parser.add_argument("--raw-json", help="Optional JSON path for raw extracted rows.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    records = extract_records(args)
    write_outputs(records, Path(args.output), write_csv=not args.no_csv, raw_json=Path(args.raw_json) if args.raw_json else None)
    print(f"Wrote {len(records)} records to {args.output}")
    if not records:
        print("Warning: no records were extracted. Check scan quality, DPI, page layout, and --columns.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
