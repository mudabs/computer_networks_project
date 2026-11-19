"""Headless tests for the evaluation report (fixtures only; no experiments)."""
import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import create_evaluation_report as report

PATH = ["H1", "A", "C", "D", "H2"]


def be_row(run_id, loss, delay, seed, timeouts, workload, timeout, case_type="core",
           source="H1", destination="H2"):
    delivered = workload - timeouts
    per_probe = 0.001 + delay * 0.0011           # seconds per matched probe
    return {
        "schema_version": 1, "run_id": run_id, "mode": "best_effort", "source": source,
        "destination": destination,
        "matrix": {"case_id": run_id, "case_type": case_type, "condition_link": "A->C"},
        "loss": loss, "delay_ms": delay, "seed": seed, "repeat": seed - 42,
        "workload_size": workload, "unique_delivered": delivered,
        "complete_sequence": timeouts == 0, "matched_probes": delivered, "timeouts": timeouts,
        "elapsed_s": delivered * per_probe + timeouts * timeout,
        "status": "COMPLETE" if timeouts == 0 else ("PARTIAL" if delivered else "FAILED"),
        "transfer_status": None, "retransmissions": 0, "failures": 0, "runner_error": None}


def rel_row(run_id, loss, seed, workload, retx, status="COMPLETE", delivered=None, timeout=1.0):
    delivered = workload if delivered is None else delivered
    return {
        "schema_version": 1, "run_id": run_id, "mode": "reliable", "source": "H1",
        "destination": "H2", "matrix": None, "loss": loss, "delay_ms": 0, "seed": seed,
        "repeat": seed - 42, "workload_size": workload, "unique_delivered": delivered,
        "complete_sequence": status == "COMPLETE" and delivered == workload,
        "matched_probes": None, "timeouts": None,
        "elapsed_s": 0.02 + retx * (timeout + 0.01), "status": status,
        "transfer_status": status, "retransmissions": retx,
        "failures": 1 if status == "FAILED" else 0, "runner_error": None}


def raw_reliable(run_id, workload, retx, acks=None):
    def send(device, type_, **extra):
        return {"t": 0.0, "fields": {"event": "send", "device": device, "type": type_, **extra}}

    events = [send("H1", "DATA") for _ in range(workload + retx)]
    events += [send("H1", "DATA", retransmit="true") for _ in range(0)]
    events[:retx] = [send("H1", "DATA", retransmit="true") for _ in range(retx)]
    events += [send("H1", "FIN")]
    events += [send("H2", "ACK") for _ in range(workload + 1 if acks is None else acks)]
    events += [send(hop, "DATA") for hop in ("A", "C", "D") for _ in range(workload)]
    events += [{"t": 0.1, "fields": {"event": "drop", "device": "A", "reason": "injected_loss"}}
               for _ in range(retx)]
    return {"run_id": run_id, "config": {"source": "H1", "destination": "H2"}, "events": events}


