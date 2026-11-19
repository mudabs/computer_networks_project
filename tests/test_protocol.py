"""Deterministic unit tests for the Track B stop-and-wait protocol.

No real sockets, sleeping or wall-clock time is used: the UDP socket is a mock,
``time.monotonic`` is a fake clock and ``select.select`` advances that clock.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from config import Config
from host import Host
from packet import Packet, PacketError


def ack(src="H2", dst="H1", id=0, seq=0, payload=""):
    return Packet(src, dst, 8, "ACK", id, seq, payload)


def data(id=5, seq=0, payload="hello", src="H2", dst="H1"):
    return Packet(src, dst, 8, "DATA", id, seq, payload)


def fin(id=5, seq=1, src="H2", dst="H1"):
    return Packet(src, dst, 8, "FIN", id, seq, "")


class TestAckPacketValidation(unittest.TestCase):
    def test_valid_ack_round_trips_through_bytes(self):
        original = ack(id=42, seq=1)
        self.assertEqual(Packet.from_bytes(original.to_bytes()), original)

    def test_ack_with_payload_is_rejected_by_validate_and_to_bytes(self):
        packet = ack(payload="not empty")
        with self.assertRaisesRegex(PacketError, "ACK payload must be empty"):
            packet.validate()
        with self.assertRaises(PacketError):
            packet.to_bytes()

    def test_ack_with_payload_is_rejected_when_parsed(self):
        raw = json.dumps(
            dict(src="H2", dst="H1", ttl=8, type="ACK", id=1, seq=0, payload="x")
        ).encode()
        with self.assertRaises(PacketError):
            Packet.from_bytes(raw)

    def _fields(self):
        return dict(src="H2", dst="H1", ttl=8, type="ACK", id=1, seq=0, payload="")

    def test_missing_field_is_rejected(self):
        for name in self._fields():
            with self.subTest(missing=name):
                fields = self._fields()
                del fields[name]
                with self.assertRaisesRegex(PacketError, "Missing or unexpected"):
                    Packet.from_bytes(json.dumps(fields).encode())

    def test_unexpected_field_is_rejected(self):
        fields = self._fields()
        fields["extra"] = 1
        with self.assertRaisesRegex(PacketError, "Missing or unexpected"):
            Packet.from_bytes(json.dumps(fields).encode())


class HostHarness(unittest.TestCase):
    """Host H1 (neighbor A) with mock socket, fake clock and scripted input."""

    def setUp(self):
        self.now = 0.0
        self.script = []  # incoming packets (or None) consumed by receive()
        self.select_calls = 0

        for target, kwargs in (
            ("host.socket.socket", {}),
            ("host.time.monotonic", {"side_effect": lambda: self.now}),
            ("host.select.select", {"side_effect": self._select}),
            ("host.event", {}),
        ):
            patcher = patch(target, **kwargs)
            mock = patcher.start()
            self.addCleanup(patcher.stop)
            setattr(self, target.split(".")[-1] + "_mock", mock)

        self.event = self.event_mock
        self.sock = self.socket_mock.return_value
        self.host = Host("H1", Config(), timeout=1.0, max_retries=2, mode="reliable")
        self.addCleanup(self.host.close)
        self.host.receive = MagicMock(side_effect=self._receive)

    def _select(self, rlist, wlist, xlist, timeout=None):
        self.select_calls += 1
        if self.select_calls > 200:
            raise AssertionError("select loop did not terminate")
        if self.script:
            return rlist, [], []
        self.now += timeout  # nothing arrives: the whole wait elapses
        return [], [], []

    def _receive(self):
        return self.script.pop(0) if self.script else None

    def sent(self):
        return [call.args[0] for call in self.sock.sendto.call_args_list]

    def sent_packets(self):
        return [Packet.from_bytes(raw) for raw in self.sent()]

    def event_names(self):
        return [call.args[2] for call in self.event.call_args_list]


class TestAckMatching(HostHarness):
    def setUp(self):
        super().setUp()
        self.packet = Packet("H1", "H2", 8, "DATA", 7, 3, "payload")

    def test_matching_ack_completes_send_without_retransmission(self):
        self.script = [ack(id=7, seq=3)]

        self.assertTrue(self.host._send_reliably(self.packet))

        self.assertEqual(self.sent(), [self.packet.to_bytes()])
        self.assertIn("ack_received", self.event_names())
        self.assertNotIn("retransmit", self.event_names())

    def test_mismatched_ack_does_not_complete_send(self):
        mismatches = {
            "src": ack(src="H3", id=7, seq=3),
            "dst": ack(dst="H3", id=7, seq=3),
            "id": ack(id=8, seq=3),
            "seq": ack(id=7, seq=2),
        }
        for field, bad_ack in mismatches.items():
            with self.subTest(field=field):
                self.sock.sendto.reset_mock()
                self.event.reset_mock()
                self.script = [bad_ack]

                self.assertFalse(self.host._send_reliably(self.packet))

                # Initial send plus every allowed retry: the ACK was ignored.
                self.assertEqual(self.sock.sendto.call_count, 1 + 2)

    def test_mismatched_ack_is_not_treated_as_the_awaited_ack(self):
        """The send loop must hand a mismatched ACK to normal processing
        instead of consuming it as the awaited ACK."""
        bad_ack = ack(id=7, seq=2)
        self.script = [bad_ack]

        with patch.object(self.host, "_process") as process:
            self.assertFalse(self.host._send_reliably(self.packet))

        process.assert_called_once_with(bad_ack)

    def test_non_ack_packet_with_matching_fields_does_not_complete_send(self):
        response = Packet("H2", "H1", 8, "RESPONSE", 7, 3, "payload")
        self.script = [response]

        self.assertFalse(self.host._send_reliably(self.packet))
        self.assertNotIn("ack_received", self.event_names())

    def test_mismatched_ack_is_ignored_and_later_matching_ack_completes(self):
        self.script = [ack(id=7, seq=2), ack(id=7, seq=3)]

        self.assertTrue(self.host._send_reliably(self.packet))
        self.assertEqual(self.sock.sendto.call_count, 1)

    def test_mismatched_ack_does_not_complete_transfer(self):
        self.script = [ack(id=0, seq=1)]  # wrong seq for first message

        result = self.host.start_transfer(["only"], "H2")

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["sent"], 0)


class TestReceiverSequenceHandling(HostHarness):
    def test_expected_data_is_delivered_once_and_advances_state(self):
        self.assertTrue(self.host._process(data(id=5, seq=0)))

        self.assertEqual(self.host.expected[("H2", 5)], 1)
        self.assertIn("deliver", self.event_names())
        acks = [p for p in self.sent_packets() if p.type == "ACK"]
        self.assertEqual(len(acks), 1)
        self.assertEqual(
            (acks[0].src, acks[0].dst, acks[0].id, acks[0].seq, acks[0].payload),
            ("H1", "H2", 5, 0, ""),
        )

    def test_in_order_sequence_advances_one_step_at_a_time(self):
        for seq in range(3):
            self.assertTrue(self.host._process(data(id=5, seq=seq)))
        self.assertEqual(self.host.expected[("H2", 5)], 3)

    def test_duplicate_data_is_not_delivered_twice(self):
        self.assertTrue(self.host._process(data(seq=0)))
        self.event.reset_mock()

        self.assertFalse(self.host._process(data(seq=0)))

        self.assertNotIn("deliver", self.event_names())
        self.assertIn("duplicate", self.event_names())
        self.assertEqual(self.host.expected[("H2", 5)], 1)

    def test_duplicate_data_causes_ack_to_be_resent(self):
        self.host._process(data(seq=0))
        self.sock.sendto.reset_mock()

        self.host._process(data(seq=0))

        packets = self.sent_packets()
        self.assertEqual([p.type for p in packets], ["ACK"])
        self.assertEqual((packets[0].id, packets[0].seq), (5, 0))
        self.assertEqual((packets[0].src, packets[0].dst), ("H1", "H2"))

    def test_older_seq_is_acked_again_without_state_change(self):
        for seq in range(3):
            self.host._process(data(seq=seq))
        self.sock.sendto.reset_mock()

        self.assertFalse(self.host._process(data(seq=1)))

        self.assertEqual(self.host.expected[("H2", 5)], 3)
        self.assertEqual([(p.type, p.seq) for p in self.sent_packets()], [("ACK", 1)])

    def test_out_of_order_data_is_not_delivered_or_acked(self):
        self.assertFalse(self.host._process(data(seq=1)))

        self.assertNotIn(("H2", 5), self.host.expected)
        self.assertNotIn("deliver", self.event_names())
        self.sock.sendto.assert_not_called()

    def test_out_of_order_data_does_not_advance_existing_state(self):
        self.host._process(data(seq=0))
        self.sock.sendto.reset_mock()

        self.assertFalse(self.host._process(data(seq=5)))

        self.assertEqual(self.host.expected[("H2", 5)], 1)
        self.sock.sendto.assert_not_called()
        # The missing next packet is still accepted afterwards.
        self.assertTrue(self.host._process(data(seq=1)))

    def test_transfers_are_tracked_independently_by_source_and_id(self):
        self.host._process(data(id=5, seq=0))
        self.assertFalse(self.host._process(data(id=6, seq=1)))
        self.assertTrue(self.host._process(data(id=6, seq=0)))
        self.assertEqual(self.host.expected[("H2", 5)], 1)
        self.assertEqual(self.host.expected[("H2", 6)], 1)

    def test_fin_with_expected_sequence_completes_and_is_acked(self):
        self.host._process(data(seq=0))
        self.sock.sendto.reset_mock()

        self.assertTrue(self.host._process(fin(seq=1)))

        self.assertIn(("H2", 5), self.host.completed)
        self.assertEqual([(p.type, p.seq) for p in self.sent_packets()], [("ACK", 1)])

    def test_duplicate_fin_is_acked_again_and_not_completed_twice(self):
        self.host._process(data(seq=0))
        self.host._process(fin(seq=1))
        self.sock.sendto.reset_mock()

        self.assertFalse(self.host._process(fin(seq=1)))

        self.assertEqual([(p.type, p.seq) for p in self.sent_packets()], [("ACK", 1)])

    def test_fin_with_unexpected_sequence_is_dropped(self):
        self.host._process(data(seq=0))
        self.sock.sendto.reset_mock()

        self.assertFalse(self.host._process(fin(seq=3)))

        self.assertNotIn(("H2", 5), self.host.completed)
        self.sock.sendto.assert_not_called()

    def test_received_ack_is_only_logged(self):
        self.assertFalse(self.host._process(ack(src="H2", dst="H1")))
        self.sock.sendto.assert_not_called()

    def test_duplicate_data_does_not_emit_response(self):
        self.host._process(data(seq=0))
        self.sock.sendto.reset_mock()
        self.host._process(data(seq=0))
        self.assertNotIn("RESPONSE", [p.type for p in self.sent_packets()])


class TestTimeoutAndRetry(HostHarness):
    def setUp(self):
        super().setUp()
        self.data_packet = Packet("H1", "H2", 8, "DATA", 7, 0, "payload")
        self.fin_packet = Packet("H1", "H2", 8, "FIN", 7, 1, "")

    def test_data_is_resent_unchanged_after_timeout(self):
        self.script = []

        self.assertFalse(self.host._send_reliably(self.data_packet))

        expected = self.data_packet.to_bytes()
        self.assertEqual(self.sent(), [expected] * 3)

    def test_fin_is_resent_unchanged_after_timeout(self):
        self.assertFalse(self.host._send_reliably(self.fin_packet))

        expected = self.fin_packet.to_bytes()
        self.assertEqual(self.sent(), [expected] * 3)

    def test_retransmissions_happen_one_timeout_apart(self):
        times = []
        self.sock.sendto.side_effect = lambda *a, **k: times.append(self.now)

        self.host._send_reliably(self.data_packet)

        self.assertEqual(times, [0.0, 1.0, 2.0])
        self.assertEqual(self.now, 3.0)  # gave up one timeout after last send

    def test_retries_stop_at_max_retries_and_failure_is_returned(self):
        self.assertIs(self.host._send_reliably(self.data_packet), False)

        self.assertEqual(self.sock.sendto.call_count, 1 + self.host.max_retries)
        retransmits = [n for n in self.event_names() if n == "retransmit"]
        self.assertEqual(len(retransmits), self.host.max_retries)

    def test_zero_max_retries_sends_once_then_fails(self):
        self.host.max_retries = 0

        self.assertFalse(self.host._send_reliably(self.data_packet))

        self.assertEqual(self.sock.sendto.call_count, 1)
        self.assertNotIn("retransmit", self.event_names())

    def test_matching_ack_after_one_retransmission_succeeds(self):
        # First wait times out (script empty), then the ACK shows up.
        original_select = self.select_mock.side_effect
        calls = []

        def select(rlist, wlist, xlist, timeout=None):
            calls.append(timeout)
            if len(calls) == 2:
                self.script.append(ack(id=7, seq=0))
            return original_select(rlist, wlist, xlist, timeout)

        self.select_mock.side_effect = select

        self.assertTrue(self.host._send_reliably(self.data_packet))
        self.assertEqual(self.sock.sendto.call_count, 2)

    def test_immediate_ack_means_no_retransmission(self):
        self.script = [ack(id=7, seq=0)]

        self.assertTrue(self.host._send_reliably(self.data_packet))

        self.assertEqual(self.sock.sendto.call_count, 1)

    def test_receive_returning_none_does_not_complete_or_hang(self):
        self.script = [None, None]
        self.assertFalse(self.host._send_reliably(self.data_packet))

    def _acks_for(self, *seqs, transfer_id=0):
        return [ack(id=transfer_id, seq=seq) for seq in seqs]

    def test_transfer_fails_when_first_message_reaches_retry_limit(self):
        result = self.host.start_transfer(["a", "b"], "H2")

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["sent"], 0)
        self.assertEqual(self.sock.sendto.call_count, 1 + self.host.max_retries)
        self.assertTrue(all(p.type == "DATA" and p.seq == 0 for p in self.sent_packets()))
        failure = [c for c in self.event.call_args_list if c.args[2] == "failure"]
        self.assertEqual(len(failure), 1)
        self.assertEqual(failure[0].kwargs["reason"], "retry_limit")

    def test_transfer_fails_when_later_message_reaches_retry_limit(self):
        self.script = self._acks_for(0)

        result = self.host.start_transfer(["a", "b"], "H2")

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["sent"], 1)
        packets = self.sent_packets()
        self.assertEqual([p.seq for p in packets], [0, 1, 1, 1])
        self.assertNotIn("FIN", [p.type for p in packets])

    def test_transfer_fails_when_fin_reaches_retry_limit(self):
        self.script = self._acks_for(0, 1)

        result = self.host.start_transfer(["a", "b"], "H2")

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["sent"], 2)
        packets = self.sent_packets()
        self.assertEqual([p.type for p in packets], ["DATA", "DATA"] + ["FIN"] * 3)
        self.assertEqual({p.seq for p in packets if p.type == "FIN"}, {2})
        failure = [c for c in self.event.call_args_list if c.args[2] == "failure"]
        self.assertEqual(failure[0].kwargs["reason"], "fin_retry_limit")
        self.assertNotIn("transfer_complete", self.event_names())

    def test_successful_transfer_completes_without_retransmission(self):
        self.script = self._acks_for(0, 1, 2)

        result = self.host.start_transfer(["a", "b"], "H2")

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["sent"], 2)
        self.assertEqual(
            [(p.type, p.seq) for p in self.sent_packets()],
            [("DATA", 0), ("DATA", 1), ("FIN", 2)],
        )
        self.assertNotIn("retransmit", self.event_names())

    def test_start_transfer_returns_instead_of_looping_forever(self):
        # select_calls is capped in the harness; reaching here means it ended.
        self.host.start_transfer(["a"], "H2")
        self.assertLess(self.select_calls, 200)


if __name__ == "__main__":
    unittest.main()
