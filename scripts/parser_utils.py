"""Parsing helpers for voter-list OCR text."""

from __future__ import annotations

import re
from typing import Any


def clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip(" :-—|")
    return value or None


def parse_metadata(text: str) -> dict[str, str | None]:
    flat = re.sub(r"\s+", " ", text)
    metadata: dict[str, str | None] = {
        "constituency_no": None,
        "constituency_name": None,
        "part_no": None,
        "section_no": None,
        "section_name": None,
    }
    ac_match = re.search(r"Assembly\s+Constituency\s+No\s+and\s+Name\s*[:：]?\s*(\d+)\s*[-–]\s*([^\n]+?)(?=\s+Part\s+No|\s+Section\s+No|$)", flat, re.I)
    if ac_match:
        metadata["constituency_no"] = clean_value(ac_match.group(1))
        metadata["constituency_name"] = clean_value(ac_match.group(2))
    part_match = re.search(r"Part\s+No\.?\s*[:：]?\s*(\d+)", flat, re.I)
    if part_match:
        metadata["part_no"] = clean_value(part_match.group(1))
    section_match = re.search(r"Section\s+No\s+and\s+Name\s*[:：]?\s*(\d+)\s*[-–]\s*([^\n]+?)(?=\s+\d{1,4}\s|$)", flat, re.I)
    if section_match:
        metadata["section_no"] = clean_value(section_match.group(1))
        metadata["section_name"] = clean_value(section_match.group(2))
    return metadata


def parse_card_text(text: str) -> dict[str, Any]:
    lines = [clean_value(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    flat = re.sub(r"\s+", " ", " ".join(lines))
    flat = flat.replace("：", ":")

    data: dict[str, Any] = {
        "serial_no": None,
        "epic_no": None,
        "voter_name": None,
        "relation_type": None,
        "relation_name": None,
        "house_number": None,
        "age": None,
        "gender": None,
    }

    for line in lines[:4]:
        if re.fullmatch(r"\d{1,5}", line):
            data["serial_no"] = line
            break
    if data["serial_no"] is None:
        serial_match = re.search(r"(?:^|\s)(\d{1,5})(?=\s+[A-Z]{2,4}\d{5,})", flat)
        if not serial_match:
            serial_match = re.search(r"(?:^|\s)(\d{1,5})(?=\s+Name\s*:)", flat, re.I)
        if serial_match:
            data["serial_no"] = serial_match.group(1)

    epic_candidates = re.findall(r"\b[A-Z]{2,4}\s*[A-Z0-9]{5,10}\b", flat)
    if epic_candidates:
        data["epic_no"] = epic_candidates[-1].replace(" ", "")

    name_match = re.search(r"\bName\s*[:;]?\s*(.+?)(?=\s+(?:Fathers?|Father'?s|Husbands?|Husband'?s|Mothers?|Mother'?s)\s+Name\s*[:;]?|\s+House\s+Number\s*[:;]?|\s+Age\s*[:;]?|$)", flat, re.I)
    if name_match:
        data["voter_name"] = clean_value(name_match.group(1))

    relation_match = re.search(r"\b(Fathers?|Father'?s|Husbands?|Husband'?s|Mothers?|Mother'?s)\s+Name\s*[:;]?\s*(.+?)(?=\s+House\s+Number\s*[:;]?|\s+Age\s*[:;]?|$)", flat, re.I)
    if relation_match:
        relation = relation_match.group(1).lower().replace("'", "")
        if relation.startswith("father"):
            data["relation_type"] = "Father"
        elif relation.startswith("husband"):
            data["relation_type"] = "Husband"
        elif relation.startswith("mother"):
            data["relation_type"] = "Mother"
        data["relation_name"] = clean_value(relation_match.group(2))

    house_match = re.search(r"\bHouse\s+Number\s*[:;]?\s*(.+?)(?=\s+Age\s*[:;]?|\s+Gender\s*[:;]?|$)", flat, re.I)
    if house_match:
        data["house_number"] = clean_value(house_match.group(1))

    age_match = re.search(r"\bAge\s*[:;]?\s*(\d{1,3})\b", flat, re.I)
    if age_match:
        age = int(age_match.group(1))
        if 0 < age < 130:
            data["age"] = age

    gender_match = re.search(r"\bGender\s*[:;]?\s*(Male|Female|M|F)\b", flat, re.I)
    if gender_match:
        gender = gender_match.group(1).upper()
        data["gender"] = "Male" if gender == "M" or gender.startswith("MALE") else "Female"

    return data
