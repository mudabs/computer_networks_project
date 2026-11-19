"""Tests for the experiment runner (small, bounded real-UDP runs on ephemeral ports)."""
import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import experiment_runner as runner
import link
from config import Config


def make_settings(directory, **overrides):
    options = dict(mode="reliable", messages=3, timeout=0.3, max_retries=2,
                   seed=7, repeats=1, output=str(Path(directory) / "raw.jsonl"))
    options.update(overrides)
    return runner.ExperimentSettings(**options)


def quiet_main(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return runner.main(argv)


def load(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


class WorkloadAndSeedTests(unittest.TestCase):
    def test_workload_is_deterministic_and_finite(self):
        first = runner.generate_workload(4, "probe")
        self.assertEqual(first, ["probe-0000", "probe-0001", "probe-0002", "probe-0003"])
        self.assertEqual(first, runner.generate_workload(4, "probe"))
        self.assertEqual(len(runner.generate_workload(1)), 1)
        self.assertNotEqual(runner.generate_workload(2, "a"), runner.generate_workload(2, "b"))

    def test_run_seeds_are_explicit_distinct_and_reproducible(self):
        seeds = [runner.derive_run_seed(42, repeat) for repeat in range(4)]
        self.assertEqual(seeds, [42, 43, 44, 45])
        self.assertEqual(seeds, [runner.derive_run_seed(42, repeat) for repeat in range(4)])
        self.assertNotEqual(runner.derive_run_seed(1, 0), runner.derive_run_seed(2, 0))


class ConfigurationTests(unittest.TestCase):
    def test_loss_and_delay_become_one_seeded_directed_rule(self):
        settings = runner.ExperimentSettings(loss=0.25, delay_ms=40, link_from="A", link_to="C")
        self.assertEqual(runner.conditions_for(settings, 99), [
            {"from": "A", "to": "C", "loss": 0.25, "delay_ms": 40, "seed": 99}])
        config = runner.build_config(settings, 99)
        self.assertEqual(config.data["conditions"][0]["seed"], 99)

    def test_unimpaired_link_has_no_rule(self):
        self.assertEqual(runner.conditions_for(runner.ExperimentSettings(), 5), [])
        self.assertEqual(runner.build_config(runner.ExperimentSettings(), 5).data["conditions"], [])

    def test_building_a_config_never_changes_the_shared_data(self):
        before = Config()
        runner.build_config(runner.ExperimentSettings(loss=0.5), 1)
        after = Config()
        self.assertEqual(before.data, after.data)
        self.assertEqual(before.route_data, after.route_data)

    def test_ephemeral_ports_replace_the_fixed_ports_in_memory_only(self):
        fixed = Config()
        config = runner.build_config(runner.ExperimentSettings(), 1)
        ports = runner.assign_ephemeral_ports(config)
        self.assertEqual(set(ports), set(fixed.data["addresses"]))
        self.assertEqual(len(set(ports.values())), len(ports))  # all distinct
        fixed_ports = {a["port"] for a in fixed.data["addresses"].values()}
        self.assertFalse(fixed_ports & set(ports.values()))
        self.assertEqual({d: a["port"] for d, a in config.data["addresses"].items()}, ports)
        self.assertEqual(Config().data["addresses"], fixed.data["addresses"])  # file untouched

    def test_route_path_follows_the_routing_tables(self):
        path = runner.route_path(Config(), "H1", "H2")
        self.assertEqual((path[0], path[-1]), ("H1", "H2"))
        self.assertEqual(path, ["H1", "A", "C", "D", "H2"])

    def test_invalid_settings_are_rejected(self):
        bad = {
            "mode": dict(mode="sometimes"), "messages": dict(messages=0),
            "loss low": dict(loss=-0.1), "loss high": dict(loss=1.5),
            "loss nan": dict(loss=float("nan")), "timeout": dict(timeout=0),
            "timeout inf": dict(timeout=float("inf")), "retries": dict(max_retries=-1),
            "delay": dict(delay_ms=-5), "seed": dict(seed=-1), "repeats": dict(repeats=0),
            "same endpoints": dict(destination="H1"), "router source": dict(source="A"),
            "unknown host": dict(destination="H9"), "missing link": dict(link_from="H1", link_to="D", loss=0.1),
            "oversized": dict(message_prefix="x" * 5000), "no output": dict(output=""),
        }
        for name, overrides in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                runner.ExperimentSettings(**overrides).validate()


class CliTests(unittest.TestCase):
    def parse_fails(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            runner.parse_args(list(argv))
        return ctx.exception.code

    def test_invalid_cli_values_exit_with_status_2(self):
        for argv in (["--messages", "0"], ["--loss", "1.5"], ["--loss", "nan"],
                     ["--timeout", "0"], ["--max-retries", "-1"], ["--repeats", "0"],
                     ["--seed", "-3"], ["--mode", "fast"], ["--messages", "many"],
                     ["--link-to", "D"], ["--destination", "H1"], ["--delay-ms", "-1"]):
            with self.subTest(argv):
                self.assertEqual(self.parse_fails(*argv), 2)

    def test_defaults_are_valid_and_visible(self):
        settings = runner.parse_args([])
        self.assertEqual(settings, runner.ExperimentSettings())
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            runner.parse_args(["--help"])
        self.assertIn("--repeats", out.getvalue())

    def test_values_are_parsed(self):
        settings = runner.parse_args(
            ["--mode", "best_effort", "--messages", "5", "--loss", "0.05", "--seed", "9",
             "--repeats", "3", "--output", "x/y.jsonl", "--delay-ms", "20"])
        self.assertEqual((settings.mode, settings.messages, settings.loss, settings.seed,
                          settings.repeats, settings.output, settings.delay_ms),
                         ("best_effort", 5, 0.05, 9, 3, "x/y.jsonl", 20.0))


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_jsonl_records_have_the_documented_shape_and_round_trip(self):
        settings = make_settings(self.directory.name, repeats=2, loss=0.0)
        written = runner.run_experiment(settings)
        loaded = load(settings.output)
        self.assertEqual(loaded, json.loads(json.dumps(written)))  # lossless round trip
        self.assertEqual(len(loaded), 2)
        for index, record in enumerate(loaded):
            for key in ("schema_version", "run_id", "repeat", "mode", "seed", "base_seed",
                        "config", "settings", "workload", "runtime", "outcome", "events",
                        "timing", "cleanup", "runner_error"):
                self.assertIn(key, record)
            self.assertEqual(record["schema_version"], runner.SCHEMA_VERSION)
            self.assertEqual((record["repeat"], record["seed"]), (index, 7 + index))
            self.assertEqual(record["mode"], "reliable")
            self.assertEqual(record["workload"]["messages"],
                             runner.generate_workload(3, "msg"))
            self.assertEqual(record["outcome"]["type"], "reliable_transfer")
            self.assertEqual(record["outcome"]["result"]["status"], "COMPLETE")
            self.assertEqual(record["settings"]["timeout"], 0.3)
            self.assertEqual(record["settings"]["max_retries"], 2)
            for part in ("devices", "links", "conditions", "routes", "path"):
                self.assertIn(part, record["config"])
            times = [event["t"] for event in record["events"]]
            self.assertTrue(record["events"] and times == sorted(times) and times[0] >= 0)
            self.assertTrue(all(e["fields"]["run_id"] == record["run_id"]
                                for e in record["events"]))
            names = {e["fields"]["event"] for e in record["events"]}
            self.assertTrue({"transfer_start", "deliver", "ack_received", "send"} <= names)
        # Ports are runtime-only and per run; the logical configuration is identical.
        self.assertEqual(loaded[0]["config"], loaded[1]["config"])
        self.assertEqual(loaded[0]["workload"], loaded[1]["workload"])

    def test_best_effort_records_per_message_outcomes(self):
        settings = make_settings(self.directory.name, mode="best_effort", messages=2)
        (record,) = runner.run_experiment(settings)
        self.assertEqual(record["outcome"]["type"], "best_effort_probes")
        self.assertEqual([m["message"] for m in record["outcome"]["messages"]],
                         record["workload"]["messages"])
        self.assertEqual({m["status"] for m in record["outcome"]["messages"]}, {"MATCHED"})
        self.assertIsNone(record["settings"]["max_retries"])  # not applicable

    def test_repeats_share_configuration_and_differ_only_in_seed(self):
        settings = make_settings(self.directory.name, repeats=3, loss=0.1, delay_ms=5)
        records = runner.run_experiment(settings)
        rules = [r["config"]["conditions"][0] for r in records]
        self.assertEqual([r["seed"] for r in rules], [7, 8, 9])
        for rule in rules:
            self.assertEqual({k: v for k, v in rule.items() if k != "seed"},
                             {"from": "H1", "to": "A", "loss": 0.1, "delay_ms": 5})
        self.assertEqual(len({json.dumps(r["workload"]) for r in records}), 1)
        self.assertEqual(len({json.dumps(r["config"]["routes"]) for r in records}), 1)
        self.assertEqual(len({r["run_id"] for r in records}), 3)

    def test_output_directory_is_created(self):
        nested = Path(self.directory.name) / "a" / "b" / "raw.jsonl"
        self.assertEqual(quiet_main(["--messages", "1", "--timeout", "0.3",
                                     "--output", str(nested)]), 0)
        self.assertEqual(len(load(nested)), 1)


class FailureAndCleanupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = str(Path(self.directory.name) / "raw.jsonl")

    def test_failed_reliable_transfer_is_a_recorded_result_with_exit_status_0(self):
        argv = ["--mode", "reliable", "--messages", "2", "--loss", "1.0", "--timeout", "0.1",
                "--max-retries", "1", "--repeats", "2", "--output", self.output]
        self.assertEqual(quiet_main(argv), 0)
        records = load(self.output)
        self.assertEqual(len(records), 2)  # the batch continued after the first FAILED run
        for record in records:
            self.assertIsNone(record["runner_error"])
            self.assertEqual(record["outcome"]["result"]["status"], "FAILED")
            self.assertEqual(record["outcome"]["result"]["sent"], 0)
            names = [e["fields"]["event"] for e in record["events"]]
            self.assertIn("failure", names)
            self.assertEqual(names.count("retransmit"), 1)
            self.assertIn("drop", names)

    def test_best_effort_timeouts_are_recorded_per_message(self):
        argv = ["--mode", "best_effort", "--messages", "2", "--loss", "1.0",
                "--timeout", "0.1", "--output", self.output]
        self.assertEqual(quiet_main(argv), 0)
        (record,) = load(self.output)
        self.assertEqual([m["status"] for m in record["outcome"]["messages"]],
                         ["TIMEOUT", "TIMEOUT"])
        self.assertEqual([m["response"] for m in record["outcome"]["messages"]], [None, None])
        self.assertIsNone(record["runner_error"])

    def test_runner_errors_are_recorded_and_exit_nonzero(self):
        with patch("experiment_runner.Router", side_effect=RuntimeError("boom")):
            status = quiet_main(["--messages", "1", "--output", self.output])
        self.assertEqual(status, 1)
        (record,) = load(self.output)
        self.assertIn("boom", record["runner_error"])
        self.assertIsNone(record["outcome"])

    def test_logging_and_threads_are_restored_after_every_run(self):
        logger = link.logger
        before = (list(logger.handlers), logger.level, logger.propagate,
                  set(threading.enumerate()))
        for argv in (["--mode", "reliable", "--messages", "2", "--timeout", "0.3"],
                     ["--mode", "reliable", "--messages", "1", "--loss", "1.0",
                      "--timeout", "0.1", "--max-retries", "0"],
                     ["--mode", "best_effort", "--messages", "1", "--timeout", "0.3",
                      "--delay-ms", "10"]):
            with self.subTest(argv):
                self.assertEqual(quiet_main(argv + ["--output", self.output]), 0)
                self.assertEqual(
                    (list(logger.handlers), logger.level, logger.propagate),
                    before[:3])
                self.assertEqual(set(threading.enumerate()), before[3])
                (record,) = load(self.output)
                self.assertEqual(record["cleanup"]["threads_alive"], 0)
                self.assertEqual(record["cleanup"]["thread_errors"], [])

    def test_handler_is_removed_even_when_the_run_raises(self):
        handlers = list(link.logger.handlers)
        with patch("experiment_runner.Router", side_effect=RuntimeError("boom")):
            runner.run_once(make_settings(self.directory.name), 0)
        self.assertEqual(list(link.logger.handlers), handlers)

    def test_experiments_do_not_use_the_fixed_ports(self):
        fixed = {a["port"] for a in Config().data["addresses"].values()}
        (record,) = runner.run_experiment(make_settings(self.directory.name, messages=1))
        self.assertFalse(fixed & set(record["runtime"]["ports"].values()))


if __name__ == "__main__":
    unittest.main()
