"""retransmit/duplicate marks travel Host -> LinkSender -> final log event.

The marks annotate log events only: packet bytes, sends and retry behavior are
unchanged, and a mark must stay attached to its own packet through link loss
and delay.
"""
import threading
import unittest
from unittest.mock import MagicMock

from config import Config
from gui import parse_event
from link import LinkSender
from packet import Packet

try:
    from test_protocol import HostHarness
except ImportError:  # run as ``python -m unittest tests.<module>``
    from tests.test_protocol import HostHarness


def events(captured, name):
    parsed = (parse_event(record.getMessage()) for record in captured.records)
    return [fields for fields in parsed if fields.get("event") == name]


class LinkMarkTests(unittest.TestCase):
    def make_sender(self, loss=0, delay_ms=0):
        config = Config()
        config.data["conditions"] = [dict(
            **{"from": "A", "to": "B"}, loss=loss, delay_ms=delay_ms, seed=42)]
        config.validate_conditions()
        sock = MagicMock()
        sender = LinkSender(sock, config, "A", "test")
        self.addCleanup(sender.close)
        return sender, sock

    @staticmethod
    def packet(msg_id, seq=0):
        return Packet("H1", "H2", 7, "DATA", msg_id, seq, "x").to_bytes()

    def test_unmarked_send_has_no_mark_fields(self):
        sender, _ = self.make_sender()
        with self.assertLogs("link", "INFO") as captured:
            sender.send(self.packet(1), "C")
        (sent,) = events(captured, "send")
        self.assertNotIn("retransmit", sent)
        self.assertNotIn("duplicate", sent)

    def test_immediate_send_carries_marks_and_bytes_are_untouched(self):
        sender, sock = self.make_sender()
        data = self.packet(1)
        with self.assertLogs("link", "INFO") as captured:
            sender.send(data, "C", retransmit=True)
            sender.send(data, "C", duplicate=True)
        first, second = events(captured, "send")
        self.assertEqual((first.get("retransmit"), first.get("duplicate")), ("true", None))
        self.assertEqual((second.get("retransmit"), second.get("duplicate")), (None, "true"))
        self.assertEqual([call.args[0] for call in sock.sendto.call_args_list], [data, data])

    def test_marks_survive_a_link_delay_and_stay_with_their_own_packet(self):
        sender, sock = self.make_sender(delay_ms=20)
        delivered = threading.Event()
        count = []

        def on_send(*_args):
            count.append(1)
            if len(count) == 3:
                delivered.set()

        sock.sendto.side_effect = on_send
        with self.assertLogs("link", "INFO") as captured:
            sender.send(self.packet(1), "B")                    # original, delayed
            sender.send(self.packet(1), "B", retransmit=True)   # retry, delayed
            sender.send(self.packet(2), "B", duplicate=True)    # other packet's re-ACK
            self.assertTrue(delivered.wait(5))
        sent = events(captured, "send")
        self.assertEqual(
            [(f["msg_id"], f.get("retransmit"), f.get("duplicate")) for f in sent],
            [("1", None, None), ("1", "true", None), ("2", None, "true")])
        self.assertEqual(len(events(captured, "delay")), 3)

    def test_marks_are_kept_on_a_lost_packet_and_do_not_leak(self):
        sender, sock = self.make_sender(loss=1.0)
        with self.assertLogs("link", "INFO") as captured:
            sender.send(self.packet(1), "B", retransmit=True)
            sender.send(self.packet(2), "B")
        marked, plain = events(captured, "drop")
        self.assertEqual((marked["reason"], marked.get("retransmit")), ("injected_loss", "true"))
        self.assertNotIn("retransmit", plain)
        sock.sendto.assert_not_called()

    def test_marks_are_kept_when_shutdown_discards_a_delayed_packet(self):
        sender, _ = self.make_sender(delay_ms=60000)
        sender.send(self.packet(1), "B", duplicate=True)
        with self.assertLogs("link", "INFO") as captured:
            sender.close()
        (dropped,) = events(captured, "drop")
        self.assertEqual((dropped["reason"], dropped.get("duplicate")), ("shutdown", "true"))


class HostMarkTests(HostHarness):
    def link_events(self, name, action):
        with self.assertLogs("link", "INFO") as captured:
            action()
        return events(captured, name)

    def test_only_retries_are_marked_and_bytes_never_change(self):
        packet = Packet("H1", "H2", 8, "DATA", 7, 3, "payload")
        result = []
        sends = self.link_events(
            "send", lambda: result.append(self.host._send_reliably(packet)))

        self.assertEqual(result, [False])  # retry behavior unchanged: 1 + 2 retries
        self.assertEqual([s.get("retransmit") for s in sends], [None, "true", "true"])
        self.assertTrue(all("duplicate" not in s for s in sends))
        self.assertEqual(self.sent(), [packet.to_bytes()] * 3)

    def test_ack_for_duplicate_data_is_marked_but_the_first_ack_is_not(self):
        data = Packet("H2", "H1", 8, "DATA", 5, 0, "hello")
        sends = self.link_events("send", lambda: (
            self.host._handle_data(data), self.host._handle_data(data)))
        self.assertEqual([s["type"] for s in sends], ["ACK", "ACK"])
        self.assertEqual([s.get("duplicate") for s in sends], [None, "true"])
        self.assertTrue(all("retransmit" not in s for s in sends))
        first, second = self.sent()
        self.assertEqual(first, second)  # the re-ACK is byte-identical

    def test_ack_for_duplicate_fin_is_marked(self):
        fin = Packet("H2", "H1", 8, "FIN", 5, 0, "")
        sends = self.link_events("send", lambda: (
            self.host._handle_fin(fin), self.host._handle_fin(fin)))
        self.assertEqual([s.get("duplicate") for s in sends], [None, "true"])

    def test_out_of_order_and_fresh_data_are_never_marked(self):
        sends = self.link_events("send", lambda: (
            self.host._handle_data(Packet("H2", "H1", 8, "DATA", 5, 0, "a")),
            self.host._handle_data(Packet("H2", "H1", 8, "DATA", 5, 1, "b")),
            self.host._handle_data(Packet("H2", "H1", 8, "DATA", 5, 9, "late"))))
        self.assertEqual(len(sends), 2)  # the out-of-order packet gets no ACK
        self.assertTrue(all("duplicate" not in s and "retransmit" not in s for s in sends))


if __name__ == "__main__":
    unittest.main()
