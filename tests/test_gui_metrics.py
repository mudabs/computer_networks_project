"""Headless tests for transfer metrics and reliable demo scenarios (no Tk window)."""
import copy
import random
import unittest

from config import Config
from gui import SCENARIO_LOSS, SCENARIOS, TransferMetrics, build_scenario


def ev(name, run_id="run-1", **fields):
    return {"event": name, "run_id": run_id, **fields}


def deliver(seq, msg_id="7", run_id="run-1", type_="DATA", src="H1"):
    return ev("deliver", run_id, device="H2", src=src, msg_id=msg_id, type=type_, seq=str(seq))


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.metrics = TransferMetrics()
        self.metrics.reset("run-1")

    def test_progress_follows_transfer_start_and_deliver_events(self):
        self.assertTrue(self.metrics.apply(ev("transfer_start", transfer_id="7", count="8")))
        self.assertEqual((self.metrics.delivered, self.metrics.total), (0, 8))
        self.metrics.apply(deliver(0))
        self.metrics.apply(deliver(1))
        self.assertEqual(self.metrics.delivered, 2)
        self.assertTrue(self.metrics.summary().startswith("Delivered: 2/8"))

    def test_retry_duplicate_and_failure_counters(self):
        for name in ("retransmit", "retransmit", "retransmit", "duplicate", "failure"):
            self.assertTrue(self.metrics.apply(ev(name, seq="1")))
        self.assertEqual(
            (self.metrics.retries, self.metrics.duplicates, self.metrics.failures), (3, 1, 1))
        summary = self.metrics.summary()
        for text in ("Retries: 3", "Duplicates: 1", "Failures: 1"):
            self.assertIn(text, summary)

    def test_a_delivery_is_never_counted_twice(self):
        self.assertTrue(self.metrics.apply(deliver(0)))
        self.assertFalse(self.metrics.apply(deliver(0)))   # same packet logged again
        self.assertTrue(self.metrics.apply(deliver(0, msg_id="8")))  # another transfer
        self.assertEqual(self.metrics.delivered, 2)

    def test_duplicate_events_do_not_add_deliveries(self):
        self.metrics.apply(deliver(0))
        self.metrics.apply(ev("duplicate", type="DATA", seq="0"))
        self.assertEqual((self.metrics.delivered, self.metrics.duplicates), (1, 1))

    def test_only_data_deliveries_count(self):
        self.assertFalse(self.metrics.apply(deliver(3, type_="FIN")))
        self.assertFalse(self.metrics.apply(deliver(3, type_="ACK")))
        self.assertEqual(self.metrics.delivered, 0)

    def test_stale_run_ids_are_ignored(self):
        self.metrics.apply(ev("transfer_start", count="4"))
        for fields in (deliver(0, run_id="gui-old"), ev("retransmit", "gui-old"),
                       ev("duplicate", "gui-old"), ev("failure", "gui-old"),
                       ev("transfer_start", "gui-old", count="99")):
            self.assertFalse(self.metrics.apply(fields))
        self.assertEqual(
            (self.metrics.delivered, self.metrics.total, self.metrics.retries,
             self.metrics.duplicates, self.metrics.failures), (0, 4, 0, 0, 0))

    def test_events_without_a_run_id_never_count(self):
        self.assertFalse(self.metrics.apply({"event": "retransmit"}))
        fresh = TransferMetrics()  # no run yet
        self.assertFalse(fresh.apply(ev("retransmit", "run-1")))

    def test_reset_clears_everything_and_switches_runs(self):
        self.metrics.apply(ev("transfer_start", count="4"))
        self.metrics.apply(deliver(0))
        self.metrics.apply(ev("retransmit"))
        self.metrics.apply(ev("duplicate"))
        self.metrics.apply(ev("failure"))

        self.metrics.reset("run-2", total=5)

        self.assertEqual(
            (self.metrics.delivered, self.metrics.total, self.metrics.retries,
             self.metrics.duplicates, self.metrics.failures), (0, 5, 0, 0, 0))
        self.assertFalse(self.metrics.apply(ev("retransmit", "run-1")))  # old run now stale
        self.assertTrue(self.metrics.apply(ev("retransmit", "run-2")))
        self.assertTrue(self.metrics.apply(deliver(0, run_id="run-2")))  # counted again

    def test_unrelated_events_and_bad_counts_change_nothing(self):
        self.assertFalse(self.metrics.apply(ev("send", device="H1")))
        self.assertFalse(self.metrics.apply(ev("transfer_start", count="many")))
        self.assertEqual(self.metrics.total, 0)


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        base = Config()
        self.network = copy.deepcopy(base.data)
        self.routes = copy.deepcopy(base.route_data)
        self.network_before = copy.deepcopy(self.network)
        self.routes_before = copy.deepcopy(self.routes)

    def plan(self, name, source="H1", destination="H2"):
        return build_scenario(name, self.network, self.routes, source, destination)

    def test_normal_has_no_loss_or_delay_and_untouched_routes(self):
        plan = self.plan("normal")
        self.assertEqual(plan["network_data"]["conditions"], [])
        self.assertEqual(plan["route_data"], self.routes_before)

    def test_normal_restores_after_a_modified_state(self):
        # Scenarios always start from the pristine data, so "normal" undoes both
        # injected conditions and removed routes.
        self.plan("unreachable")
        self.plan("dropped_data")
        plan = self.plan("normal")
        self.assertEqual(plan["network_data"]["conditions"], [])
        self.assertEqual(plan["route_data"], self.routes_before)

    def test_dropped_data_loses_packets_on_the_sources_first_hop(self):
        plan = self.plan("dropped_data")
        self.assertEqual(plan["network_data"]["conditions"], [
            {"from": "H1", "to": "A", "loss": SCENARIO_LOSS, "delay_ms": 0, "seed": 1}])
        self.assertEqual(plan["route_data"], self.routes_before)

    def test_dropped_ack_loses_packets_on_the_destinations_first_hop(self):
        plan = self.plan("dropped_ack")
        self.assertEqual(plan["network_data"]["conditions"], [
            {"from": "H2", "to": "D", "loss": SCENARIO_LOSS, "delay_ms": 0, "seed": 2}])

    def test_scenarios_follow_the_selected_endpoints(self):
        data = self.plan("dropped_data", "H2", "H1")["network_data"]["conditions"][0]
        ack = self.plan("dropped_ack", "H2", "H1")["network_data"]["conditions"][0]
        self.assertEqual((data["from"], data["to"]), ("H2", "D"))
        self.assertEqual((ack["from"], ack["to"]), ("H1", "A"))

    def test_unreachable_removes_the_destination_route_from_the_first_router_only(self):
        plan = self.plan("unreachable")
        self.assertNotIn("H2", plan["route_data"]["A"])
        self.assertEqual(plan["route_data"]["A"]["H1"], self.routes_before["A"]["H1"])
        for router in ("B", "C", "D"):
            self.assertEqual(plan["route_data"][router], self.routes_before[router])
        self.assertEqual(plan["network_data"]["conditions"], [])

    def test_unreachable_preserves_the_original_route_data(self):
        plan = self.plan("unreachable")
        self.assertEqual(self.routes, self.routes_before)  # input not mutated
        self.assertEqual(self.network, self.network_before)
        self.assertIn("H2", self.routes["A"])
        plan["route_data"]["A"]["H2"] = "B"  # the plan is a copy, not shared state
        self.assertEqual(self.routes["A"], self.routes_before["A"])

    def test_plans_are_independent_copies_and_reproducible(self):
        first, second = self.plan("dropped_ack"), self.plan("dropped_ack")
        self.assertEqual(first, second)
        first["network_data"]["conditions"].clear()
        self.assertEqual(len(self.plan("dropped_ack")["network_data"]["conditions"]), 1)
        self.assertEqual(self.network, self.network_before)

    def test_every_scenario_builds_a_valid_bounded_configuration(self):
        for name, settings in SCENARIOS.items():
            with self.subTest(name):
                plan = self.plan(name)
                Config(network_data=plan["network_data"], route_data=plan["route_data"])
                self.assertGreater(plan["count"], 0)
                self.assertGreater(plan["timeout"], 0)
                self.assertGreaterEqual(plan["max_retries"], 0)
                self.assertTrue(plan["label"])
                # worst-case duration of the (failing) unreachable run stays short
                self.assertLess(plan["timeout"] * (plan["max_retries"] + 1), 3)

    def test_bad_requests_are_rejected(self):
        with self.assertRaises(ValueError):
            self.plan("nope")
        with self.assertRaises(ValueError):
            self.plan("normal", "H1", "H1")

    def test_seeded_loss_sequences_give_a_deterministic_completing_run(self):
        """Replays the stop-and-wait send sequence against each seed's RNG."""
        def drops(seed, packets, retries):
            rng, lost = random.Random(seed), 0
            for _ in range(packets):
                for _attempt in range(retries + 1):
                    if rng.random() < SCENARIO_LOSS:
                        lost += 1
                    else:
                        break
                else:
                    return None  # a packet ran out of retries
            return lost

        for name in ("dropped_data", "dropped_ack"):
            settings = SCENARIOS[name]
            packets = settings["count"] + 1  # messages + FIN
            lost = drops(settings["seed"], packets, settings["max_retries"])
            self.assertEqual(lost, 2, name)  # visible loss, and it completes
            self.assertEqual(drops(settings["seed"], packets, settings["max_retries"]), lost)


if __name__ == "__main__":
    unittest.main()
