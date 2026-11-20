"""Headless tests for per-run metric definitions (synthetic records; no sockets)."""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import calculate_metrics as metrics

WORKLOAD = [f"probe-{i:04d}" for i in range(4)]
SOURCE, DESTINATION = "H1", "H2"


def event(name, t=0.0, **fields):
    return {"t": t, "fields": {"event": name, **fields}}


def deliver(seq, msg_id="7", src=SOURCE, type_="DATA"):
    return event("deliver", device=DESTINATION, src=src, msg_id=msg_id,
                 type=type_, seq=str(seq))


def base(mode, outcome, events=(), **extra):
    record = {
        "schema_version": 1, "run_id": f"{mode}-run", "repeat": 1, "mode": mode, "seed": 43,
        "base_seed": 42,
        "config": {"source": SOURCE, "destination": DESTINATION, "conditions": []},
        "settings": {"loss": 0.05, "delay_ms": 40, "timeout": 1.0},
        "workload": {"messages": list(WORKLOAD)},
        "outcome": outcome, "events": list(events), "runner_error": None,
    }
    record.update(extra)
    return record


def probes(statuses, elapsed=0.01):
    return {"type": "best_effort_probes", "messages": [
        {"index": i, "message": WORKLOAD[i], "status": status,
         "response": WORKLOAD[i] if status == "MATCHED" else None,
         "elapsed_s": elapsed * (i + 1)} for i, status in enumerate(statuses)]}


def transfer(status="COMPLETE", sent=4, elapsed=0.5):
    return {"type": "reliable_transfer", "elapsed_s": elapsed,
            "result": {"id": 7, "status": status, "count": 4, "sent": sent}}


def best_effort(statuses, **extra):
    return base("best_effort", probes(statuses), **extra)


def reliable(status="COMPLETE", delivered=range(4), extra_events=(), **extra):
    events = [deliver(i) for i in delivered] + list(extra_events)
    return base("reliable", transfer(status), events, **extra)


class BestEffortMetricsTests(unittest.TestCase):
    def test_all_matched_is_a_complete_sequence(self):
        row = metrics.compute_metrics(best_effort(["MATCHED"] * 4))
        self.assertEqual((row["unique_delivered"], row["matched_probes"], row["timeouts"],
                          row["complete_sequence"], row["status"]), (4, 4, 0, True, "COMPLETE"))

    def test_timeouts_are_counted_and_break_the_sequence(self):
        row = metrics.compute_metrics(best_effort(["MATCHED", "TIMEOUT", "MATCHED", "TIMEOUT"]))
        self.assertEqual((row["unique_delivered"], row["matched_probes"], row["timeouts"],
                          row["complete_sequence"], row["status"]), (2, 2, 2, False, "PARTIAL"))

    def test_nothing_matched_is_preserved_as_failed(self):
        row = metrics.compute_metrics(best_effort(["TIMEOUT"] * 4))
        self.assertEqual((row["unique_delivered"], row["timeouts"], row["status"],
                          row["complete_sequence"]), (0, 4, "FAILED", False))

    def test_elapsed_is_the_sum_of_sequential_probe_times(self):
        row = metrics.compute_metrics(best_effort(["MATCHED", "TIMEOUT", "MATCHED", "MATCHED"]))
        self.assertAlmostEqual(row["elapsed_s"], 0.01 + 0.02 + 0.03 + 0.04)  # timeouts included

    def test_missing_workload_probes_are_not_a_complete_sequence(self):
        record = best_effort(["MATCHED"] * 4)
        del record["outcome"]["messages"][3]  # only 3 of 4 messages were probed
        row = metrics.compute_metrics(record)
        self.assertEqual((row["unique_delivered"], row["complete_sequence"]), (3, False))

    def test_deliver_events_do_not_affect_best_effort_counts(self):
        record = best_effort(["TIMEOUT"] * 4, events=[deliver(0), deliver(0)])
        self.assertEqual(metrics.compute_metrics(record)["unique_delivered"], 0)


