"""Controller and input tests for the GUI reliable-transfer controls.

No Tk window, sockets, sleeps, or subprocesses: devices are mocks and the
transfer worker is synchronised with events.
"""
import copy
import threading
import unittest
from unittest.mock import MagicMock, patch

from config import Config
from gui import MAX_TRANSFER_MESSAGES, NetworkController, parse_transfer_settings


class ControllerTestCase(unittest.TestCase):
    def setUp(self):
        base = Config()
        self.network = copy.deepcopy(base.data)
        self.routes = copy.deepcopy(base.route_data)
        self.hosts = {}       # device id -> latest mock Host
        self.host_calls = []  # (device id, keyword arguments) per Host created

        def make_host(device_id, config, **kwargs):
            host = MagicMock(name=f"Host-{device_id}")
            host.id = device_id
            self.hosts[device_id] = host
            options = {k: v for k, v in kwargs.items() if k != "run_id"}
            self.host_calls.append((device_id, options))
            return host

        host_patch = patch("gui.Host", side_effect=make_host)
        router_patch = patch("gui.Router")
        for patcher in (host_patch, router_patch):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.controller = NetworkController()
        self.addCleanup(self.controller.stop)

    def start(self, **kwargs):
        self.controller.start(self.network, self.routes, "H1", "H2", **kwargs)


class ReliableSettingsTests(ControllerTestCase):
    def test_reliable_mode_timeout_and_retries_reach_every_host(self):
        self.start(mode="reliable", timeout=0.4, max_retries=5)
        self.assertEqual({d for d, _ in self.host_calls}, {"H1", "H2"})
        for _, kwargs in self.host_calls:
            self.assertEqual(
                kwargs, {"mode": "reliable", "timeout": 0.4, "max_retries": 5}
            )

    def test_best_effort_defaults_are_unchanged(self):
        self.start()
        for _, kwargs in self.host_calls:
            self.assertEqual(kwargs, {})
        self.assertEqual(self.controller.mode, "best_effort")

    def test_only_destination_host_runs_as_receiver(self):
        self.start(mode="reliable", timeout=0.4, max_retries=1)
        for thread in self.controller.threads:
            thread.join(2)
        self.hosts["H2"].run.assert_called_once()
        self.hosts["H1"].run.assert_not_called()

    def test_unknown_mode_and_same_endpoints_are_rejected(self):
        with self.assertRaises(ValueError):
            self.start(mode="bogus")
        with self.assertRaises(ValueError):
            self.controller.start(self.network, self.routes, "H1", "H1")


class TransferLifecycleTests(ControllerTestCase):
    def test_transfer_runs_on_source_and_result_is_polled(self):
        self.start(mode="reliable", timeout=0.4, max_retries=1)
        result = {"status": "COMPLETE", "count": 2, "sent": 2, "id": 0}
        self.hosts["H1"].start_transfer.return_value = result

        self.controller.begin_transfer(["hi", "hi"])
        self.controller.transfer_thread.join(2)

        self.hosts["H1"].start_transfer.assert_called_once_with(["hi", "hi"], "H2")
        self.assertEqual(self.controller.poll_transfer(), result)
        self.assertIsNone(self.controller.poll_transfer())

    def test_begin_transfer_requires_a_running_reliable_network(self):
        with self.assertRaises(RuntimeError):
            self.controller.begin_transfer(["x"])
        self.start()  # best effort
        with self.assertRaises(RuntimeError):
            self.controller.begin_transfer(["x"])

    def test_stop_ends_active_transfer_quietly_and_closes_devices(self):
        self.start(mode="reliable", timeout=0.4, max_retries=1)
        blocked = threading.Event()
        released = threading.Event()

        def blocking_transfer(messages, destination):
            blocked.set()
            released.wait(5)
            raise OSError("socket closed")  # what a closed socket does mid-wait

        self.hosts["H1"].start_transfer.side_effect = blocking_transfer
        for host in self.hosts.values():
            host.close.side_effect = released.set

        self.controller.begin_transfer(["x"])
        self.assertTrue(blocked.wait(5))
        with patch("gui.logging.getLogger") as get_logger:
            self.controller.stop()

        self.assertFalse(self.controller.running)
        self.assertIsNone(self.controller.transfer_thread)
        self.assertEqual(self.controller.threads, [])
        for host in self.hosts.values():
            host.close.assert_called()
        self.assertIsNone(self.controller.poll_transfer())  # nothing stale surfaces
        get_logger.return_value.exception.assert_not_called()

    def test_stop_is_idempotent(self):
        self.start(mode="reliable", timeout=0.4, max_retries=1)
        self.controller.stop()
        self.controller.stop()
        self.assertFalse(self.controller.running)

    def test_worker_error_is_reported_not_raised(self):
        self.start(mode="reliable", timeout=0.4, max_retries=1)
        boom = ValueError("bad transfer")
        self.hosts["H1"].start_transfer.side_effect = boom
        self.controller.begin_transfer(["x"])
        self.controller.transfer_thread.join(2)
        outcome = self.controller.poll_transfer()
        self.assertEqual(outcome["status"], "ERROR")
        self.assertIs(outcome["error"], boom)


