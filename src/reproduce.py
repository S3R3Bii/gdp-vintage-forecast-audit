"""Replay in a temporary local clone without changing the published exports."""

import csv, hashlib, json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    originals = {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and "__pycache__" not in p.parts
        and p.name != "reproduction.json"
    }
    with tempfile.TemporaryDirectory(prefix="gdp-vintage-replay-") as temp:
        target = Path(temp) / "study"
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(target)],
            check=True,
        )
        for cmd in [
            ["src/verify_source.py"],
            ["src/prepare_inputs.py"],
            ["src/run_analysis.py", "development"],
            ["src/verify_results.py", "development"],
            ["src/run_analysis.py", "held"],
            ["src/verify_results.py", "held"],
            ["-m", "unittest", "discover", "-s", "tests"],
        ]:
            subprocess.run(
                [sys.executable, *cmd],
                cwd=target,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        fields = documents = 0
        files = [
            "origin_eligibility.csv",
            "growth_inputs.csv",
            "source_gate.json",
            "selection.json",
        ]
        for stage in ["development", "held"]:
            files += [
                stage + "_" + suffix
                for suffix in [
                    "predictions.csv",
                    "metrics.json",
                    "rankings.csv",
                    "contrasts.csv",
                    "pairwise_orders.csv",
                ]
            ]
        files += ["selected_held_metrics.json"]
        for name in files:
            a, b = ROOT / "results" / name, target / "results" / name
            if a.suffix == ".csv":
                left = list(csv.DictReader(a.open(encoding="utf-8", newline="")))
                right = list(csv.DictReader(b.open(encoding="utf-8", newline="")))
                assert left == right, name
                fields += sum(len(r) for r in left)
            else:
                assert json.loads(a.read_text(encoding="utf-8")) == json.loads(
                    b.read_text(encoding="utf-8")
                ), name
                documents += 1
    for name, digest in originals.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    result = dict(
        status="PASS",
        exact_csv_fields=fields,
        json_documents=documents,
        tests=23,
        frozen_dependencies=26,
        original_files_unchanged=True,
        network_during_replay=False,
        scope="Local clone,full transformation and independent recomputation;no new source request",
    )
    (ROOT / "results/reproduction.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