class ReliableMetricsTests(unittest.TestCase):
    def test_complete_transfer(self):
        row = metrics.compute_metrics(reliable())
        self.assertEqual((row["unique_delivered"], row["complete_sequence"], row["status"],
                          row["transfer_status"], row["elapsed_s"]),
                         (4, True, "COMPLETE", "COMPLETE", 0.5))
        self.assertIsNone(row["matched_probes"])  # probes do not apply to transfers
        self.assertIsNone(row["timeouts"])

    def test_duplicate_data_delivery_is_counted_once(self):
        record = reliable(extra_events=[deliver(1), deliver(1), deliver(3)])
        row = metrics.compute_metrics(record)
        self.assertEqual(row["unique_delivered"], 4)
        self.assertTrue(row["complete_sequence"])

    def test_same_sequence_in_another_message_id_is_a_distinct_delivery(self):
        row = metrics.compute_metrics(reliable(extra_events=[deliver(0, msg_id="8")]))
        self.assertEqual(row["unique_delivered"], 5)

    def test_missing_sequence_numbers_are_not_complete_even_if_outcome_says_so(self):
        row = metrics.compute_metrics(reliable(delivered=[0, 1, 3]))
        self.assertEqual((row["unique_delivered"], row["complete_sequence"],
                          row["status"]), (3, False, "COMPLETE"))  # status stays as reported

    def test_failed_transfer_keeps_its_partial_delivery(self):
        row = metrics.compute_metrics(reliable(status="FAILED", delivered=[0, 1]))
        self.assertEqual((row["unique_delivered"], row["complete_sequence"], row["status"],
                          row["transfer_status"]), (2, False, "FAILED", "FAILED"))

    def test_non_data_other_source_and_non_deliver_events_are_ignored(self):
        noise = [deliver(9, type_="FIN"), deliver(9, type_="ACK"), deliver(9, src="H3"),
                 event("duplicate", src=SOURCE, msg_id="7", type="DATA", seq="1"),
                 event("send", src=SOURCE, msg_id="7", type="DATA", seq="8")]
        row = metrics.compute_metrics(reliable(extra_events=noise))
        self.assertEqual(row["unique_delivered"], 4)

    def test_elapsed_is_the_runner_transfer_time(self):
        record = reliable()
        record["outcome"]["elapsed_s"] = 1.2345
        self.assertEqual(metrics.compute_metrics(record)["elapsed_s"], 1.2345)


class EventCountTests(unittest.TestCase):
    def test_retransmissions_and_failures_come_from_events(self):
        events = [event("retransmit", seq="1"), event("retransmit", seq="1"),
                  event("retransmit", seq="2"), event("failure", seq="2"),
                  event("send"), event("drop")]
        for record in (reliable(extra_events=events),
                       best_effort(["MATCHED"] * 4, events=events)):
            row = metrics.compute_metrics(record)
            self.assertEqual((row["retransmissions"], row["failures"]), (3, 1))

    def test_zero_when_there_are_none(self):
        row = metrics.compute_metrics(reliable())
        self.assertEqual((row["retransmissions"], row["failures"]), (0, 0))


class MetadataTests(unittest.TestCase):
    def test_identity_and_condition_fields_are_carried_over(self):
        matrix = {"case_id": "core-x", "case_type": "core", "index": 3, "source": "H1",
                  "destination": "H2", "loss": 0.05, "delay_ms": 40, "seed": 43,
                  "base_seed": 42, "repeat": 1, "probes": 4, "condition_link": "A->C"}
        row = metrics.compute_metrics(best_effort(["MATCHED"] * 4, matrix=matrix))
        self.assertEqual(
            (row["run_id"], row["mode"], row["source"], row["destination"], row["loss"],
             row["delay_ms"], row["seed"], row["repeat"], row["workload_size"], row["matrix"]),
            ("best_effort-run", "best_effort", "H1", "H2", 0.05, 40, 43, 1, 4, matrix))
        self.assertEqual(row["schema_version"], metrics.METRICS_SCHEMA_VERSION)

    def test_records_without_matrix_metadata_get_null(self):
        self.assertIsNone(metrics.compute_metrics(reliable())["matrix"])

    def test_runner_errors_without_an_outcome_are_kept(self):
        record = base("reliable", None, runner_error="RuntimeError: boom")
        metrics.validate_record(record)
        row = metrics.compute_metrics(record)
        self.assertEqual((row["status"], row["unique_delivered"], row["complete_sequence"],
                          row["elapsed_s"], row["runner_error"]),
                         ("ERROR", 0, False, None, "RuntimeError: boom"))

    def test_computing_metrics_does_not_mutate_the_record(self):
        record = reliable(extra_events=[deliver(1)])
        before = copy.deepcopy(record)
        metrics.compute_metrics(record)
        self.assertEqual(record, before)


