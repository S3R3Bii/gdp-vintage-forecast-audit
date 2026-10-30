"""Retain every candidate and control contrast; select only on development data."""

from pathlib import Path
from fractions import Fraction
from datetime import datetime, timezone
from collections import defaultdict
import argparse, csv, hashlib, json, subprocess
from model import (
    CONFIGS,
    REGIMES,
    quarter,
    label,
    forecast,
    metrics,
    choose,
    exact,
    display,
)

ROOT = Path(__file__).resolve().parents[1]


def read_csv(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path, value):
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def write_csv(path, rows):
    assert rows
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def frozen_proof():
    path = ROOT / "results/frozen_dependencies.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    for name, digest in frozen["sha256"].items():
        content = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == digest, (
            "Frozen dependency changed",
            name,
        )
    commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", "results/frozen_dependencies.json"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert commit
    assert subprocess.check_output(
        ["git", "show", commit + ":results/frozen_dependencies.json"], cwd=ROOT
    ) == path.read_bytes().replace(b"\r\n", b"\n")
    for name, digest in frozen["sha256"].items():
        blob = subprocess.check_output(["git", "show", commit + ":" + name], cwd=ROOT)
        assert hashlib.sha256(blob).hexdigest() == digest, ("Freeze commit blob", name)
    return commit, len(frozen["sha256"])


def predictions(stage):
    origins = [
        r
        for r in read_csv("results/origin_eligibility.csv")
        if r["split"] == stage and r["eligible"] == "True"
    ]
    histories = defaultdict(list)
    for r in read_csv("results/growth_inputs.csv"):
        if r["split"] == stage and r["state"] == "available":
            histories[(r["target"], r["input_kind"])].append(
                (r["observation"], Fraction(r["growth_exact"]))
            )
    output = []
    for row in origins:
        for input_kind, target_kind in REGIMES:
            history = [g for _, g in sorted(histories[(row["target"], input_kind)])]
            actual = Fraction(row[target_kind + "_target"])
            for index, config in enumerate(CONFIGS):
                estimate = forecast(history, config)
                error = estimate - actual
                output.append(
                    dict(
                        target=row["target"],
                        split=stage,
                        input_kind=input_kind,
                        target_kind=target_kind,
                        regime=input_kind + "__" + target_kind,
                        config=config,
                        config_index=index,
                        estimate_exact=exact(estimate),
                        estimate_pp=display(estimate),
                        actual_exact=exact(actual),
                        actual_pp=display(actual),
                        absolute_loss_exact=exact(abs(error)),
                        squared_loss_exact=exact(error * error),
                    )
                )
    return output


def metric_rows(rows, stage):
    grouped = defaultdict(list)
    for row in rows:
        keys = [("overall", "all")]
        if stage == "held":
            year = int(row["target"][:4])
            era = (
                "2014-2019"
                if year <= 2019
                else "2020-2021" if year <= 2021 else "2022-2024"
            )
            keys.extend([("year", str(year)), ("era", era)])
        for group, level in keys:
            grouped[
                (row["regime"], row["config_index"], row["config"], group, level)
            ].append(row)
    output = []
    for (regime, index, config, group, level), items in sorted(grouped.items()):
        score = metrics(
            [Fraction(r["estimate_exact"]) for r in items],
            [Fraction(r["actual_exact"]) for r in items],
        )
        output.append(
            dict(
                regime=regime,
                config_index=index,
                config=config,
                group=group,
                level=level,
                **score
            )
        )
    return output


def ranking_rows(scores):
    result = []
    for input_kind, target_kind in REGIMES:
        regime = input_kind + "__" + target_kind
        items = sorted(
            [r for r in scores if r["regime"] == regime and r["group"] == "overall"],
            key=lambda r: (Fraction(r["mae_exact"]), r["config_index"]),
        )
        for position, row in enumerate(items, 1):
            rank = 1 + sum(
                Fraction(other["mae_exact"]) < Fraction(row["mae_exact"])
                for other in items
            )
            result.append(
                dict(
                    regime=regime,
                    config=row["config"],
                    config_index=row["config_index"],
                    rank=rank,
                    ordered_position=position,
                    mae=row["mae"],
                    mae_exact=row["mae_exact"],
                )
            )
    return result


