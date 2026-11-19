"""Tests for the core baseline matrix definition and runner.

The full 240-probe matrix is not run here; execution tests use tiny cases with
short timeouts on ephemeral ports in temporary directories.
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import experiment_runner as runner
import run_baseline_matrix as matrix
from config import Config
from run_baseline_matrix import MatrixCase


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


class CaseDefinitionTests(unittest.TestCase):
    def test_exactly_twelve_core_cases_cover_every_condition_and_seed(self):
        cases = matrix.core_cases()
        self.assertEqual(len(cases), 12)
        self.assertEqual({(c.loss, c.delay_ms, c.seed) for c in cases},
                         {(loss, delay, seed) for loss in (0.0, 0.05)
                          for delay in (0, 40) for seed in (42, 43, 44)})
        for case in cases:
            self.assertEqual((case.case_type, case.source, case.destination, case.probes,
                              case.condition_link), ("core", "H1", "H2", 20, "A->C"))

    def test_case_order_and_seeds_are_fixed(self):
        expected = [
            f"core-loss{loss}-delay{delay}-seed{seed}"
            for loss in (0.0, 0.05) for delay in (0, 40) for seed in (42, 43, 44)]
        self.assertEqual([c.case_id for c in matrix.core_cases()], expected)
        self.assertEqual(matrix.all_cases(), matrix.all_cases())  # deterministic
        self.assertEqual([c.seed for c in matrix.core_cases()[:3]], [42, 43, 44])
        ids = [c.case_id for c in matrix.all_cases()]
        self.assertEqual(len(set(ids)), len(ids))

    def test_two_loss_free_directional_acceptance_cases(self):
        forward, reverse = matrix.acceptance_cases()
        self.assertEqual((forward.source, forward.destination), ("H1", "H2"))
        self.assertEqual((reverse.source, reverse.destination), ("H2", "H1"))
        for case in (forward, reverse):
            self.assertEqual((case.case_type, case.loss, case.delay_ms, case.probes),
                             ("directional_acceptance", 0.0, 0, 20))
            self.assertIsNone(case.condition_link)  # nothing injected
            self.assertEqual(
                runner.conditions_for(case.settings(), case.seed), [])

    def test_probe_totals(self):
        self.assertEqual(sum(c.probes for c in matrix.core_cases()), 240)
        self.assertEqual(sum(c.probes for c in matrix.acceptance_cases()), 40)
        self.assertEqual(sum(c.probes for c in matrix.all_cases()), 280)
        self.assertEqual(len(matrix.all_cases()), 14)

    def test_settings_use_the_best_effort_runner_with_the_core_condition(self):
        for case in matrix.all_cases():
            with self.subTest(case.case_id):
                settings = case.settings()
                settings.validate()  # A->C is a real link; hosts exist
                self.assertEqual((settings.mode, settings.messages, settings.timeout,
                                  settings.seed), ("best_effort", 20, 1.0, 42))
                self.assertEqual((settings.link_from, settings.link_to), ("A", "C"))
                self.assertEqual(runner.derive_run_seed(settings.seed, case.repeat), case.seed)
        core = matrix.core_cases()[-1].settings()
        rule = runner.conditions_for(core, matrix.core_cases()[-1].seed)[0]
        self.assertEqual((rule["from"], rule["to"], rule["loss"], rule["delay_ms"], rule["seed"]),
                         ("A", "C", 0.05, 40, 44))

    def test_condition_link_is_on_the_default_route(self):
        path = runner.route_path(Config(), "H1", "H2")
        self.assertIn(matrix.CONDITION_LINK, list(zip(path, path[1:])))


class RunMatrixTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / "out" / "matrix.jsonl"

    def tiny_cases(self):
        return [
            MatrixCase("core", "H1", "H2", 0.0, 0, 0, probes=3),
            MatrixCase("core", "H1", "H2", 0.0, 40, 1, probes=3),
            MatrixCase("core", "H1", "H2", 1.0, 0, 2, probes=3),   # every probe times out
            MatrixCase("directional_acceptance", "H2", "H1", 0.0, 0, 0, probes=3),
        ]

    def run_tiny(self, **kwargs):
        return matrix.run_matrix(self.output, cases=self.tiny_cases(), timeout=0.25, **kwargs)

    def test_all_records_land_in_one_loadable_file_in_case_order(self):
        records = self.run_tiny()
        loaded = load(self.output)
        self.assertEqual(loaded, json.loads(json.dumps(records)))
        self.assertEqual(len(loaded), 4)
        self.assertEqual([r["matrix"]["index"] for r in loaded], [0, 1, 2, 3])
        self.assertEqual([r["run_id"] for r in loaded],
                         [c.case_id for c in self.tiny_cases()])  # unique ids

    def test_metadata_is_written_for_every_record(self):
        for case, record in zip(self.tiny_cases(), self.run_tiny()):
            meta = record["matrix"]
            self.assertEqual(
                (meta["case_id"], meta["case_type"], meta["source"], meta["destination"],
                 meta["loss"], meta["delay_ms"], meta["seed"], meta["base_seed"],
                 meta["repeat"], meta["probes"], meta["condition_link"]),
                (case.case_id, case.case_type, case.source, case.destination,
                 case.loss, case.delay_ms, case.seed, 42, case.repeat, 3,
                 case.condition_link))
            self.assertEqual((record["seed"], record["repeat"], record["mode"]),
                             (case.seed, case.repeat, "best_effort"))
            self.assertEqual(record["config"]["source"], case.source)
            self.assertEqual(record["config"]["destination"], case.destination)

    def test_raw_per_probe_statuses_and_elapsed_times_are_preserved(self):
        clean, delayed, lossy, reverse = self.run_tiny()
        for record in (clean, delayed, reverse):
            probes = record["outcome"]["messages"]
            self.assertEqual([p["message"] for p in probes], record["workload"]["messages"])
            self.assertEqual({p["status"] for p in probes}, {"MATCHED"})
            self.assertTrue(all(isinstance(p["elapsed_s"], float) and p["elapsed_s"] > 0
                                for p in probes))
            self.assertTrue(all(p["response"] == p["message"] for p in probes))
        # The injected delay is a minimum: no matched probe can be faster than it.
        self.assertTrue(all(p["elapsed_s"] >= 0.04 for p in delayed["outcome"]["messages"]))
        probes = lossy["outcome"]["messages"]  # timeouts stay in the output
        self.assertEqual([p["status"] for p in probes], ["TIMEOUT"] * 3)
        self.assertTrue(all(p["response"] is None and p["elapsed_s"] > 0 for p in probes))
        self.assertIsNone(lossy["runner_error"])

    def test_events_route_and_condition_are_kept(self):
        clean, delayed, lossy, _ = self.run_tiny()
        self.assertEqual(clean["config"]["path"], ["H1", "A", "C", "D", "H2"])
        self.assertEqual(delayed["config"]["conditions"],
                         [{"from": "A", "to": "C", "loss": 0.0, "delay_ms": 40, "seed": 43}])
        self.assertEqual(clean["config"]["conditions"], [])
        names = {e["fields"]["event"] for e in lossy["events"]}
        self.assertTrue({"drop", "timeout"} <= names)
        self.assertEqual(clean["runtime"]["ports"].keys(), lossy["runtime"]["ports"].keys())
        fixed = {a["port"] for a in Config().data["addresses"].values()}
        self.assertFalse(fixed & set(clean["runtime"]["ports"].values()))

    def test_existing_output_is_not_overwritten_without_the_flag(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_text("precious\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.run_tiny()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "precious\n")
        self.run_tiny(overwrite=True)
        self.assertEqual(len(load(self.output)), 4)

    def test_duplicate_or_invalid_cases_are_rejected_before_running(self):
        case = MatrixCase("core", "H1", "H2", 0.0, 0, 0, probes=1)
        with self.assertRaises(ValueError):
            matrix.run_matrix(self.output, cases=[case, case])
        bad = MatrixCase("core", "H1", "H1", 0.0, 0, 0, probes=1)
        with self.assertRaises(ValueError):
            matrix.run_matrix(self.output, cases=[bad])
        self.assertFalse(self.output.exists())

    def test_progress_is_reported_per_case(self):
        seen = []
        matrix.run_matrix(self.output, cases=self.tiny_cases()[:2], timeout=0.25,
                          progress=seen.append)
        self.assertEqual(len(seen), 2)


class CliTests(unittest.TestCase):
    def test_main_exit_statuses(self):
        with patch.object(matrix, "run_matrix", return_value=[]) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(matrix.main(["--output", "x.jsonl", "--overwrite"]), 0)
        self.assertEqual(run.call_args.kwargs["overwrite"], True)
        with patch.object(matrix, "run_matrix", side_effect=FileExistsError("exists")), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(matrix.main([]), 2)
        failing = [{"runner_error": "boom", "matrix": {}, "outcome": None}]
        with patch.object(matrix, "run_matrix", return_value=failing), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(matrix.main([]), 1)

    def test_help_mentions_the_matrix(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            matrix.main(["--help"])
        self.assertIn("baseline matrix", out.getvalue())


if __name__ == "__main__":
    unittest.main()