class ValidationTests(unittest.TestCase):
    def assert_malformed(self, record, text):
        with self.assertRaises(metrics.MalformedRecord) as ctx:
            metrics.validate_record(record, "line 1")
        self.assertIn(text, str(ctx.exception))
        self.assertIn("line 1", str(ctx.exception))

    def test_valid_records_pass(self):
        metrics.validate_record(reliable())
        metrics.validate_record(best_effort(["MATCHED"] * 4))

    def test_malformed_records_fail_clearly(self):
        cases = []

        def mutate(label, text, change, factory=reliable):
            record = factory()
            change(record)
            cases.append((label, text, record))

        mutate("missing key", "missing key 'events'", lambda r: r.pop("events"))
        mutate("bad version", "schema_version", lambda r: r.update(schema_version=2))
        mutate("bad mode", "unknown mode", lambda r: r.update(mode="fast"))
        mutate("bad seed", "seed must be an integer", lambda r: r.update(seed="42"))
        mutate("no run id", "run_id", lambda r: r.update(run_id=""))
        mutate("config", "source and destination", lambda r: r.update(config={}))
        mutate("settings", "numeric loss", lambda r: r["settings"].pop("loss"))
        mutate("workload", "workload.messages", lambda r: r.update(workload={"messages": [1]}))
        mutate("event shape", "events[0]", lambda r: r.update(events=[{"fields": {}}]))
        mutate("null outcome", "no runner_error", lambda r: r.update(outcome=None))
        mutate("wrong outcome type", "outcome type",
               lambda r: r.update(outcome=probes(["MATCHED"] * 4)))
        mutate("transfer result", "status, sent and count",
               lambda r: r["outcome"]["result"].pop("status"))
        mutate("transfer elapsed", "elapsed_s", lambda r: r["outcome"].pop("elapsed_s"))
        mutate("probe status", "MATCHED/TIMEOUT",
               lambda r: r["outcome"]["messages"][0].update(status="LOST"),
               lambda: best_effort(["MATCHED"] * 4))
        mutate("probe elapsed", "numeric elapsed_s",
               lambda r: r["outcome"]["messages"][1].pop("elapsed_s"),
               lambda: best_effort(["MATCHED"] * 4))
        mutate("matrix", "matrix must be an object", lambda r: r.update(matrix=[1]))
        for label, text, record in cases:
            with self.subTest(label):
                self.assert_malformed(record, text)
        self.assert_malformed([], "JSON object")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.directory, True))
        self.raw = self.directory / "raw.jsonl"
        self.out = self.directory / "nested" / "metrics.jsonl"

    def write_raw(self, records, extra=""):
        self.raw.write_text("".join(json.dumps(r) + "\n" for r in records) + extra,
                            encoding="utf-8")

    def raw_digest(self):
        return hashlib.sha256(self.raw.read_bytes()).hexdigest()

    def sample(self):
        a = best_effort(["MATCHED", "TIMEOUT", "MATCHED", "MATCHED"], run_id="a")
        b = reliable(run_id="b")
        c = reliable(status="FAILED", delivered=[0], run_id="c")
        return [a, b, c]

    def test_one_output_row_per_input_row_in_order_and_input_untouched(self):
        self.write_raw(self.sample(), extra="\n")  # a blank line is ignored
        before = self.raw_digest()
        rows = metrics.calculate(self.raw, self.out)
        self.assertEqual(self.raw_digest(), before)  # raw JSONL not modified
        loaded = [json.loads(line) for line in self.out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(loaded, rows)
        self.assertEqual([r["run_id"] for r in loaded], ["a", "b", "c"])
        self.assertEqual([r["status"] for r in loaded], ["PARTIAL", "COMPLETE", "FAILED"])
        self.assertEqual(sum(r["workload_size"] for r in loaded), 12)

    def test_malformed_input_fails_with_the_line_number_and_writes_nothing(self):
        good = self.sample()[0]
        bad = copy.deepcopy(good)
        del bad["mode"]
        self.write_raw([good, bad])
        with self.assertRaises(metrics.MalformedRecord) as ctx:
            metrics.calculate(self.raw, self.out)
        self.assertIn(":2:", str(ctx.exception))
        self.assertFalse(self.out.exists())

    def test_invalid_json_empty_and_same_file_inputs_are_rejected(self):
        self.write_raw([self.sample()[0]], extra="{not json}\n")
        with self.assertRaisesRegex(metrics.MalformedRecord, r":2: invalid JSON"):
            metrics.calculate(self.raw, self.out)
        self.raw.write_text("\n\n", encoding="utf-8")
        with self.assertRaisesRegex(metrics.MalformedRecord, "no records"):
            metrics.calculate(self.raw, self.out)
        self.write_raw(self.sample())
        with self.assertRaises(ValueError):
            metrics.calculate(self.raw, self.raw)  # would overwrite the raw data
        self.assertEqual(len(self.raw.read_text(encoding="utf-8").splitlines()), 3)

    def test_main_statuses_and_messages(self):
        self.write_raw(self.sample())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            status = metrics.main(["--input", str(self.raw), "--output", str(self.out)])
        self.assertEqual(status, 0)
        self.assertIn("3 metrics record(s)", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()) as err:
            status = metrics.main(["--input", str(self.directory / "missing.jsonl"),
                                   "--output", str(self.out)])
        self.assertEqual(status, 2)
        self.assertIn("error:", err.getvalue())
        self.raw.write_text("{bad\n", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(metrics.main(["--input", str(self.raw),
                                           "--output", str(self.out)]), 2)
        self.assertIn("invalid JSON", err.getvalue())

    def test_help_and_required_arguments(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            metrics.main(["--help"])
        self.assertIn("--input", out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            metrics.main([])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
