"""Check actual SVG rectangles against independent verified result exports."""

import hashlib, json, re, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    read = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))
    held = read("results/held_metrics.json")
    dev = read("results/development_metrics.json")
    expected = {}

    def score(data, regime, config, group="overall", level="all"):
        return float(
            next(
                r
                for r in data
                if r["regime"] == regime
                and r["config"] == config
                and r["group"] == group
                and r["level"] == level
            )["mae"]
        )

    for j, kind in enumerate(["real_time", "revised"]):
        for k, target in enumerate(["early", "later"]):
            for i, c in enumerate(
                ["zero", "mean20", "blend25", "blend50", "blend75", "last_growth"]
            ):
                expected[f"all-{j}-{k}-{i}"] = score(held, kind + "__" + target, c)
    for j, data in enumerate([dev, held]):
        for i, regime in enumerate(
            ["real_time__early", "real_time__later", "revised__early", "revised__later"]
        ):
            expected[f"selected-{j}-{i}"] = score(data, regime, "blend50")
    for j, c in enumerate(["mean20", "blend50"]):
        for i, era in enumerate(["2014-2019", "2020-2021", "2022-2024"]):
            expected[f"era-{j}-{i}"] = score(held, "real_time__early", c, "era", era)
    geometry = read("figures/artist_geometry.json")
    assert len(geometry) == len(expected) == 38
    checks = 0
    trees = {}
    for record in geometry:
        assert record["value"] == expected[record["gid"]]
        checks += 1
        name = record["figure"]
        if name not in trees:
            trees[name] = ET.parse(ROOT / "figures" / (name + ".svg"))
        group = trees[name].find('.//*[@id="' + record["gid"] + '"]')
        assert group is not None
        path = group.find("{http://www.w3.org/2000/svg}path")
        values = [
            float(v)
            for v in re.findall(
                r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?", path.attrib["d"]
            )
        ]
        for a, b in zip(values[:8], [v for point in record["points"] for v in point]):
            assert abs(a - b) < 1e-5
            checks += 1
    for name, digest in read("figures/manifest.json").items():
        assert (
            hashlib.sha256((ROOT / "figures" / name).read_bytes()).hexdigest() == digest
        )
        checks += 1
    result = dict(
        status="PASS",
        checks=checks,
        source_values=38,
        svg_coordinates=304,
        hashes=9,
        scope="Result values,rendered SVG bar coordinates and exact artifact hashes;no operational validation",
    )
    (ROOT / "results/figure_verification.json").write_bytes(
        (json.dumps(result, indent=2) + "\n").encode()
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
