"""Bounded receiver grace period after an accepted FIN (deterministic: fake clock).

The harness host is a reliable receiver H1 (timeout 1.0, max_retries 2, so the
default grace period is 3.0) with a mock socket and a patched monotonic clock.
``run()`` is driven by a scripted ``receive()`` that advances the clock the way
an idle socket timeout would; nothing sleeps.
"""
import math
import unittest

from config import Config
from host import Host
from packet import Packet

try:
    from test_protocol import HostHarness, ack, data, fin
except ImportError:  # run as ``python -m unittest tests.<module>``
    from tests.test_protocol import HostHarness, ack, data, fin


class Stop(Exception):
    """Raised by the scripted receive() to end a run() that is still looping."""


class GraceHarness(HostHarness):
    def feed(self, schedule, stop_at=None, tick=0.2, limit=2000):
        """receive() hands out (time, packet) items once the clock reaches them and
        otherwise advances the clock by ``tick`` (an idle socket timeout)."""
        pending, calls = list(schedule), [0]

        def receive():
            calls[0] += 1
            if calls[0] > limit:
                raise AssertionError("run() did not stop")
            if stop_at is not None and self.now >= stop_at:
                raise Stop()
            if pending and pending[0][0] <= self.now:
                return pending.pop(0)[1]
            self.now += tick
            return None

        self.host.receive = receive

    def ack_types(self):
        return [(p.type, p.seq) for p in self.sent_packets()]


class GraceStartTests(GraceHarness):
    def test_deadline_starts_only_after_a_valid_fin_and_its_ack(self):
        self.assertIsNone(self.host.grace_deadline)
        self.host._handle_data(data(id=5, seq=0))            # ordinary DATA
        self.host._handle_fin(fin(id=5, seq=9))               # wrong FIN sequence: rejected
        self.assertIsNone(self.host.grace_deadline)

        self.now = 10.0
        self.assertTrue(self.host._handle_fin(fin(id=5, seq=1)))  # valid FIN accepted
        self.assertEqual(self.host.grace_deadline, 10.0 + 3.0)
        self.assertEqual(self.ack_types()[-1], ("ACK", 1))     # the FIN ACK was sent

    def test_a_duplicate_fin_does_not_extend_the_deadline(self):
        self.host._handle_data(data(id=5, seq=0))
        self.now = 10.0
        self.host._handle_fin(fin(id=5, seq=1))
        self.now = 12.0
        self.assertFalse(self.host._handle_fin(fin(id=5, seq=1)))
        self.assertEqual(self.host.grace_deadline, 13.0)

    def test_default_and_explicit_grace_periods(self):
        self.assertEqual(self.host.grace_period, 1.0 * (2 + 1))
        other = Host("H1", Config(), timeout=0.5, max_retries=3, mode="reliable")
        self.addCleanup(other.close)
        self.assertEqual(other.grace_period, 0.5 * 4)
        explicit = Host("H1", Config(), timeout=0.5, mode="reliable", grace_period=0.25)
        self.addCleanup(explicit.close)
        self.assertEqual(explicit.grace_period, 0.25)

    def test_invalid_grace_periods_are_rejected(self):
        for bad in (0, -1, 0.0, math.inf, math.nan, "1", True, []):
            with self.subTest(bad), self.assertRaises(ValueError):
                Host("H1", Config(), mode="reliable", grace_period=bad)


class GraceRunTests(GraceHarness):
    def test_duplicate_final_data_and_fin_are_reacked_during_the_grace_period(self):
        self.feed([
            (0.0, data(id=5, seq=0)), (0.0, fin(id=5, seq=1)),
            (1.0, data(id=5, seq=0)),        # duplicate final DATA
            (2.0, fin(id=5, seq=1)),         # duplicate FIN
        ])
        self.host.run()
        self.assertEqual(self.ack_types(), [("ACK", 0), ("ACK", 1), ("ACK", 0), ("ACK", 1)])
        self.assertEqual(self.event_names().count("duplicate"), 2)
        self.assertEqual(self.host.expected[("H2", 5)], 1)  # still delivered exactly once

    def test_receiver_exits_after_the_grace_deadline_and_leaves_the_socket_open(self):
        self.feed([(0.0, data(id=5, seq=0)), (0.0, fin(id=5, seq=1))])
        self.assertIsNone(self.host.run())  # returns normally instead of looping forever
        self.assertGreaterEqual(self.now, 3.0)
        self.assertLess(self.now, 3.0 + 0.2 + 1e-9)  # within one idle-socket tick
        self.assertIn("grace_expired", self.event_names())
        self.sock.close.assert_not_called()  # the owner closes the host, not run()

    def test_receiver_does_not_exit_before_the_grace_period_elapses(self):
        self.feed([(0.0, data(id=5, seq=0)), (0.0, fin(id=5, seq=1)),
                   (2.8, data(id=5, seq=0))], stop_at=2.9, tick=0.05)
        with self.assertRaises(Stop):       # still serving when the script ends it
            self.host.run()
        self.assertLess(self.now, self.host.grace_deadline)
        self.assertNotIn("grace_expired", self.event_names())
        self.assertEqual(self.ack_types()[-1], ("ACK", 0))  # the late duplicate was re-ACKed

    def test_receiver_without_a_fin_keeps_running(self):
        self.feed([(0.0, data(id=5, seq=0))], stop_at=100.0)
        with self.assertRaises(Stop):
            self.host.run()
        self.assertIsNone(self.host.grace_deadline)
        self.assertNotIn("grace_expired", self.event_names())

    def test_a_rejected_fin_does_not_start_a_grace_period(self):
        self.feed([(0.0, fin(id=5, seq=4))], stop_at=100.0)  # no DATA, wrong sequence
        with self.assertRaises(Stop):
            self.host.run()
        self.assertIsNone(self.host.grace_deadline)

    def test_explicit_grace_period_controls_the_exit_time(self):
        self.host.grace_period = 0.5
        self.feed([(0.0, data(id=5, seq=0)), (0.0, fin(id=5, seq=1))], tick=0.1)
        self.host.run()
        self.assertGreaterEqual(self.now, 0.5)
        self.assertLess(self.now, 0.5 + 0.1 + 1e-9)


class BestEffortUnchangedTests(GraceHarness):
    def setUp(self):
        super().setUp()
        self.host.close()
        self.host = Host("H1", Config(), timeout=1.0, max_retries=2, mode="best_effort")
        self.addCleanup(self.host.close)

    def test_best_effort_never_starts_a_grace_period_or_exits(self):
        self.feed([(0.0, data(id=5, seq=0, payload="hi")), (0.5, fin(id=5, seq=1)),
                   (1.0, ack(id=5, seq=0))], stop_at=100.0)
        with self.assertRaises(Stop):
            self.host.run()
        self.assertIsNone(self.host.grace_deadline)
        packets = self.sent_packets()
        self.assertEqual([p.type for p in packets], ["RESPONSE"])  # echo only
        self.assertEqual(packets[0].payload, "hi")
        self.assertNotIn("grace_expired", self.event_names())


if __name__ == "__main__":
    unittest.main()