class ProbeAfterReliableTests(ControllerTestCase):
    def test_probe_restores_best_effort_network_first(self):
        self.start(mode="reliable", timeout=0.4, max_retries=1)
        self.host_calls.clear()

        real_start = self.controller.start

        def start_and_prime(*args, **kwargs):
            real_start(*args, **kwargs)
            self.hosts["H1"].probe.return_value = "pong"  # fresh best-effort source

        with patch.object(self.controller, "start", side_effect=start_and_prime) as start:
            outcome = self.controller.probe("ping", 1.0)

        start.assert_called_once_with(self.network, self.routes, "H1", "H2")
        self.assertEqual(outcome, ("MATCHED", "pong"))
        self.assertEqual(self.controller.mode, "best_effort")
        for _, kwargs in self.host_calls:
            self.assertEqual(kwargs, {})
        self.hosts["H1"].probe.assert_called_once_with("H2", "ping", timeout=1.0)

    def test_probe_in_best_effort_does_not_restart(self):
        self.start()
        self.hosts["H1"].probe.return_value = None
        with patch.object(self.controller, "start") as start:
            outcome = self.controller.probe("ping", 1.0)
        start.assert_not_called()
        self.assertEqual(outcome, ("TIMEOUT", None))


class InputValidationTests(unittest.TestCase):
    def parse(self, source="H1", destination="H2", message="hi", count="3",
              timeout="0.5", retries="2"):
        return parse_transfer_settings(source, destination, message, count, timeout, retries)

    def test_valid_inputs_repeat_the_message(self):
        self.assertEqual(
            self.parse(count=" 3 "),
            {"messages": ["hi"] * 3, "timeout": 0.5, "max_retries": 2},
        )

    def test_count_limit_is_gui_safe_and_inclusive(self):
        self.assertEqual(MAX_TRANSFER_MESSAGES, 100)
        self.assertEqual(
            len(self.parse(count=str(MAX_TRANSFER_MESSAGES))["messages"]),
            MAX_TRANSFER_MESSAGES,
        )

    def test_zero_retries_is_allowed(self):
        self.assertEqual(self.parse(retries="0")["max_retries"], 0)

    def test_invalid_inputs_are_rejected_with_a_clear_message(self):
        cases = {
            "identical endpoints": ({"destination": "H1"}, "different"),
            "empty message": ({"message": ""}, "message"),
            "zero count": ({"count": "0"}, "Message count"),
            "negative count": ({"count": "-2"}, "Message count"),
            "fractional count": ({"count": "2.5"}, "Message count"),
            "text count": ({"count": "many"}, "Message count"),
            "huge count": ({"count": str(MAX_TRANSFER_MESSAGES + 1)}, "Message count"),
            "zero timeout": ({"timeout": "0"}, "Timeout"),
            "negative timeout": ({"timeout": "-1"}, "Timeout"),
            "nan timeout": ({"timeout": "nan"}, "Timeout"),
            "inf timeout": ({"timeout": "inf"}, "Timeout"),
            "text timeout": ({"timeout": "soon"}, "Timeout"),
            "negative retries": ({"retries": "-1"}, "Max retries"),
            "fractional retries": ({"retries": "1.5"}, "Max retries"),
            "text retries": ({"retries": "x"}, "Max retries"),
            "oversized message": ({"message": "x" * 5000}, "cannot be sent"),
        }
        for name, (kwargs, fragment) in cases.items():
            with self.subTest(name), self.assertRaises(ValueError) as ctx:
                self.parse(**kwargs)
            self.assertIn(fragment, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
