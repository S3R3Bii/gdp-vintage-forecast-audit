"""Enforce source timing, preserve every origin and check the separate First series."""

from pathlib import Path
from fractions import Fraction
import argparse, csv, json
from model import quarter, label, growth, fixed, display, exact

ROOT = Path(__file__).resolve().parents[1]


def read_csv(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build():
    levels = {(r["vintage"], r["observation"]): r for r in read_csv("data/levels.csv")}
    first = {r["observation"]: r for r in read_csv("data/first_release.csv")}
    catalog = {
        r["vintage"]: r
        for r in json.loads(
            (ROOT / "data/vintage_catalog.json").read_text(encoding="utf-8")
        )
    }
    origins = []
    histories = []
    mismatches = []
    compared = 0
    for q in range(quarter("2000Q1"), quarter("2024Q4") + 1):
        key = label(q)
        reasons = []
        split = (
            "development"
            if q <= quarter("2011Q4")
            else "separation" if q <= quarter("2013Q4") else "held"
        )
        exceptional = key in ["2018Q4", "2019Q1"]
        if key == "2018Q4":
            reasons.append("documented_target_timing_exception")
        if key == "2019Q1":
            reasons.append("documented_origin_timing_exception")
        if catalog[key]["last_observation"] != label(q - 1):
            reasons.append("origin_latest_observation_not_previous_quarter")
        if catalog[label(q + 1)]["last_observation"] != key:
            reasons.append("early_target_vintage_latest_observation_mismatch")

        def level(v, t):
            row = levels[(v, label(t))]
            if row["state"] != "numeric":
                return None
            value = Fraction(row["value"])
            return value if value > 0 else None

        per_kind = {}
        for kind, v in [("real_time", key), ("revised", "2026Q3")]:
            values = []
            for t in range(q - 20, q):
                current, previous = level(v, t), level(v, t - 1)
                g = (
                    fixed(growth(current, previous))
                    if current is not None and previous is not None
                    else None
                )
                values.append(g)
                histories.append(
                    dict(
                        target=key,
                        split=split,
                        input_kind=kind,
                        observation=label(t),
                        source_vintage=v,
                        state=(
                            "available"
                            if g is not None
                            else "missing_or_nonpositive_level"
                        ),
                        growth_exact=exact(g) if g is not None else "",
                        growth_pp=display(g) if g is not None else "",
                    )
                )
            if any(g is None for g in values):
                reasons.append(kind + "_history_unavailable")
            per_kind[kind] = values
        targets = {}
        for kind, v in [("early", label(q + 1)), ("later", "2026Q3")]:
            a, b = level(v, q), level(v, q - 1)
            targets[kind] = growth(a, b) if a is not None and b is not None else None
            if targets[kind] is None:
                reasons.append(kind + "_target_unavailable")
        independent = (
            Fraction(first[key]["value"]) if first[key]["state"] == "numeric" else None
        )
        raw_difference = (
            targets["early"] - independent
            if targets["early"] is not None and independent is not None
            else None
        )
        if independent is not None:
            assert (
                fixed(independent, 4) == independent
            ), "Stored First precision changed"
        difference = (
            fixed(targets["early"], 4) - independent
            if raw_difference is not None
            else None
        )
        if independent is None:
            reasons.append("separate_first_series_unavailable")
        if not exceptional and difference is not None:
            compared += 1
            if abs(difference) > Fraction(1, 10**8):
                mismatches.append(
                    dict(
                        target=key,
                        absolute_difference_pp=display(abs(difference)),
                        difference_exact=exact(difference),
                    )
                )
        row = dict(
            target=key,
            split=split,
            eligible=not reasons,
            reasons=";".join(reasons),
            input_vintage=key,
            origin_last_observation=catalog[key]["last_observation"],
            history_start=label(q - 21),
            history_end=label(q - 1),
            early_target_vintage=label(q + 1),
            later_vintage="2026Q3",
            first_series_raw_difference_pp=(
                display(raw_difference) if raw_difference is not None else ""
            ),
            first_series_stored_precision_difference_pp=(
                display(difference) if difference is not None else ""
            ),
        )
        for kind, value in targets.items():
            row[kind + "_target"] = exact(fixed(value)) if value is not None else ""
        for kind, values in per_kind.items():
            available = all(g is not None for g in values)
            row[kind + "_mean20"] = (
                exact(sum(values, Fraction(0)) / 20) if available else ""
            )
            row[kind + "_last"] = exact(values[-1]) if available else ""
        origins.append(row)
    return (
        origins,
        histories,
        dict(
            status=(
                "PASS_AT_STORED_SOURCE_PRECISION"
                if not mismatches
                else "FAIL_FIRST_RELEASE_RECONCILIATION"
            ),
            first_series_compared=compared,
            comparison="Exact four-decimal source representation before the model twelve-decimal quantization; original raw differences retained",
            tolerance_pp="0.00000001",
            mismatches=mismatches,
            scope="Source cross-check before model selection; current files are not proof of exact-day historical access",
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)
    origins, histories, gate = build()
    write_csv(args.output_dir / "origin_eligibility.csv", origins)
    write_csv(args.output_dir / "growth_inputs.csv", histories)
    (args.output_dir / "source_gate.json").write_bytes(
        (json.dumps(gate, indent=2) + "\n").encode()
    )
    print(
        json.dumps(
            dict(
                source_gate=gate,
                eligible_by_split={
                    s: sum(r["eligible"] for r in origins if r["split"] == s)
                    for s in ["development", "separation", "held"]
                },
                excluded=[
                    dict(target=r["target"], reasons=r["reasons"])
                    for r in origins
                    if not r["eligible"]
                ],
            )
        )
    )
    assert (
        gate["status"] == "PASS_AT_STORED_SOURCE_PRECISION"
    ), "Source disagreement: preserve this failure and investigate without relaxing tolerance."


if __name__ == "__main__":
    main()
