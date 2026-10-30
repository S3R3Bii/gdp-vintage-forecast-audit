"""Check reviewed canonical Git bytes and bounded publication exclusions in CI."""

import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    def blob(name):
        return subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT)

    audit = json.loads(blob("PUBLICATION_CHECK.json"))
    assert audit["status"] == "PASS_BOUNDED_SCAN" and not audit["findings"]
    names = set(
        subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=ROOT, text=True
        ).splitlines()
    )
    assert set(audit["sha256"]) == names - {
        "PUBLICATION_CHECK.json",
        "RELEASE_CHECK.json",
    }
    scope = json.loads(blob("paper/PUBLICATION_SCOPE.json"))
    assert (
        scope["scope"] == "independent-program-study"
        and scope["formal_submission_target"] is False
    )
    patterns = [
        r"[Cc]:\\[Uu]sers\\",
        r"/[Uu]sers/",
        r"/[Hh]ome/",
        r"-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----",
        r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"github_pat_[A-Za-z0-9_]+",
        r"X-Amz-(?:Credential|Security-Token|Signature)=",
    ]
    for name, digest in audit["sha256"].items():
        data = blob(name)
        assert hashlib.sha256(data).hexdigest() == digest, name
        forbidden = (
            name.startswith(("private/", "data/raw/", "data/cache/"))
            or Path(name).suffix.lower() in [".tex", ".zip", ".env", ".joblib"]
            or "manuscript" in name.lower()
        )
        assert not forbidden or name in scope["public_files"], name
        if name in scope["public_files"]:
            assert hashlib.sha256(data).hexdigest() == scope["public_files"][name]
        if Path(name).suffix.lower() in [".png", ".pdf", ".jpg", ".jpeg"]:
            continue
        if name.endswith(".gz"):
            data = gzip.decompress(data)
        text = data.decode("utf-8-sig")
        assert not any(re.search(pattern, text) for pattern in patterns), name
    print(
        json.dumps(
            {
                "status": "PASS",
                "canonical_blobs_checked": len(audit["sha256"]),
                "scope": "Exact reviewed Git blobs, approved independent paper hashes, path exclusions and high-confidence text patterns. No claim of universal privacy or security detection.",
            }
        )
    )


if __name__ == "__main__":
    main()
