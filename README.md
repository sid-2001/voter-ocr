# Voter OCR to XLSX

This repository contains a PaddleOCR-based script for extracting voter-list pages (PDFs or scanned images) into Excel (`.xlsx`) and CSV files.

## Why PaddleOCR / PP-OCRv5?

The script defaults to PaddleOCR's PP-OCRv5 pipeline because current PaddleOCR documentation presents PP-OCRv5 as the default OCR version and the recommended universal scene-text recognition pipeline. For better accuracy on small voter-list text, the script asks PaddleOCR 3.x for `PP-OCRv5_server_det` and `PP-OCRv5_server_rec` when `--high-accuracy` is enabled.

## Install

Use Python 3.10+ in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> First execution may download PaddleOCR models. Use a machine with enough RAM; GPU is optional but faster for bulk PDFs.

## Run

```bash
python scripts/voter_list_to_xls.py \
  --input /path/to/voter-list.pdf \
  --output output/voter_list.xlsx
```

For a directory of PDFs/images:

```bash
python scripts/voter_list_to_xls.py \
  --input /path/to/pages_or_pdfs \
  --output output/all_voters.xlsx \
  --raw-json output/all_voters_raw.json
```

The script also writes a `.csv` next to the `.xlsx` unless `--no-csv` is used.

## Extracted columns

- `source_file`
- `page_number`
- `card_index`
- `constituency_no`
- `constituency_name`
- `part_no`
- `section_no`
- `section_name`
- `serial_no`
- `epic_no`
- `voter_name`
- `relation_type`
- `relation_name`
- `house_number`
- `age`
- `gender`
- `ocr_confidence`
- `raw_text`

## Accuracy tips

1. Use original PDFs where possible. If only images are available, scan at 300-400 DPI.
2. Keep `--dpi 350` for PDF rendering; increase to `400` if small text is missed.
3. Keep `--high-accuracy` enabled for the server PP-OCRv5 models.
4. Review rows with low `ocr_confidence` and the `raw_text` column before using the spreadsheet.
5. If a state roll uses a different layout, adjust `--columns` or the parsing regexes in `scripts/voter_list_to_xls.py`.

## Development checks

```bash
python -m py_compile scripts/voter_list_to_xls.py
pytest
```
