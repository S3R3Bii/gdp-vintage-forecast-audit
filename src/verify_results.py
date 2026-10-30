"""Rebuild source joins and all forecast scores using independent SQL/Decimal code."""

from pathlib import Path
from collections import defaultdict
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from fractions import Fraction
import argparse, csv, hashlib, json, re, sqlite3, subprocess

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["zero", "mean20", "blend25", "blend50", "blend75", "last_growth"]
WEIGHTS = [
    None,
    Fraction(0),
    Fraction(1, 4),
    Fraction(1, 2),
    Fraction(3, 4),
    Fraction(1),
]
REGIMES = [
    ("real_time", "early"),
    ("real_time", "later"),
    ("revised", "early"),
    ("revised", "later"),
]


def read_csv(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def index(text):
    assert re.fullmatch(r"\d{4}Q[1-4]", text)
    return int(text[:4]) * 4 + int(text[-1]) - 1


def quarter_text(value):
    year, part = divmod(value, 4)
    return str(year) + "Q" + str(part + 1)


def ftext(value):
    return str(value.numerator) + "/" + str(value.denominator)


def rendered(value, places=12):
    value = Fraction(value)
    with localcontext() as context:
        context.prec = 160
        context.rounding = ROUND_HALF_EVEN
        number = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
            Decimal(1).scaleb(-places)
        )
        if not number:
            number = Decimal(0)
        return format(number, "." + str(places) + "f")


def raw_growth(a, b):
    with localcontext() as context:
        context.prec = 160
        return Decimal(100) * ((Decimal(a) / Decimal(b)) ** 4 - 1)


def growth(a, b):
    with localcontext() as context:
        context.prec = 160
        context.rounding = ROUND_HALF_EVEN
        return Fraction(raw_growth(a, b).quantize(Decimal("0.000000000001")))


