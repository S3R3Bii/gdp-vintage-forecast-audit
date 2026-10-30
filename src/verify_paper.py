"""Rebuild paper displays independently from exact prediction exports."""

import csv, hashlib, json, unicodedata
from decimal import Decimal, localcontext
from fractions import Fraction as Q
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


def read(p):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def csvrows(p):
    return list(csv.DictReader((ROOT / p).open(encoding="utf-8", newline="")))


def main():
    data = {
        s: csvrows("results/" + s + "_predictions.csv") for s in ["development", "held"]
    }
    regimes = [
        "real_time__early",
        "real_time__later",
        "revised__early",
        "revised__later",
    ]
    configs = ["zero", "mean20", "blend25", "blend50", "blend75", "last_growth"]
    expected = {}

    def number(stage, regime, config, group, level, metric):
        rows = [
            r for r in data[stage] if r["regime"] == regime and r["config"] == config
        ]
        if group == "era":
            rows = [
                r
                for r in rows
                if (
                    "2014-2019"
                    if int(r["target"][:4]) <= 2019
                    else "2020-2021" if int(r["target"][:4]) <= 2021 else "2022-2024"
                )
                == level
            ]
        loss = [abs(Q(r["estimate_exact"]) - Q(r["actual_exact"])) for r in rows]
        n = len(loss)
        mae = sum(loss, Q(0)) / n
        mse = sum((x * x for x in loss), Q(0)) / n
        with localcontext() as c:
            c.prec = 60
            value = (
                Decimal(mae.numerator) / Decimal(mae.denominator)
                if metric == "mae"
                else (Decimal(mse.numerator) / Decimal(mse.denominator)).sqrt()
            )
            return format(value, ".3f")

    def add(stage, regime, config, metric, page, group="overall", level="all"):
        key = "|".join([stage, regime, config, group, level, metric, str(page)])
        expected[key] = dict(
            text=number(stage, regime, config, group, level, metric), page=page
        )

    for stage, page in [("development", 2), ("held", 3)]:
        for config in configs:
            for regime in regimes:
                add(stage, regime, config, "mae", page)
    for era in ["2014-2019", "2020-2021", "2022-2024"]:
        for config in ["mean20", "blend50"]:
            for metric in ["mae", "rmse"]:
                add("held", "real_time__early", config, metric, 4, "era", era)
    for regime, config in [
        ("real_time__early", "blend50"),
        ("real_time__early", "mean20"),
        ("real_time__later", "blend50"),
        ("real_time__later", "mean20"),
    ]:
        add("held", regime, config, "mae", 0)
    for regime in regimes:
        add("held", regime, "blend50", "rmse", 3)
    assert len(expected) == 68 and read("paper/TABLE_VALUES.json") == expected
    reader = PdfReader(ROOT / "paper/manuscript.pdf")
    assert len(reader.pages) == 6
    texts = [
        " ".join(unicodedata.normalize("NFKC", p.extract_text()).split())
        for p in reader.pages
    ]
    joined = " ".join(texts)
    checks = 0
    for key, r in expected.items():
        assert r["text"] in texts[r["page"]], key
        checks += 2
    for regime, c in [
        ("revised__early", "blend50"),
        ("revised__later", "blend50"),
        ("real_time__early", "zero"),
        ("revised__early", "blend75"),
        ("revised__later", "zero"),
        ("revised__later", "blend75"),
    ]:
        assert number("held", regime, c, "overall", "all", "mae") in texts[3]
        checks += 1

    def exact_mae(regime, c):
        a = [r for r in data["held"] if r["regime"] == regime and r["config"] == c]
        return sum(
            (abs(Q(r["estimate_exact"]) - Q(r["actual_exact"])) for r in a), Q(0)
        ) / len(a)

    for c in ["blend50", "mean20"]:
        delta = exact_mae("real_time__later", c) - exact_mae("real_time__early", c)
        assert format(float(delta), ".3f") in texts[3]
        checks += 1
    for kind, wanted in [("real_time", 0), ("revised", 1)]:
        count = 0
        for i, a in enumerate(configs):
            for b in configs[i + 1 :]:
                before = exact_mae(kind + "__early", a) - exact_mae(kind + "__early", b)
                after = exact_mae(kind + "__later", a) - exact_mae(kind + "__later", b)
                count += before * after < 0
        assert count == wanted
        checks += 1
    for phrase in [
        "does not beat",
        "corrected source-interpretation",
        "exact-day historical access is unknown",
        "all 98 then agreed",
        "twenty-six dependency hashes",
        "5dbc870",
        "one reversal among fifteen pairs",
        "no target-induced tie transitions",
        "no extreme quarter is removed",
        "significance result",
        "twenty-three synthetic fixtures",
        "63,116 CSV fields",
        "five JSON documents",
        "Formal-publication projects",
        "45,528 checks",
        "22,320 development",
        "22,565 held",
        "no investment return",
    ]:
        assert phrase.casefold() in joined.casefold(), phrase
        checks += 1
    source = csvrows("data/levels.csv") + csvrows("data/first_release.csv")
    assert len(source) == 2323 and len(read("data/vintage_catalog.json")) == 102
    checks += 2
    assert len(data["development"]) == 1152 and len(data["held"]) == 1008
    checks += 2
    scope = read("paper/PUBLICATION_SCOPE.json")
    assert (
        scope["scope"] == "independent-program-study"
        and scope["formal_submission_target"] is False
        and len(scope["public_files"]) == 5
    )
    for name, digest in scope["public_files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
        checks += 1
    assert all(len(t) > 900 for t in texts) and not reader.get_fields()
    root = reader.trailer["/Root"]
    assert "/AA" not in root and "/JavaScript" not in str(root.get("/Names", ""))
    if root.get("/OpenAction"):
        action = root["/OpenAction"].get_object()
        assert isinstance(action, list) and str(action[1]) == "/Fit"
    for page in reader.pages:
        assert "/AA" not in page
        box = page.mediabox
        for ref in page.get("/Annots", []):
            a = ref.get_object()
            assert "/AA" not in a
            action = a.get("/A")
            if action:
                assert action.get_object().get("/S") == "/URI"
            rect = a.get("/Rect")
            if rect:
                assert (
                    float(rect[0]) >= float(box.left) - 1
                    and float(rect[1]) >= float(box.bottom) - 1
                    and float(rect[2]) <= float(box.right) + 1
                    and float(rect[3]) <= float(box.top) + 1
                )
            checks += 1
    result = dict(
        status="PASS",
        checks=checks,
        pages=6,
        numeric_display_values=68,
        manuscript_sha256=hashlib.sha256(
            (ROOT / "paper/manuscript.pdf").read_bytes()
        ).hexdigest(),
        scope="Independent observation-level display/ranking arithmetic,claims,hashes and bounded PDF actions;visual review separate",
    )
    (ROOT / "results/paper_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
