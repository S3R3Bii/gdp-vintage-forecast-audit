"""Reconcile selected cells through an independent OOXML reader, when originals exist."""

from pathlib import Path
from decimal import Decimal
from collections import Counter
import argparse
import csv
import hashlib
import json
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def original_sheets(path):
    result = {}
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            tree = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = ["".join(node.itertext()) for node in tree.findall("s:si", NS)]
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {node.attrib["Id"]: node.attrib["Target"] for node in relationships}
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        for sheet in workbook.findall("s:sheets/s:sheet", NS):
            identity = sheet.attrib[
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            ]
            target = targets[identity]
            member = (
                target.lstrip("/")
                if target.startswith("/")
                else posixpath.normpath("xl/" + target)
            )
            cells = {}
            for cell in ET.fromstring(archive.read(member)).findall(
                ".//s:sheetData/s:row/s:c", NS
            ):
                kind = cell.attrib.get("t", "n")
                value = cell.find("s:v", NS)
                if kind == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall("s:is//s:t", NS))
                    kind = "text"
                elif value is None or value.text is None:
                    value = None
                    kind = "empty"
                elif kind == "s":
                    value = strings[int(value.text)]
                    kind = "text"
                else:
                    value = value.text
                    kind = "numeric" if kind == "n" else "source_missing"
                cells[cell.attrib["r"]] = (kind, value)
            result[sheet.attrib["name"]] = cells
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path)
    args = parser.parse_args()
    manifest = json.loads(
        (ROOT / "data/source_manifest.json").read_text(encoding="utf-8")
    )
    checks = 0

    def check(condition, message):
        nonlocal checks
        assert condition, message
        checks += 1

    for name, digest in manifest["file_sha256"].items():
        check(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest,
            ("Projection hash", name),
        )
    originals = {}
    if args.private_dir:
        for request in manifest["requests"]:
            path = args.private_dir / request["file"]
            check(
                hashlib.sha256(path.read_bytes()).hexdigest() == request["sha256"],
                "Workbook hash",
            )
            originals[request["name"]] = original_sheets(path)
    counts = {}
    for filename, source in [
        ("levels.csv", "quarterly"),
        ("first_release.csv", "releases"),
    ]:
        with (ROOT / "data" / filename).open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        check(
            len(rows)
            == manifest[
                "level_cells" if source == "quarterly" else "first_release_cells"
            ],
            "Complete projection",
        )
        keys = set()
        for row in rows:
            key = (row.get("vintage"), row["observation"])
            check(key not in keys, "No duplicate selected key")
            keys.add(key)
            check(
                re.fullmatch(r"(?:19|20)\d{2}Q[1-4]", row["observation"]) is not None,
                "Observation key",
            )
            check(row["state"] in ["numeric", "empty", "source_missing"], "State")
            if row["state"] == "numeric":
                check(Decimal(row["value"]).is_finite(), "Finite selected value")
            elif row["state"] == "empty":
                check(row["value"] == "", "Empty retained")
            if not args.private_dir:
                continue
            cells = originals[source][row["sheet"]]
            kind, value = cells.get(row["cell"], ("empty", None))
            check(kind == row["state"], ("Source type", row["cell"]))
            column, r = re.fullmatch(r"([A-Z]+)(\d+)", row["cell"]).groups()
            check(
                str(cells["A" + r][1]).replace(":", "") == row["observation"],
                "Original observation binding",
            )
            if source == "quarterly":
                header = cells[column + "1"][1]
                m = re.fullmatch(r"ROUTPUT(\d\d)Q([1-4])", header)
                year = int(m[1]) + (1900 if int(m[1]) >= 65 else 2000)
                check(f"{year}Q{m[2]}" == row["vintage"], "Original vintage binding")
            else:
                check(
                    str(
                        cells[column + str(manifest["releases_header_row"])][1]
                    ).casefold()
                    == "first",
                    "Original release binding",
                )
            if kind == "numeric":
                expected = Decimal(value)
                allowance = max(Decimal("1e-12"), abs(expected) * Decimal("2e-16"))
                check(
                    abs(Decimal(row["value"]) - expected) <= allowance,
                    ("Original numeric representation", row["cell"]),
                )
            else:
                check((value or "") == row["value"], "Original missing token")
        counts[filename] = len(rows)
    catalog = json.loads(
        (ROOT / "data/vintage_catalog.json").read_text(encoding="utf-8")
    )
    check(len(catalog) == manifest["selected_vintages"], "Vintage catalog complete")
    if args.private_dir:
        cells = originals["quarterly"]["ROUTPUT"]
        for record in catalog:
            check(
                cells[record["header_cell"]][1] == record["source_header"],
                "Source catalog header",
            )
            col = re.match(r"[A-Z]+", record["header_cell"])[0]
            present = []
            for coordinate, (kind, value) in cells.items():
                if kind == "numeric" and re.fullmatch(col + r"\d+", coordinate):
                    r = re.search(r"\d+", coordinate)[0]
                    rawdate = cells["A" + r][1]
                    m = re.fullmatch(r"(\d{4}):Q([1-4])", str(rawdate))
                    check(m is not None, "Source date for last observation")
                    present.append((int(m[1]), int(m[2])))
            year, q = max(present)
            check(
                f"{year}Q{q}" == record["last_observation"],
                "Independent last-observation scan",
            )
    result = dict(
        status="PASS",
        mode=(
            "PRIVATE_ORIGINALS_INDEPENDENT_OOXML_PASS"
            if args.private_dir
            else "PUBLIC_HASH_SCHEMA_ONLY"
        ),
        checks=checks,
        selected_cells=sum(counts.values()),
        vintages=len(catalog),
        limits="Source-defined quarterly information sets; no exact-day availability or final-truth claim",
    )
    if args.private_dir:
        (ROOT / "data/source_verification.json").write_bytes(
            (json.dumps(result, indent=2) + "\n").encode()
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
