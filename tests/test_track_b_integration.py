"""Checks that the Track B packet/host/router/CLI changes stay compatible with
the existing best-effort behavior. Mock sockets only; no real network."""

import unittest
from unittest.mock import MagicMock, patch

from config import Config
from host import Host
from packet import Packet
from router import Router


def data(id=5, seq=0, payload="hello"):
    return Packet("H2", "H1", 8, "DATA", id, seq, payload)


class TestBestEffortCompatibility(unittest.TestCase):
    def setUp(self):
        for target in ("host.socket.socket", "host.event"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = Host("H1", Config())
        self.addCleanup(self.host.close)
        self.sock = self.host.sock

    def sent_packets(self):
        return [Packet.from_bytes(c.args[0]) for c in self.sock.sendto.call_args_list]

    def test_data_is_echoed_with_response_and_not_acked(self):
        self.assertTrue(self.host._process(data(id=5, seq=0)))
        packets = self.sent_packets()
        self.assertEqual([p.type for p in packets], ["RESPONSE"])
        self.assertEqual((packets[0].id, packets[0].payload), (5, "hello"))

    def test_fin_and_ack_are_ignored(self):
        fin = Packet("H2", "H1", 8, "FIN", 5, 1, "")
        ack = Packet("H2", "H1", 8, "ACK", 5, 0, "")
        self.assertFalse(self.host._process(fin))
        self.assertFalse(self.host._process(ack))
        self.sock.sendto.assert_not_called()

    def test_send_uses_seq_zero_and_advances_packet_id(self):
        self.host.send("hi", "H2")
        (packet,) = self.sent_packets()
        self.assertEqual((packet.type, packet.seq, packet.id), ("DATA", 0, 0))
        self.assertEqual(self.host.packet_id, 1)


class TestRouterForwardsNewFields(unittest.TestCase):
    def test_router_decrements_ttl_and_preserves_seq_and_type(self):
        with patch("router.socket.socket"), patch("router.event"):
            router = Router("A", Config())
            self.addCleanup(router.close)
            router._send_packet = MagicMock()
            packets = (
                data(seq=4),
                Packet("H2", "H1", 8, "FIN", 5, 2, ""),
                Packet("H2", "H1", 8, "ACK", 5, 9, ""),
            )
            for packet in packets:
                router._send_packet.reset_mock()
                router.forward(packet.to_bytes())
                raw, _ = router._send_packet.call_args.args
                forwarded = Packet.from_bytes(raw)
                self.assertEqual(forwarded.ttl, packet.ttl - 1)
                self.assertEqual(
                    (forwarded.type, forwarded.id, forwarded.seq, forwarded.payload),
                    (packet.type, packet.id, packet.seq, packet.payload),
                )


class TestHeadlessLaunch(unittest.TestCase):
    def test_run_all_passes_headless_to_every_child(self):
        import main

        with patch("main.subprocess.Popen") as popen, patch(
            "main.time.sleep", side_effect=KeyboardInterrupt
        ):
            popen.return_value.poll.return_value = None
            main.run_all(["H1", "A"], "run", "INFO")

        self.assertEqual(popen.call_count, 2)
        for call in popen.call_args_list:
            self.assertIn("--headless", call.args[0])

    def _run_host(self, isatty, headless):
        import main

        with patch("main.Host") as host_cls, patch(
            "main.sys.stdin.isatty", return_value=isatty
        ):
            device = host_cls.return_value
            main.run_device("H1", Config(), "run", headless=headless)
        return device

    def test_headless_host_never_enters_interactive_mode(self):
        device = self._run_host(isatty=True, headless=True)
        device.interactive.assert_not_called()
        device.run.assert_called_once()

    def test_reliable_host_is_receive_only_even_on_a_tty(self):
        import main

        with patch("main.Host") as host_cls, patch(
            "main.sys.stdin.isatty", return_value=True
        ):
            device = host_cls.return_value
            main.run_device("H1", Config(), "run", mode="reliable")
        device.interactive.assert_not_called()
        device.run.assert_called_once()

    def test_normal_tty_host_stays_interactive(self):
        device = self._run_host(isatty=True, headless=False)
        device.interactive.assert_called_once()
        device.run.assert_not_called()


class TestModeCombinations(unittest.TestCase):
    def _parse(self, *argv):
        import main

        with patch("sys.argv", ["main.py", *argv]):
            return main.parse_args(Config())

    def test_transfer_requires_reliable_mode(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as ctx:
            self._parse("H1", "--transfer", "H2", "--messages", "a")
        self.assertEqual(ctx.exception.code, 2)

    def test_probe_is_rejected_in_reliable_mode(self):
        with patch("sys.stderr") as err, self.assertRaises(SystemExit):
            self._parse("H1", "--mode", "reliable", "--probe", "H2")
        written = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertIn("use --transfer", written)

    def test_valid_combinations_still_parse(self):
        args = self._parse("H1", "--mode", "reliable", "--transfer", "H2", "--messages", "a")
        self.assertEqual(args.transfer, "H2")
        args = self._parse("H1", "--probe", "H2")
        self.assertEqual(args.mode, "best_effort")


if __name__ == "__main__":
    unittest.main()
