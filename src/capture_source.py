"""Capture two published workbooks once, after the executable protocol is committed."""

from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import subprocess
import requests

ROOT = Path(__file__).resolve().parents[1]
URLS = {
    "quarterly": "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/real-time-data/data-files/xlsx/ROUTPUTQvQd.xlsx?sc_lang=en&hash=34FA1C6BF0007996E1885C8C32E3BEF9",
    "releases": "https://www.philadelphiafed.org/-/media/FRBP/Assets/Surveys-And-Data/real-time-data/data-files/xlsx/routput_first_second_third.xlsx?sc_lang=en&hash=AB8BB59BBBF6840DE1448851E90D7A80",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, required=True)
    args = parser.parse_args()
    assert not (
        ROOT / "data/source_manifest.json"
    ).exists(), "Never replace a source snapshot."
    committed = subprocess.check_output(["git", "show", "HEAD:PROTOCOL.md"], cwd=ROOT)
    assert (
        committed == (ROOT / "PROTOCOL.md").read_bytes()
    ), "Commit the current protocol first."
    args.private_dir.mkdir(exist_ok=False)
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    receipts = []
    for name, url in URLS.items():
        start = datetime.now(timezone.utc).isoformat()
        response = requests.get(url, timeout=(20, 90))
        end = datetime.now(timezone.utc).isoformat()
        filename = name + ".xlsx"
        (args.private_dir / filename).write_bytes(response.content)
        receipt = dict(
            name=name,
            file=filename,
            url=url,
            start_utc=start,
            end_utc=end,
            http_status=response.status_code,
            bytes=len(response.content),
            sha256=hashlib.sha256(response.content).hexdigest(),
            headers={
                k: v
                for k, v in response.headers.items()
                if k.lower()
                in ["content-type", "content-length", "last-modified", "etag"]
            },
            pre_collection_commit=head,
        )
        receipts.append(receipt)
        (args.private_dir / "requests.json").write_bytes(
            (json.dumps(receipts, indent=2) + "\n").encode()
        )
        response.raise_for_status()
    print(
        json.dumps(
            dict(
                status="CAPTURED_PRIVATE_NOT_YET_RECONCILED",
                files=[
                    dict(name=r["name"], bytes=r["bytes"], sha256=r["sha256"])
                    for r in receipts
                ],
            )
        )
    )


if __name__ == "__main__":
    main()
