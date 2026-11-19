"""Headless tests for pairing best-effort baseline runs with reliable runs.

Fixtures stand in for the baseline files and a fake runner stands in for
``experiment_runner.run_once``: no sockets, sleeps or live experiments.
"""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import calculate_metrics
import compare_delivery_modes as compare

PATH = ["H1", "A", "C", "D", "H2"]
ROUTES = {"A": {"H1": "H1", "H2": "C"}, "C": {"H1": "A", "H2": "D"}}
CONFIG = {"source": "H1", "destination": "H2", "path": PATH, "routes": ROUTES,
          "devices": {}, "links": []}


def messages(count=20, prefix="probe"):
    return [f"{prefix}-{i:04d}" for i in range(count)]


def condition(loss, delay, seed):
    if loss == 0 and delay == 0:
        return []
    return [{"from": "A", "to": "C", "loss": loss, "delay_ms": delay, "seed": seed}]


def common(mode, loss, delay, seed, run_id, max_retries):
    return {
        "schema_version": 1, "run_id": run_id, "repeat": seed - 42, "mode": mode,
        "seed": seed, "base_seed": 42,
        "config": {**copy.deepcopy(CONFIG), "conditions": condition(loss, delay, seed)},
        "settings": {"timeout": 1.0, "max_retries": max_retries, "loss": loss,
                     "delay_ms": delay, "link_from": "A", "link_to": "C",
                     "message_count": 20, "message_prefix": "probe", "repeats": 3},
        "workload": {"messages": messages()}, "runtime": {"ports": {}},
        "runner_error": None,
    }


def best_effort_record(loss, delay, seed, timeouts=0, case_type="core"):
    record = common("best_effort", loss, delay, seed,
                    f"core-loss{loss}-delay{delay}-seed{seed}", None)
    lost = set(range(timeouts))
    record["outcome"] = {"type": "best_effort_probes", "messages": [
        {"index": i, "message": m, "status": "TIMEOUT" if i in lost else "MATCHED",
         "response": None if i in lost else m, "elapsed_s": 1.0 if i in lost else 0.005}
        for i, m in enumerate(messages())]}
    record["events"] = [{"t": 0.0, "fields": {"event": "send", "run_id": record["run_id"]}}]
    record["matrix"] = {
        "case_id": record["run_id"], "case_type": case_type, "index": 0, "source": "H1",
        "destination": "H2", "loss": loss, "delay_ms": delay, "seed": seed,
        "base_seed": 42, "repeat": seed - 42, "probes": 20,
        "condition_link": "A->C" if case_type == "core" else None}
    return record


def reliable_record(loss, delay, seed, status="COMPLETE", delivered=20, retransmits=0,
                    run_id=None):
    record = common("reliable", loss, delay, seed,
                    run_id or f"compare-reliable-loss{loss}-delay{delay}-seed{seed}", 3)
    record["outcome"] = {"type": "reliable_transfer", "elapsed_s": 0.5 + retransmits,
                         "result": {"id": 0, "status": status, "count": 20,
                                    "sent": delivered}}
    record["events"] = (
        [{"t": 0.0, "fields": {"event": "deliver", "type": "DATA", "src": "H1",
                               "msg_id": "0", "seq": str(i)}} for i in range(delivered)]
        + [{"t": 0.1, "fields": {"event": "retransmit", "seq": "1"}}] * retransmits
        + ([{"t": 0.2, "fields": {"event": "failure", "seq": str(delivered)}}]
           if status == "FAILED" else []))
    return record


def baseline_records():
    """12 core runs (incl. delay 40) + 2 acceptance runs, like the real matrix."""
    records = []
    for loss in (0.0, 0.05):
        for delay in (0, 40):
            for seed in (42, 43, 44):
                timeouts = 2 if loss and seed == 43 else 0
                records.append(best_effort_record(loss, delay, seed, timeouts))
    for source, destination in (("H1", "H2"), ("H2", "H1")):
        record = best_effort_record(0.0, 0, 42, case_type="directional_acceptance")
        record["run_id"] = f"accept-{source}-{destination}"
        record["matrix"].update(case_id=record["run_id"], source=source,
                                destination=destination)
        records.append(record)
    return records


