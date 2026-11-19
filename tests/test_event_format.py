"""The log event format must expose seq so the GUI can label packets."""
import unittest
from unittest.mock import MagicMock

from config import Config
from gui import parse_event
from link import LinkSender, event
from packet import Packet


class EventFormatTests(unittest.TestCase):
    def logged(self, *args, **kwargs):
        with self.assertLogs("link", level="INFO") as captured:
            event("run", "H1", *args, **kwargs)
        return parse_event(captured.records[0].getMessage())

    def test_packet_events_include_seq(self):
        fields = self.logged("send", Packet("H1", "H2", 8, "DATA", 5, 3, "x"), "A")
        self.assertEqual((fields["seq"], fields["msg_id"], fields["type"]), ("3", "5", "DATA"))

    def test_events_without_a_packet_use_a_placeholder(self):
        self.assertEqual(self.logged("start")["seq"], "-")

    def test_explicit_seq_detail_is_not_duplicated(self):
        with self.assertLogs("link", level="INFO") as captured:
            event("run", "H1", "retransmit", Packet("H1", "H2", 8, "DATA", 5, 3, "x"),
                  "A", seq=3, retries=1)
        message = captured.records[0].getMessage()
        self.assertEqual(message.count("seq="), 1)
        self.assertIn("retries=1", message)

    def test_link_send_event_carries_seq_without_changing_packet_bytes(self):
        sock = MagicMock()
        sender = LinkSender(sock, Config(), "A", "run")
        self.addCleanup(sender.close)
        data = Packet("H1", "H2", 7, "DATA", 1, 4, "hello").to_bytes()
        with self.assertLogs("link", level="INFO") as captured:
            sender.send(data, "B")
        fields = parse_event(captured.records[-1].getMessage())
        self.assertEqual((fields["event"], fields["seq"]), ("send", "4"))
        self.assertEqual(sock.sendto.call_args.args[0], data)  # bytes untouched


if __name__ == "__main__":
    unittest.main()
