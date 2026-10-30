"""Synthetic arithmetic and information-boundary fixtures, excluded from results."""

import copy
from fractions import Fraction as F
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import model as m
import prepare_inputs as prep


class Arithmetic(unittest.TestCase):
    def test_quarter_rollover(self):
        self.assertEqual(m.label(m.quarter("1999Q4") + 1), "2000Q1")

    def test_invalid_quarter(self):
        for value in ["2000Q0", "2000Q5", "00Q1", "2000:Q1"]:
            with self.assertRaises(ValueError):
                m.quarter(value)

    def test_exact_annualization(self):
        self.assertEqual(m.growth(110, 100), F(4641, 100))

    def test_rebasing_invariance(self):
        self.assertEqual(m.growth(210, 200), m.growth(105, 100))

    def test_nonpositive_levels(self):
        for pair in [(0, 100), (100, 0), (-1, 100)]:
            with self.assertRaises(ValueError):
                m.growth(*pair)

    def test_signed_half_even(self):
        for digits in [4, 12]:
            scale = 10**digits
            for sign in [-1, 1]:
                self.assertEqual(
                    m.fixed(F(sign * 5, 2 * scale), digits), F(sign * 2, scale)
                )
                self.assertEqual(
                    m.fixed(F(sign * 7, 2 * scale), digits), F(sign * 4, scale)
                )

    def test_negative_zero_display(self):
        self.assertEqual(m.display(F(-1, 10**14)), "0.000000000000")

    def test_candidate_endpoints(self):
        history = [F(0)] * 19 + [F(20)]
        self.assertEqual(
            [m.forecast(history, c) for c in m.CONFIGS],
            [F(0), F(1), F(23, 4), F(21, 2), F(61, 4), F(20)],
        )

    def test_history_cannot_shorten(self):
        for n in [0, 19, 21]:
            with self.assertRaises(ValueError):
                m.forecast([F(1)] * n, "mean20")

    def test_exact_losses(self):
        score = m.metrics([F(1), F(3)], [F(0), F(0)])
        self.assertEqual(score["mae_exact"], "2/1")
        self.assertEqual(score["mse_exact"], "5/1")
        self.assertEqual(score["rmse"], "2.236067977500")

    def test_zero_losses(self):
        self.assertEqual(m.metrics([F(1)], [F(1)])["mae_exact"], "0/1")

    def test_unmatched_losses_rejected(self):
        for a, b in [([], []), ([F(1)], [])]:
            with self.assertRaises(ValueError):
                m.metrics(a, b)

    def test_selection_ties(self):
        self.assertEqual(m.choose([{"mae_exact": "1/3"}, {"mae_exact": "2/6"}]), 0)

    def test_zero_can_win(self):
        self.assertEqual(
            m.choose([{"mae_exact": "0/1"}] + [{"mae_exact": "1/1"}] * 5), 0
        )


class Availability(unittest.TestCase):
    def setUp(self):
        self.levels = []
        self.catalog = []
        self.first = []
        for q in range(m.quarter("2000Q1"), m.quarter("2025Q1") + 1):
            v = m.label(q)
            self.catalog.append(dict(vintage=v, last_observation=m.label(q - 1)))
            for t in range(q - 21, q):
                self.levels.append(
                    dict(
                        vintage=v, observation=m.label(t), state="numeric", value="100"
                    )
                )
            if q <= m.quarter("2024Q4"):
                self.first.append(dict(observation=v, state="numeric", value="0"))
        for t in range(m.quarter("1994Q4"), m.quarter("2024Q4") + 1):
            self.levels.append(
                dict(
                    vintage="2026Q3",
                    observation=m.label(t),
                    state="numeric",
                    value="100",
                )
            )

    def build(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / "data").mkdir()
            (root / "data/vintage_catalog.json").write_text(
                json.dumps(self.catalog), encoding="utf-8"
            )
            tables = {
                "data/levels.csv": self.levels,
                "data/first_release.csv": self.first,
            }
            with patch.object(prep, "ROOT", root), patch.object(
                prep, "read_csv", side_effect=lambda p: copy.deepcopy(tables[p])
            ):
                return prep.build()

    def test_constant_series_calendar(self):
        origins, histories, gate = self.build()
        self.assertEqual(len(origins), 100)
        self.assertEqual(len(histories), 4000)
        self.assertEqual(sum(r["eligible"] for r in origins), 98)
        self.assertEqual(gate["first_series_compared"], 98)
        self.assertEqual(gate["status"], "PASS_AT_STORED_SOURCE_PRECISION")
        self.assertEqual(
            [r["target"] for r in origins if not r["eligible"]], ["2018Q4", "2019Q1"]
        )

    def test_missing_history_abstains(self):
        next(
            r
            for r in self.levels
            if r["vintage"] == "2000Q1" and r["observation"] == "1994Q4"
        )["state"] = "missing"
        self.assertIn("real_time_history_unavailable", self.build()[0][0]["reasons"])

    def test_future_origin_rejected(self):
        self.catalog[0]["last_observation"] = "2000Q1"
        self.assertIn(
            "origin_latest_observation_not_previous_quarter",
            self.build()[0][0]["reasons"],
        )

    def test_stale_origin_rejected(self):
        self.catalog[0]["last_observation"] = "1999Q3"
        self.assertFalse(self.build()[0][0]["eligible"])

    def test_early_target_binding(self):
        self.catalog[1]["last_observation"] = "1999Q4"
        self.assertIn(
            "early_target_vintage_latest_observation_mismatch",
            self.build()[0][0]["reasons"],
        )

    def test_source_rounding_mismatch_stops_gate(self):
        self.first[0]["value"] = "0.0001"
        self.assertEqual(self.build()[2]["status"], "FAIL_FIRST_RELEASE_RECONCILIATION")

    def test_unexpected_source_precision_rejected(self):
        self.first[0]["value"] = "0.00001"
        with self.assertRaisesRegex(AssertionError, "precision changed"):
            self.build()

    def test_nonpositive_history_abstains(self):
        self.levels[0]["value"] = "0"
        self.assertFalse(self.build()[0][0]["eligible"])

    def test_target_revision_does_not_change_prediction(self):
        baseline = self.build()[0][0]
        next(
            r
            for r in self.levels
            if r["vintage"] == "2000Q2" and r["observation"] == "2000Q1"
        )["value"] = "110"
        self.first[0]["value"] = "46.41"
        changed = self.build()[0][0]
        self.assertNotEqual(baseline["early_target"], changed["early_target"])
        self.assertEqual(baseline["real_time_mean20"], changed["real_time_mean20"])
        self.assertEqual(baseline["real_time_last"], changed["real_time_last"])


if __name__ == "__main__":
    unittest.main()