class FakeRunner:
    """Stands in for experiment_runner.run_once; records how it was called."""

    def __init__(self, outcomes=None):
        self.calls, self.outcomes = [], outcomes or {}

    def __call__(self, settings, repeat, run_id=None):
        self.calls.append((settings, repeat, run_id))
        seed = settings.seed + repeat
        kwargs = self.outcomes.get((settings.loss, seed), {})
        record = reliable_record(settings.loss, settings.delay_ms, seed, run_id=run_id,
                                 **kwargs)
        record["settings"]["timeout"] = settings.timeout
        record["settings"]["max_retries"] = settings.max_retries
        return record


class SelectionTests(unittest.TestCase):
    def test_exact_six_cases_ordered_by_loss_then_seed(self):
        cases = compare.select_best_effort_cases(baseline_records())
        self.assertEqual(len(cases), 6)  # 2 loss levels x 3 seeds
        self.assertEqual([(c["matrix"]["loss"], c["seed"]) for c in cases],
                         [(l, s) for l in (0.0, 0.05) for s in (42, 43, 44)])
        self.assertTrue(all(c["matrix"]["delay_ms"] == 0 and c["mode"] == "best_effort"
                            for c in cases))

    def test_delay_acceptance_and_reliable_records_are_not_selected(self):
        records = baseline_records() + [reliable_record(0.0, 0, 42)]
        run_ids = {c["run_id"] for c in compare.select_best_effort_cases(records)}
        self.assertFalse(any("delay40" in r or r.startswith(("accept", "compare"))
                             for r in run_ids))

    def test_missing_duplicate_and_unexpected_baselines_are_rejected(self):
        records = baseline_records()
        with self.assertRaisesRegex(ValueError, "missing cases"):
            compare.select_best_effort_cases(
                [r for r in records if r["run_id"] != "core-loss0.05-delay0-seed44"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            compare.select_best_effort_cases(records + [copy.deepcopy(records[0])])
        odd = copy.deepcopy(records)
        odd[0]["settings"]["timeout"] = 2.0
        with self.assertRaisesRegex(ValueError, "unexpected settings"):
            compare.select_best_effort_cases(odd)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.be = best_effort_record(0.05, 0, 43, timeouts=2)
        self.rel = reliable_record(0.05, 0, 43)

    def test_matching_runs_are_compatible(self):
        compare.check_compatible(self.be, self.rel)

    def test_each_mismatch_is_rejected_by_name(self):
        def mutate(change):
            record = copy.deepcopy(self.rel)
            change(record)
            return record

        cases = {
            "workload": (lambda r: r["workload"]["messages"].__setitem__(0, "other"), "workload"),
            "path": (lambda r: r["config"].update(path=["H1", "A", "B", "D", "H2"]), "config.path"),
            "routes": (lambda r: r["config"]["routes"]["A"].update(H2="B"), "config.routes"),
            "delay": (lambda r: r["settings"].update(delay_ms=40), "settings.delay_ms"),
            "timeout": (lambda r: r["settings"].update(timeout=2.0), "settings.timeout"),
            "seed": (lambda r: r.update(seed=44), "seed"),
            "loss": (lambda r: r["settings"].update(loss=0.0), "settings.loss"),
            "condition": (lambda r: r["config"].update(conditions=[]), "config.conditions"),
            "source": (lambda r: r["config"].update(source="H2"), "config.source"),
            "link": (lambda r: r["settings"].update(link_to="D"), "settings.link_to"),
            "prefix": (lambda r: r["settings"].update(message_prefix="x"), "message_prefix"),
            "retries": (lambda r: r["settings"].update(max_retries=1), "max_retries"),
            "mode": (lambda r: r.update(mode="best_effort"), "modes must be"),
        }
        for name, (change, fragment) in cases.items():
            with self.subTest(name), self.assertRaises(compare.IncompatiblePair) as ctx:
                compare.check_compatible(self.be, mutate(change))
            self.assertIn(fragment, str(ctx.exception))

    def test_a_best_effort_side_with_retries_is_rejected(self):
        self.be["settings"]["max_retries"] = 3
        with self.assertRaises(compare.IncompatiblePair):
            compare.check_compatible(self.be, self.rel)

    def test_reused_metrics_must_match_the_raw_record(self):
        row = calculate_metrics.compute_metrics(self.be)
        compare.check_metrics_match(self.be, row)
        row["unique_delivered"] += 1
        with self.assertRaisesRegex(ValueError, "does not match"):
            compare.check_metrics_match(self.be, row)
        with self.assertRaisesRegex(ValueError, "no metrics row"):
            compare.check_metrics_match(self.be, None)


class ReliableSettingsTests(unittest.TestCase):
    def test_every_setting_but_mode_is_copied_from_the_baseline(self):
        be = best_effort_record(0.05, 0, 44)
        settings = compare.reliable_settings_for(be)
        self.assertEqual(
            (settings.mode, settings.messages, settings.message_prefix, settings.timeout,
             settings.max_retries, settings.loss, settings.delay_ms, settings.link_from,
             settings.link_to, settings.source, settings.destination, settings.seed),
            ("reliable", 20, "probe", 1.0, 3, 0.05, 0, "A", "C", "H1", "H2", 42))
        self.assertEqual(compare.reliable_run_id(be), "compare-reliable-loss0.05-delay0-seed44")


class RunComparisonTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, True))
        self.raw = self.directory / "raw.jsonl"
        self.metrics = self.directory / "metrics.jsonl"
        self.output = self.directory / "out" / "comparison.jsonl"
        self.reliable_raw = self.directory / "out" / "reliable_raw.jsonl"
        records = baseline_records()
        self.write(self.raw, records)
        self.write(self.metrics, [calculate_metrics.compute_metrics(r) for r in records])

    @staticmethod
    def write(path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def run_compare(self, runner=None):
        runner = runner or FakeRunner()
        pairs = compare.run_comparison(self.raw, self.metrics, self.output,
                                       self.reliable_raw, run=runner)
        return pairs, runner

    def test_six_reliable_cases_are_run_with_matching_settings(self):
        _, runner = self.run_compare()
        self.assertEqual(len(runner.calls), 6)
        for (settings, repeat, run_id), (loss, seed) in zip(
                runner.calls, [(l, s) for l in (0.0, 0.05) for s in (42, 43, 44)]):
            self.assertEqual(
                (settings.mode, settings.messages, settings.timeout, settings.max_retries,
                 settings.loss, settings.delay_ms, settings.source, settings.destination,
                 settings.seed, repeat, run_id),
                ("reliable", 20, 1.0, 3, loss, 0, "H1", "H2", 42, seed - 42,
                 f"compare-reliable-loss{loss}-delay0-seed{seed}"))

    def test_output_pairs_cover_both_loss_levels_with_pairing_metadata(self):
        pairs, _ = self.run_compare()
        loaded = [json.loads(line) for line in self.output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(loaded, json.loads(json.dumps(pairs)))
        self.assertEqual(len(loaded), 6)
        self.assertEqual({p["pair"]["loss"] for p in loaded}, {0.0, 0.05})
        self.assertEqual({p["pair"]["seed"] for p in loaded}, {42, 43, 44})
        first = loaded[0]
        self.assertEqual(first["pair_id"], "loss0.0-delay0-seed42")
        self.assertEqual(
            {k: first["pair"][k] for k in ("delay_ms", "source", "destination", "condition_link",
                                          "path", "workload_size", "message_prefix", "timeout",
                                          "reliable_max_retries", "base_seed", "repeat")},
            {"delay_ms": 0, "source": "H1", "destination": "H2", "condition_link": "A->C",
             "path": PATH, "workload_size": 20, "message_prefix": "probe", "timeout": 1.0,
             "reliable_max_retries": 3, "base_seed": 42, "repeat": 0})
        self.assertEqual(first["pair"]["workload"], messages())
        self.assertIn("sequential", first["pair"]["pacing"])

    def test_best_effort_records_are_reused_not_duplicated_or_rerun(self):
        pairs, runner = self.run_compare()
        baseline = {r["run_id"]: calculate_metrics.compute_metrics(r)
                    for r in baseline_records()}
        for pair in pairs:
            side = pair["best_effort"]
            self.assertEqual(side["metrics"], baseline[side["run_id"]])  # existing metrics
            self.assertEqual(side["source"], "reused")
            self.assertEqual(set(side), {"run_id", "source", "metrics"})  # no raw copy
        self.assertTrue(all(settings.mode == "reliable" for settings, _, _ in runner.calls))

    def test_reliable_metrics_use_the_public_metrics_function(self):
        pairs, _ = self.run_compare(FakeRunner({(0.05, 42): {"retransmits": 4}}))
        reliable = {p["pair_id"]: p["reliable"] for p in pairs}["loss0.05-delay0-seed42"]
        expected = calculate_metrics.compute_metrics(
            reliable_record(0.05, 0, 42, retransmits=4))
        self.assertEqual(reliable["metrics"], expected)
        self.assertEqual((reliable["metrics"]["retransmissions"],
                          reliable["metrics"]["complete_sequence"]), (4, True))

    def test_failed_partial_and_timed_out_runs_are_preserved(self):
        runner = FakeRunner({(0.05, 43): {"status": "FAILED", "delivered": 7, "retransmits": 3}})
        pairs, _ = self.run_compare(runner)
        failed = {p["pair_id"]: p for p in pairs}["loss0.05-delay0-seed43"]
        self.assertEqual((failed["best_effort"]["metrics"]["status"],
                          failed["best_effort"]["metrics"]["timeouts"],
                          failed["best_effort"]["metrics"]["complete_sequence"]),
                         ("PARTIAL", 2, False))
        rel = failed["reliable"]["metrics"]
        self.assertEqual((rel["status"], rel["unique_delivered"], rel["complete_sequence"],
                          rel["failures"], rel["retransmissions"]), ("FAILED", 7, False, 1, 3))
        raw = [json.loads(l) for l in self.reliable_raw.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(raw), 6)  # raw reliable evidence, including the failure
        self.assertIn("FAILED", {r["outcome"]["result"]["status"] for r in raw})

    def test_inputs_are_not_modified_and_outputs_are_created(self):
        before = (self.digest(self.raw), self.digest(self.metrics))
        self.run_compare()
        self.assertEqual((self.digest(self.raw), self.digest(self.metrics)), before)
        self.assertTrue(self.output.exists() and self.reliable_raw.exists())

    def test_an_incompatible_reliable_result_is_rejected_and_nothing_is_written(self):
        class WrongWorkload(FakeRunner):
            def __call__(self, settings, repeat, run_id=None):
                record = super().__call__(settings, repeat, run_id)
                record["workload"]["messages"][3] = "tampered"
                return record

        with self.assertRaises(compare.IncompatiblePair):
            self.run_compare(WrongWorkload())
        self.assertFalse(self.output.exists() or self.reliable_raw.exists())

    def test_stale_baseline_metrics_stop_the_run_before_any_reliable_case(self):
        rows = [json.loads(l) for l in self.metrics.read_text(encoding="utf-8").splitlines()]
        rows[0]["unique_delivered"] = 0
        self.write(self.metrics, rows)
        runner = FakeRunner()
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.run_compare(runner)
        self.assertEqual(runner.calls, [])

    def test_output_paths_must_not_collide_with_inputs(self):
        for output, reliable_raw in ((self.raw, self.reliable_raw),
                                     (self.output, self.metrics),
                                     (self.output, self.output)):
            with self.assertRaises(ValueError):
                compare.run_comparison(self.raw, self.metrics, output, reliable_raw,
                                       run=FakeRunner())


class CliTests(unittest.TestCase):
    def test_help_describes_reuse(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            compare.main(["--help"])
        self.assertIn("reused", out.getvalue())

    def test_exit_statuses(self):
        pair = {"pair_id": "p", "best_effort": {"metrics": {
            "unique_delivered": 18, "workload_size": 20, "status": "PARTIAL", "timeouts": 2,
            "elapsed_s": 2.0}}, "reliable": {"metrics": {
                "unique_delivered": 20, "workload_size": 20, "status": "COMPLETE",
                "retransmissions": 2, "failures": 0, "elapsed_s": 2.1, "runner_error": None}}}
        with patch.object(compare, "run_comparison", return_value=[pair]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compare.main([]), 0)
        broken = copy.deepcopy(pair)
        broken["reliable"]["metrics"]["runner_error"] = "boom"
        with patch.object(compare, "run_comparison", return_value=[broken]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(compare.main([]), 1)
        with patch.object(compare, "run_comparison", side_effect=ValueError("bad")), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(compare.main([]), 2)
        self.assertIn("bad", err.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(compare.main(["--raw", "does-not-exist.jsonl"]), 2)
            self.assertEqual(compare.main(["--max-retries", "-1"]), 2)


if __name__ == "__main__":
    unittest.main()
