"""Original figures keep all rules, both vintage axes and fixed eras visible."""

import hashlib, json
from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "svg.fonttype": "none",
        "svg.hashsalt": "gdp24-v1",
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def main():
    read = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))
    held = read("results/held_metrics.json")
    dev = read("results/development_metrics.json")
    geometry = []
    hashes = {}
    configs = ["zero", "mean20", "blend25", "blend50", "blend75", "last_growth"]
    labels = ["Zero", "Mean 20", "25% last", "50% last", "75% last", "Last growth"]
    colors = ["#286c87", "#d48a32"]

    def save(fig, name, artists):
        fig.canvas.draw()
        height = fig.get_figheight() * fig.dpi
        for gid, value, patch in artists:
            points = patch.get_transform().transform(patch.get_path().vertices[:4])
            geometry.append(
                dict(
                    figure=name,
                    gid=gid,
                    value=float(value),
                    points=[
                        [float(x * 72 / fig.dpi), float((height - y) * 72 / fig.dpi)]
                        for x, y in points
                    ],
                )
            )
        for ext in ["pdf", "svg", "png"]:
            path = ROOT / "figures" / (name + "." + ext)
            fig.savefig(
                path,
                dpi=160,
                metadata=(
                    {"Creator": "GDP vintage audit,Study24"} if ext == "pdf" else None
                ),
            )
            if ext == "svg":
                path.write_bytes(
                    (
                        "\n".join(
                            line.rstrip()
                            for line in path.read_text(encoding="utf-8").splitlines()
                        )
                        + "\n"
                    ).encode()
                )
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.5, 3.7))
    fig.subplots_adjust(left=0.13, right=0.98, top=0.8, bottom=0.19, wspace=0.33)
    artists = []
    for j, kind in enumerate(["real_time", "revised"]):
        ax = axes[j]
        for k, target in enumerate(["early", "later"]):
            values = [
                float(
                    next(
                        r
                        for r in held
                        if r["regime"] == kind + "__" + target
                        and r["config"] == c
                        and r["group"] == "overall"
                    )["mae"]
                )
                for c in configs
            ]
            bars = ax.barh(
                [i + (k - 0.5) * 0.34 for i in range(6)],
                values,
                height=0.32,
                color=colors[k],
                label="Early target" if k == 0 else "2026Q3 target",
                hatch="" if k == 0 else "//",
            )
            for i, (v, p) in enumerate(zip(values, bars)):
                gid = f"all-{j}-{k}-{i}"
                p.set_gid(gid)
                artists.append((gid, v, p))
        ax.set_yticks(range(6), labels)
        ax.invert_yaxis()
        ax.set_xlim(0, 5)
        ax.set_xlabel("Held MAE (annualized pp)")
        ax.set_title(
            "Origin-vintage inputs" if j == 0 else "2026Q3 inputs: hindsight",
            fontsize=10,
            loc="left",
        )
    fig.legend(
        *axes[1].get_legend_handles_labels(),
        frameon=False,
        fontsize=8,
        ncol=2,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.01),
    )
    fig.suptitle(
        "Mean 20 has the lowest held MAE in every vintage regime",
        x=0.13,
        ha="left",
        fontsize=12,
    )
    save(fig, "all_candidates", artists)
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    fig.subplots_adjust(left=0.1, right=0.98, top=0.8, bottom=0.22)
    artists = []
    regimes = [
        "real_time__early",
        "real_time__later",
        "revised__early",
        "revised__later",
    ]
    for j, (data, label) in enumerate(
        [(dev, "Development: 48 quarters"), (held, "Held: 42 quarters")]
    ):
        values = [
            float(
                next(
                    r
                    for r in data
                    if r["regime"] == regime
                    and r["config"] == "blend50"
                    and r["group"] == "overall"
                )["mae"]
            )
            for regime in regimes
        ]
        bars = ax.bar(
            [i + (j - 0.5) * 0.34 for i in range(4)],
            values,
            width=0.32,
            color=colors[j],
            label=label,
            hatch="" if j == 0 else "//",
        )
        for i, (v, p) in enumerate(zip(values, bars)):
            gid = f"selected-{j}-{i}"
            p.set_gid(gid)
            artists.append((gid, v, p))
    ax.set_xticks(
        range(4),
        ["Origin / early", "Origin / later", "Revised / early", "Revised / later"],
    )
    ax.set_ylim(0, 4.3)
    ax.set_ylabel("MAE (annualized pp)")
    ax.set_title(
        "The development-selected 50% blend does not retain its lead",
        loc="left",
        pad=18,
        fontsize=11,
    )
    fig.legend(
        *ax.get_legend_handles_labels(),
        frameon=False,
        fontsize=8,
        ncol=2,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.01),
    )
    save(fig, "selected_transfer", artists)
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    fig.subplots_adjust(left=0.11, right=0.98, top=0.8, bottom=0.23)
    artists = []
    eras = ["2014-2019", "2020-2021", "2022-2024"]
    for j, config in enumerate(["mean20", "blend50"]):
        values = [
            float(
                next(
                    r
                    for r in held
                    if r["regime"] == "real_time__early"
                    and r["config"] == config
                    and r["group"] == "era"
                    and r["level"] == era
                )["mae"]
            )
            for era in eras
        ]
        bars = ax.bar(
            [i + (j - 0.5) * 0.34 for i in range(3)],
            values,
            width=0.32,
            color=colors[j],
            label="Mean 20" if j == 0 else "Selected 50% blend",
            hatch="" if j == 0 else "//",
        )
        for i, (v, p) in enumerate(zip(values, bars)):
            gid = f"era-{j}-{i}"
            p.set_gid(gid)
            artists.append((gid, v, p))
    ax.set_xticks(range(3), eras)
    ax.set_ylim(0, 15)
    ax.set_ylabel("MAE (annualized pp)")
    ax.set_title(
        "Pandemic quarters remain in the evaluation", loc="left", pad=18, fontsize=12
    )
    ax.legend(frameon=False, fontsize=8)
    save(fig, "era_context", artists)
    (ROOT / "figures/artist_geometry.json").write_bytes(
        (json.dumps(geometry, indent=2) + "\n").encode()
    )
    (ROOT / "figures/manifest.json").write_bytes(
        (json.dumps(hashes, indent=2, sort_keys=True) + "\n").encode()
    )
    print("Original figures,source values", len(geometry))


if __name__ == "__main__":
    main()