def contrasts(rows, scores):
    overall = {(r["regime"], r["config"]): r for r in scores if r["group"] == "overall"}
    output = []
    for axis, fixed_values in [
        ("target", ["real_time", "revised"]),
        ("input", ["early", "later"]),
    ]:
        for constant in fixed_values:
            before = (
                (constant + "__early")
                if axis == "target"
                else ("real_time__" + constant)
            )
            after = (
                (constant + "__later") if axis == "target" else ("revised__" + constant)
            )
            for config in CONFIGS:
                a, b = overall[(before, config)], overall[(after, config)]
                delta = Fraction(b["mae_exact"]) - Fraction(a["mae_exact"])
                output.append(
                    dict(
                        changed_axis=axis,
                        constant=constant,
                        config=config,
                        before_regime=before,
                        after_regime=after,
                        mae_before=a["mae"],
                        mae_after=b["mae"],
                        mean_paired_absolute_loss_change=display(delta),
                        change_exact=exact(delta),
                        n=a["n"],
                    )
                )
    pairwise = []
    for input_kind, _ in [REGIMES[0], REGIMES[2]]:
        for i, first in enumerate(CONFIGS):
            for second in CONFIGS[i + 1 :]:
                signs = []
                for target_kind in ["early", "later"]:
                    regime = input_kind + "__" + target_kind
                    difference = Fraction(
                        overall[(regime, first)]["mae_exact"]
                    ) - Fraction(overall[(regime, second)]["mae_exact"])
                    signs.append((difference > 0) - (difference < 0))
                pairwise.append(
                    dict(
                        input_kind=input_kind,
                        first=first,
                        second=second,
                        early_order=signs[0],
                        later_order=signs[1],
                        strict_reversal=signs[0] * signs[1] < 0,
                        tie_transition=(signs[0] == 0) != (signs[1] == 0),
                    )
                )
    return output, pairwise


def run(stage, output_dir):
    gate = json.loads((ROOT / "results/source_gate.json").read_text(encoding="utf-8"))
    assert gate["status"] == "PASS_AT_STORED_SOURCE_PRECISION"
    freeze_commit = None
    if stage == "held":
        freeze_commit, _ = frozen_proof()
    rows = predictions(stage)
    scores = metric_rows(rows, stage)
    write_csv(output_dir / (stage + "_predictions.csv"), rows)
    write_json(output_dir / (stage + "_metrics.json"), scores)
    write_csv(output_dir / (stage + "_rankings.csv"), ranking_rows(scores))
    controlled, pairwise = contrasts(rows, scores)
    write_csv(output_dir / (stage + "_contrasts.csv"), controlled)
    write_csv(output_dir / (stage + "_pairwise_orders.csv"), pairwise)
    if stage == "development":
        selection = []
        for input_kind, target_kind in REGIMES:
            regime = input_kind + "__" + target_kind
            candidates = sorted(
                [r for r in scores if r["regime"] == regime],
                key=lambda r: r["config_index"],
            )
            chosen = choose(candidates)
            selection.append(
                dict(
                    regime=regime,
                    config=CONFIGS[chosen],
                    config_index=chosen,
                    criterion="Exact development MAE; lower candidate index breaks ties",
                    mae_exact=candidates[chosen]["mae_exact"],
                    development_n=candidates[chosen]["n"],
                )
            )
        write_json(output_dir / "selection.json", selection)
    else:
        selection = json.loads(
            (ROOT / "results/selection.json").read_text(encoding="utf-8")
        )
        chosen = []
        for s in selection:
            for row in scores:
                if row["regime"] == s["regime"] and row["config"] == s["config"]:
                    chosen.append(row)
        write_json(output_dir / "selected_held_metrics.json", chosen)
        if (
            output_dir.resolve() == (ROOT / "results").resolve()
            and not (output_dir / "held_execution.json").exists()
        ):
            write_json(
                output_dir / "held_execution.json",
                dict(
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    freeze_commit=freeze_commit,
                    state="FROZEN_SELECTION_EVALUATED",
                    scope="Retrospective historical information-set comparison; source precision correction predates any model score",
                ),
            )
    print(
        json.dumps(
            dict(
                status="COMPUTED_NOT_YET_INDEPENDENTLY_VERIFIED",
                stage=stage,
                predictions=len(rows),
                metrics=len(scores),
                overall=[
                    {k: r[k] for k in ["regime", "config", "n", "mae", "rmse"]}
                    for r in scores
                    if r["group"] == "overall"
                ],
            )
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["development", "held"])
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    args = parser.parse_args()
    args.output_dir.mkdir(exist_ok=True)
    run(args.stage, args.output_dir)


if __name__ == "__main__":
    main()