class Fixture:
    """Baseline rows, pairs and raw reliable runs with controllable numbers."""

    def __init__(self, workload=20, prefix="probe", timeout=1.0, retries=3,
                 seeds=(42, 43, 44), delays=(0, 40), losses=(0.0, 0.05),
                 lossy_timeouts=None, reliable_failures=()):
        self.workload, self.prefix, self.timeout, self.retries = workload, prefix, timeout, retries
        lossy_timeouts = lossy_timeouts or {s: n for s, n in zip(seeds, (3, 1, 2))}
        self.baseline, self.pairs, self.raw = [], [], []
        for loss in losses:
            for delay in delays:
                for seed in seeds:
                    timeouts = lossy_timeouts[seed] if loss else 0
                    self.baseline.append(be_row(
                        f"core-loss{loss}-delay{delay}-seed{seed}", loss, delay, seed,
                        timeouts, workload, timeout))
        for source, destination in (("H1", "H2"), ("H2", "H1")):
            self.baseline.append(be_row(f"accept-{source}-{destination}", 0.0, 0, 42, 0, workload,
                                        timeout, "directional_acceptance", source, destination))
        for loss in losses:
            for seed in seeds:
                be = next(r for r in self.baseline
                          if r["run_id"] == f"core-loss{loss}-delay0-seed{seed}")
                retx = be["timeouts"]
                run_id = f"compare-reliable-loss{loss}-delay0-seed{seed}"
                failed = (loss, seed) in reliable_failures
                rel = rel_row(run_id, loss, seed, workload, retx,
                              "FAILED" if failed else "COMPLETE",
                              workload // 2 if failed else None, timeout)
                self.pairs.append({
                    "schema_version": 1, "pair_id": f"loss{loss}-delay0-seed{seed}",
                    "pair": {"loss": loss, "delay_ms": 0, "seed": seed, "source": "H1",
                             "destination": "H2", "condition_link": "A->C", "path": PATH,
                             "workload_size": workload, "message_prefix": prefix,
                             "timeout": timeout, "reliable_max_retries": retries,
                             "pacing": "sequential: one message outstanding at a time"},
                    "best_effort": {"run_id": be["run_id"], "source": "reused", "metrics": be},
                    "reliable": {"run_id": run_id, "source": "run", "metrics": rel}})
                self.raw.append(raw_reliable(run_id, workload, retx))


class ReportTestCase(unittest.TestCase):
    def build(self, fixture=None):
        fixture = fixture or Fixture()
        summary = report.compute_summary(fixture.baseline, fixture.pairs, fixture.raw)
        return summary, report.render_report(summary)


class StructureTests(ReportTestCase):
    def test_required_sections_tables_and_loss_levels(self):
        _, text = self.build()
        for heading in ("## 1. Experiment setup", "## 2. Best-effort baseline by loss and delay",
                        "## 3. Added delay", "## 4. Best-effort vs reliable",
                        "## 5. Unsuccessful or partial runs", "## 6. Interpretation"):
            self.assertIn(heading, text)
        for number in range(1, 8):
            self.assertRegex(text, rf"\*\*Table {number} - ")
        self.assertIn("| 0% | best-effort |", text)
        self.assertIn("| 5% | reliable |", text)
        self.assertIn("| 5% | 40 |", text)  # delay results at every loss level

    def test_markdown_tables_are_well_formed(self):
        _, text = self.build()
        blocks = re.findall(r"(?:^\|.*\|\n?)+", text, flags=re.M)
        self.assertGreaterEqual(len(blocks), 7)
        for block in blocks:
            lines = block.strip().splitlines()
            self.assertRegex(lines[1], r"^\|(---\|)+$")  # header separator
            widths = {len(re.split(r"(?<!\\)\|", line)) for line in lines}
            self.assertEqual(len(widths), 1, block)  # same column count on every row
        self.assertTrue(text.endswith("\n") or text.endswith("."))

    def test_no_placeholders_leak_into_the_text(self):
        _, text = self.build()
        for bad in ("{", "}", "TODO", "XXX", "None", "nan", "inf"):
            self.assertNotIn(bad, text)

    def test_scope_statement_separates_reliable_and_delay_evidence(self):
        _, text = self.build()
        self.assertIn("Reliable mode was compared with best-effort at 0 ms added delay only", text)
        self.assertIn("40 ms delay results below come from the best-effort baseline matrix", text)
        self.assertIn("reliable mode was **not** measured with added delay", text)


class DerivedValueTests(ReportTestCase):
    def test_setup_comes_from_the_inputs(self):
        fixture = Fixture(workload=12, prefix="msg", timeout=2.0, retries=5, seeds=(7, 8, 9),
                          lossy_timeouts={7: 1, 8: 1, 9: 1})
        for pair in fixture.pairs:
            pair["pair"]["path"] = ["H1", "B", "D", "H2"]
        _, text = self.build(fixture)
        for expected in ("12 messages per run", "prefix `msg`", "H1 -> B -> D -> H2", "2 s per probe",
                         "5 retransmissions per packet", "7, 8, 9"):
            self.assertIn(expected, text)
        self.assertNotIn("20 messages", text)
        self.assertNotIn("42, 43, 44", text)

    def test_tables_and_interpretation_follow_the_data(self):
        first = self.build(Fixture(lossy_timeouts={42: 3, 43: 1, 44: 2}))[1]
        second = self.build(Fixture(lossy_timeouts={42: 5, 43: 0, 44: 7}))[1]
        self.assertIn("54/60", first)               # 60 - (3+1+2)
        self.assertIn("recorded 6 timeouts", first)
        self.assertIn("using 6 retransmissions", first)
        self.assertIn("48/60", second)              # 60 - (5+0+7)
        self.assertIn("recorded 12 timeouts", second)
        self.assertNotIn("54/60", second)
        self.assertNotEqual(first, second)

    def test_summary_values_match_manual_calculation(self):
        fixture = Fixture()
        summary, text = self.build(fixture)
        lossy = next(c for c in summary["comparison"] if c["loss"] == 0.05)
        self.assertEqual((lossy["best_effort"]["delivered"], lossy["best_effort"]["timeouts"],
                          lossy["best_effort"]["complete_runs"]), (54, 6, 0))
        self.assertEqual((lossy["reliable"]["delivered"], lossy["reliable"]["retransmissions"],
                          lossy["reliable"]["complete_runs"]), (60, 6, 3))
        clean_elapsed = (0.02 + 0.02 + 0.02) / 3
        self.assertAlmostEqual(summary["overhead"]["clean_elapsed_s"], clean_elapsed)
        entry = next(e for e in summary["overhead"]["lossy"] if e["retransmissions"] == 3)
        self.assertAlmostEqual(entry["extra_time_s"], 3 * 1.01)
        self.assertAlmostEqual(entry["extra_time_per_retransmission_s"], 1.01)
        self.assertEqual(entry["extra_network_sends"], 3)  # one extra send per retransmission
        self.assertEqual(entry["injected_drops"], 3)
        # delay effect: 0 -> 40 ms adds 0.0011 * 40 s per matched probe in the fixture
        effect = next(e for e in summary["delay_effect"] if e["loss"] == 0.0)
        self.assertAlmostEqual(effect["added_ms_per_probe"], 44.0)
        self.assertTrue(effect["delivery_identical_per_seed"])
        self.assertIn("about 44.0 ms more per matched probe", text)
        self.assertIn("about 1.01 s of extra elapsed time", text)
        self.assertIn("exactly one extra link transmission per retransmission", text)

    def test_interpretation_states_each_required_point_with_numbers(self):
        _, text = self.build()
        body = text[text.index("## 6. Interpretation"):]
        for phrase in ("Best-effort delivery can lose messages and time out",
                       "Stop-and-wait improves completion through acknowledgements and retransmission",
                       "Loss increases retries and elapsed time",
                       "Reliability has a time and traffic cost",
                       "Added delay increases round-trip and elapsed time"):
            self.assertIn(phrase, body)
        self.assertRegex(body, r"delivered 54/60 \(90\.0%\)")
        self.assertRegex(body, r"throughput fell from \d+ messages/s loss-free to [\d.-]+ messages/s")
        self.assertNotRegex(body, r"\{|\}|None")

    def test_delay_section_degrades_without_a_second_delay_level(self):
        summary, text = self.build(Fixture(delays=(0,)))
        self.assertEqual(summary["delay_effect"], [])
        self.assertIn("single delay level", text)
        self.assertNotIn("Added delay increases", text)


class UnsuccessfulRunTests(ReportTestCase):
    def test_partial_and_timed_out_best_effort_runs_are_listed(self):
        summary, text = self.build()
        ids = {r["run_id"] for r in summary["unsuccessful_runs"]}
        self.assertEqual(len(ids), 6)  # 3 seeds x 2 delays at 5% loss
        self.assertIn("| core-loss0.05-delay0-seed42 | baseline matrix | best_effort | 5% | 0 | 42 "
                      "| 17/20 | 3 | 0 | 0 | PARTIAL |", text)
        self.assertNotIn("core-loss0.0-delay0-seed42 | baseline", text.split("## 5.")[1])
        self.assertIn("Every reliable run completed; all unsuccessful runs are best-effort.", text)

    def test_reliable_failures_are_reported_and_change_the_interpretation(self):
        fixture = Fixture(reliable_failures={(0.05, 43)})
        summary, text = self.build(fixture)
        failed = [r for r in summary["unsuccessful_runs"] if r["mode"] == "reliable"]
        self.assertEqual([(r["run_id"], r["status"], r["delivered"]) for r in failed],
                         [("compare-reliable-loss0.05-delay0-seed43", "FAILED", 10)])
        self.assertIn("| compare-reliable-loss0.05-delay0-seed43 | comparison (reliable) | reliable "
                      "| 5% | 0 | 43 | 10/20 |", text)
        self.assertIn("Reliable runs that did not complete are listed above.", text)
        body = text[text.index("## 6."):]
        self.assertIn("completed 2 of 3 runs", body)
        self.assertIn("recorded 1 failures: recovery is bounded by the retry limit", body)
        self.assertNotIn("Every reliable run completed", text)

    def test_all_successful_inputs_report_none_unsuccessful(self):
        fixture = Fixture(lossy_timeouts={42: 0, 43: 0, 44: 0})
        summary, text = self.build(fixture)
        self.assertEqual(summary["unsuccessful_runs"], [])
        self.assertIn("No unsuccessful or partial runs were found.", text)


class InputAndFileTests(ReportTestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.directory, True))
        fixture = Fixture()
        self.paths = {name: self.directory / f"{name}.jsonl"
                      for name in ("baseline", "comparison", "raw")}
        for name, rows in (("baseline", fixture.baseline), ("comparison", fixture.pairs),
                           ("raw", fixture.raw)):
            self.write(self.paths[name], rows)
        self.report = self.directory / "out" / "report.md"
        self.summary = self.directory / "out" / "summary.json"

    @staticmethod
    def write(path, rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def generate(self):
        return report.generate(self.paths["baseline"], self.paths["comparison"], self.paths["raw"],
                               self.report, self.summary)

    def digests(self):
        return [hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths.values()]

    def test_files_are_written_and_inputs_are_untouched(self):
        before = self.digests()
        summary = self.generate()
        self.assertEqual(self.digests(), before)
        self.assertEqual(json.loads(self.summary.read_text(encoding="utf-8")),
                         json.loads(json.dumps(summary)))
        self.assertEqual(self.report.read_text(encoding="utf-8"), report.render_report(summary))
        self.assertEqual(summary["inputs"], {"baseline_metrics_rows": 14, "comparison_rows": 6,
                                             "reliable_raw_records": 6})

    def test_bad_inputs_fail_clearly(self):
        rows = [json.loads(l) for l in self.paths["baseline"].read_text(encoding="utf-8").splitlines()]
        del rows[2]["timeouts"]
        self.write(self.paths["baseline"], rows)
        with self.assertRaisesRegex(ValueError, "baseline metrics row 3: missing timeouts"):
            self.generate()
        self.paths["baseline"].write_text('{"ok": 1}\n{broken\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r":2: invalid JSON"):
            self.generate()
        self.paths["baseline"].write_text("\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no records"):
            self.generate()
        self.assertFalse(self.report.exists())

    def test_missing_raw_reliable_record_is_rejected(self):
        raw = [json.loads(l) for l in self.paths["raw"].read_text(encoding="utf-8").splitlines()]
        self.write(self.paths["raw"], raw[1:])
        with self.assertRaisesRegex(ValueError, "no raw reliable record"):
            self.generate()

    def test_outputs_must_not_overwrite_inputs(self):
        with self.assertRaises(ValueError):
            report.generate(self.paths["baseline"], self.paths["comparison"], self.paths["raw"],
                            self.paths["baseline"], self.summary)
        with self.assertRaises(ValueError):
            report.generate(self.paths["baseline"], self.paths["comparison"], self.paths["raw"],
                            self.report, self.report)

    def test_cli_statuses_and_help(self):
        argv = ["--baseline-metrics", str(self.paths["baseline"]),
                "--comparison", str(self.paths["comparison"]),
                "--reliable-raw", str(self.paths["raw"]),
                "--report", str(self.report), "--summary", str(self.summary)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(report.main(argv), 0)
        self.assertIn("14 baseline rows, 6 pairs", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(report.main(argv[:1] + [str(self.directory / "missing.jsonl")] + argv[2:]), 2)
        self.assertIn("error:", err.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            report.main(["--help"])
        self.assertIn("Nothing is rerun", out.getvalue().replace("\n", " "))


class CommittedResultsTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_report_generates_from_the_committed_results(self):
        paths = [self.ROOT / "results" / name for name in (
            "baseline_metrics.jsonl", "mode_comparison.jsonl", "mode_comparison_reliable_raw.jsonl")]
        if not all(p.exists() for p in paths):
            self.skipTest("committed results not present")
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(directory, True))
        summary = report.generate(*paths, directory / "r.md", directory / "s.json")
        text = (directory / "r.md").read_text(encoding="utf-8")
        self.assertEqual(summary["inputs"]["baseline_metrics_rows"], 14)
        self.assertEqual(summary["inputs"]["comparison_rows"], 6)
        self.assertEqual({c["loss"] for c in summary["comparison"]}, {0.0, 0.05})
        self.assertIn("PARTIAL", text)       # unsuccessful best-effort runs
        self.assertIn("COMPLETE", text)      # successful runs
        self.assertNotIn("TODO", text)


if __name__ == "__main__":
    unittest.main()