def scores(items):
    errors = [r["prediction"] - r["actual"] for r in items]
    n = len(items)
    mae = sum((abs(e) for e in errors), Fraction(0)) / n
    mse = sum((e * e for e in errors), Fraction(0)) / n
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        rmse = format(
            (Decimal(mse.numerator) / Decimal(mse.denominator)).sqrt(), ".12f"
        )
    return dict(
        n=n,
        mae=rendered(mae),
        mse=rendered(mse),
        rmse=rmse,
        mae_exact=ftext(mae),
        mse_exact=ftext(mse),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["development", "held"])
    args = parser.parse_args()
    checks = 0

    def check(condition, message):
        nonlocal checks
        assert condition, message
        checks += 1

    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE levels(v INTEGER,o INTEGER,x TEXT,state TEXT,PRIMARY KEY(v,o))"
    )
    db.executemany(
        "INSERT INTO levels VALUES(?,?,?,?)",
        [
            (index(r["vintage"]), index(r["observation"]), r["value"], r["state"])
            for r in read_csv("data/levels.csv")
        ],
    )
    catalog = {
        index(r["vintage"]): index(r["last_observation"])
        for r in read_json("data/vintage_catalog.json")
    }
    first = {index(r["observation"]): r for r in read_csv("data/first_release.csv")}
    inputs = {}
    expected_origins = {}
    expected_histories = {}
    first_compared = 0
    for q in range(8000, 8100):
        name = quarter_text(q)
        split = "development" if q < 8048 else "separation" if q < 8056 else "held"
        reasons = []
        if name == "2018Q4":
            reasons.append("documented_target_timing_exception")
        if name == "2019Q1":
            reasons.append("documented_origin_timing_exception")
        if catalog[q] != q - 1:
            reasons.append("origin_latest_observation_not_previous_quarter")
        if catalog[q + 1] != q:
            reasons.append("early_target_vintage_latest_observation_mismatch")
        histories = {}
        for kind, v in [("real_time", q), ("revised", 8106)]:
            # The same date predicate is used in both regimes; only the vintage changes.
            joined = db.execute(
                "SELECT a.o,a.x,a.state,b.x,b.state FROM levels a LEFT JOIN levels b ON a.v=b.v AND a.o=b.o+1 WHERE a.v=? AND a.o BETWEEN ? AND ? ORDER BY a.o",
                (v, q - 20, q - 1),
            ).fetchall()
            check(len(joined) == 20, ("History row count", name, kind))
            values = []
            for obs, a, sa, b, sb in joined:
                available = (
                    sa == "numeric"
                    and sb == "numeric"
                    and Decimal(a) > 0
                    and Decimal(b) > 0
                )
                value = growth(a, b) if available else None
                values.append(value)
                expected_histories[(name, kind, quarter_text(obs))] = dict(
                    target=name,
                    split=split,
                    input_kind=kind,
                    observation=quarter_text(obs),
                    source_vintage=quarter_text(v),
                    state="available" if available else "missing_or_nonpositive_level",
                    growth_exact=ftext(value) if available else "",
                    growth_pp=rendered(value) if available else "",
                )
            if any(value is None for value in values):
                reasons.append(kind + "_history_unavailable")
            histories[kind] = values
        targets = {}
        unquantized_early = None
        for kind, v in [("early", q + 1), ("later", 8106)]:
            rows = db.execute(
                "SELECT o,x,state FROM levels WHERE v=? AND o IN(?,?) ORDER BY o",
                (v, q - 1, q),
            ).fetchall()
            check(len(rows) == 2, ("Target rows", name, kind))
            if all(r[2] == "numeric" and Decimal(r[1]) > 0 for r in rows):
                targets[kind] = growth(rows[1][1], rows[0][1])
                if kind == "early":
                    unquantized_early = raw_growth(rows[1][1], rows[0][1])
            else:
                targets[kind] = None
                reasons.append(kind + "_target_unavailable")
        independent = (
            Decimal(first[q]["value"]) if first[q]["state"] == "numeric" else None
        )
        if independent is None:
            reasons.append("separate_first_series_unavailable")
        rawdiff = (
            unquantized_early - independent
            if unquantized_early is not None and independent is not None
            else None
        )
        with localcontext() as context:
            context.prec = 160
            context.rounding = ROUND_HALF_EVEN
            difference = (
                unquantized_early.quantize(Decimal(".0001")) - independent
                if rawdiff is not None
                else None
            )
        if independent is not None:
            check(
                independent.quantize(Decimal(".0001")) == independent,
                "Supplied First precision",
            )
        if name not in ["2018Q4", "2019Q1"] and difference is not None:
            check(
                abs(difference) <= Decimal(".00000001"),
                ("Stored-precision source agreement", name),
            )
            first_compared += 1
        expected = dict(
            target=name,
            split=split,
            eligible=str(not reasons),
            reasons=";".join(reasons),
            input_vintage=name,
            origin_last_observation=quarter_text(catalog[q]),
            history_start=quarter_text(q - 21),
            history_end=quarter_text(q - 1),
            early_target_vintage=quarter_text(q + 1),
            later_vintage="2026Q3",
            first_series_raw_difference_pp=(
                rendered(Fraction(rawdiff)) if rawdiff is not None else ""
            ),
            first_series_stored_precision_difference_pp=(
                rendered(Fraction(difference)) if difference is not None else ""
            ),
        )
        for kind, value in targets.items():
            expected[kind + "_target"] = ftext(value) if value is not None else ""
        for kind, values in histories.items():
            available = all(value is not None for value in values)
            expected[kind + "_mean20"] = (
                ftext(sum(values, Fraction(0)) / 20) if available else ""
            )
            expected[kind + "_last"] = ftext(values[-1]) if available else ""
        expected_origins[name] = expected
        inputs[name] = dict(
            split=split, eligible=not reasons, histories=histories, targets=targets
        )
    actual_origins = read_csv("results/origin_eligibility.csv")
    check(len(actual_origins) == 100, "Planned denominator")
    for row in actual_origins:
        check(row == expected_origins[row["target"]], ("Origin lineage", row["target"]))
    actual_histories = read_csv("results/growth_inputs.csv")
    check(len(actual_histories) == 4000, "All planned histories")
    for row in actual_histories:
        check(
            row
            == expected_histories[
                (row["target"], row["input_kind"], row["observation"])
            ],
            ("Growth input", row["target"], row["input_kind"], row["observation"]),
        )
    check(
        read_json("results/source_gate.json")["first_series_compared"]
        == first_compared
        == 98,
        "First comparison count",
    )
    check(
        read_json("results/source_gate.json")["status"]
        == "PASS_AT_STORED_SOURCE_PRECISION",
        "Source gate",
    )
    expected_predictions = {}
    development_predictions = []
    for target, source in inputs.items():
        if not source["eligible"] or source["split"] not in [args.stage, "development"]:
            continue
        for ik, tk in REGIMES:
            history = source["histories"][ik]
            for i, config in enumerate(NAMES):
                prediction = (
                    Fraction(0)
                    if i == 0
                    else (1 - WEIGHTS[i]) * sum(history, Fraction(0)) / 20
                    + WEIGHTS[i] * history[-1]
                )
                actual = source["targets"][tk]
                error = prediction - actual
                record = dict(
                    target=target,
                    split=source["split"],
                    input_kind=ik,
                    target_kind=tk,
                    regime=ik + "__" + tk,
                    config=config,
                    config_index=str(i),
                    estimate_exact=ftext(prediction),
                    estimate_pp=rendered(prediction),
                    actual_exact=ftext(actual),
                    actual_pp=rendered(actual),
                    absolute_loss_exact=ftext(abs(error)),
                    squared_loss_exact=ftext(error * error),
                )
                key = (target, record["regime"], config)
                if source["split"] == "development":
                    development_predictions.append(
                        dict(**record, prediction=prediction, actual=actual)
                    )
                if source["split"] == args.stage:
                    expected_predictions[key] = dict(
                        **record, prediction=prediction, actual=actual
                    )
    actual_predictions = read_csv("results/" + args.stage + "_predictions.csv")
    check(
        len(actual_predictions) == len(expected_predictions), "Prediction denominator"
    )
    seen = set()
    scientific = []
    for row in actual_predictions:
        key = (row["target"], row["regime"], row["config"])
        check(key not in seen, "Duplicate prediction")
        seen.add(key)
        expected = expected_predictions[key]
        for field, value in row.items():
            check(value == expected[field], ("Prediction field", key, field))
        scientific.append(expected)
    grouped = defaultdict(list)
    for row in scientific:
        groups = [("overall", "all")]
        if args.stage == "held":
            year = int(row["target"][:4])
            era = (
                "2014-2019"
                if year < 2020
                else "2020-2021" if year < 2022 else "2022-2024"
            )
            groups.extend([("year", str(year)), ("era", era)])
        for group, level in groups:
            grouped[(row["regime"], row["config"], group, level)].append(row)
    actual_scores = read_json("results/" + args.stage + "_metrics.json")
    check(len(actual_scores) == len(grouped), "Metric denominator")
    expected_scores = {}
    for row in actual_scores:
        key = (row["regime"], row["config"], row["group"], row["level"])
        score = scores(grouped[key])
        expected_scores[key] = score
        for field, value in score.items():
            check(row[field] == value, ("Metric", key, field))
        check(row["config_index"] == NAMES.index(row["config"]), "Candidate index")
    selection = read_json("results/selection.json")
    check(len(selection) == 4, "All selections")
    for chosen in selection:
        dev = [
            scores(
                [
                    r
                    for r in development_predictions
                    if r["regime"] == chosen["regime"] and r["config"] == config
                ]
            )
            for config in NAMES
        ]
        winner = min(range(6), key=lambda i: (Fraction(dev[i]["mae_exact"]), i))
        check(
            chosen["config_index"] == winner and chosen["config"] == NAMES[winner],
            "Development-only winner",
        )
        check(
            chosen["mae_exact"] == dev[winner]["mae_exact"]
            and chosen["development_n"] == dev[winner]["n"],
            "Selected score",
        )
    ranks = read_csv("results/" + args.stage + "_rankings.csv")
    check(len(ranks) == 24, "All ranks")
    for row in ranks:
        values = [
            expected_scores[(row["regime"], config, "overall", "all")]
            for config in NAMES
        ]
        ordered = sorted(range(6), key=lambda i: (Fraction(values[i]["mae_exact"]), i))
        i = NAMES.index(row["config"])
        rank = 1 + sum(
            Fraction(v["mae_exact"]) < Fraction(values[i]["mae_exact"]) for v in values
        )
        check(
            int(row["rank"]) == rank
            and int(row["ordered_position"]) == ordered.index(i) + 1,
            "Ranking and ties",
        )
        check(
            row["mae_exact"] == values[i]["mae_exact"]
            and row["mae"] == values[i]["mae"],
            "Rank score",
        )
    controls = read_csv("results/" + args.stage + "_contrasts.csv")
    check(len(controls) == 24, "All control contrasts")
    for row in controls:
        before = {
            r["target"]: r
            for r in scientific
            if r["regime"] == row["before_regime"] and r["config"] == row["config"]
        }
        after = {
            r["target"]: r
            for r in scientific
            if r["regime"] == row["after_regime"] and r["config"] == row["config"]
        }
        check(set(before) == set(after), "Matched contrast quarters")
        delta = sum(
            (
                abs(after[t]["prediction"] - after[t]["actual"])
                - abs(before[t]["prediction"] - before[t]["actual"])
                for t in before
            ),
            Fraction(0),
        ) / len(before)
        check(
            row["change_exact"] == ftext(delta)
            and row["mean_paired_absolute_loss_change"] == rendered(delta),
            "Paired exact loss change",
        )
        if row["changed_axis"] == "target":
            for t in before:
                check(
                    before[t]["prediction"] == after[t]["prediction"],
                    "Target-only prediction invariance",
                )
        else:
            for t in before:
                check(
                    before[t]["actual"] == after[t]["actual"],
                    "Input-only target invariance",
                )
    pairs = read_csv("results/" + args.stage + "_pairwise_orders.csv")
    check(len(pairs) == 30, "All pairwise orders")
    for row in pairs:
        signs = []
        for tk in ["early", "later"]:
            a = Fraction(
                expected_scores[
                    (row["input_kind"] + "__" + tk, row["first"], "overall", "all")
                ]["mae_exact"]
            )
            b = Fraction(
                expected_scores[
                    (row["input_kind"] + "__" + tk, row["second"], "overall", "all")
                ]["mae_exact"]
            )
            signs.append((a > b) - (a < b))
        check([int(row["early_order"]), int(row["later_order"])] == signs, "Pair order")
        check(
            row["strict_reversal"] == str(signs[0] * signs[1] < 0)
            and row["tie_transition"] == str((signs[0] == 0) != (signs[1] == 0)),
            "Pair reversal/ties",
        )
    if args.stage == "held":
        selected = read_json("results/selected_held_metrics.json")
        wanted = [
            r
            for r in actual_scores
            if any(
                s["regime"] == r["regime"] and s["config"] == r["config"]
                for s in selection
            )
        ]
        check(selected == wanted, "Selected held/subgroup metrics")
        frozen = read_json("results/frozen_dependencies.json")
        commit = read_json("results/held_execution.json")["freeze_commit"]
        for name, digest in frozen["sha256"].items():
            check(
                hashlib.sha256(
                    (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
                ).hexdigest()
                == digest,
                "Current frozen dependency",
            )
            check(
                hashlib.sha256(
                    subprocess.check_output(
                        ["git", "show", commit + ":" + name], cwd=ROOT
                    )
                ).hexdigest()
                == digest,
                "Original freeze dependency",
            )
    db.close()
    result = dict(
        status="PASS",
        stage=args.stage,
        checks=checks,
        origins_checked=100,
        growth_inputs_checked=4000,
        prediction_rows=len(actual_predictions),
        metric_rows=len(actual_scores),
        source_agreements_at_four_decimals=first_compared,
        independence="Separate SQLite vintage/history joins,160-digit Decimal growth and exact loss/rank arithmetic; no analysis imports",
        limits="Corrected source-precision interpretation; no higher-precision First agreement,exact-day availability or operational benefit claim",
    )
    (ROOT / "results" / ("independent_" + args.stage + ".json")).write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
