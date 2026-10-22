import io
import unittest
from unittest.mock import MagicMock, patch

from config import Config
from host import Host
from packet import Packet


class InteractiveHostTests(unittest.TestCase):
    @patch('host.socket.socket')
    def test_echoes_while_waiting_and_stays_open_after_matching(self, socket_mock):
        host = Host('H1', Config())
        self.addCleanup(host.close)
        terminal = io.StringIO('hello\n')
        host.receive = MagicMock(side_effect=[
            Packet('H2', 'H1', 5, 'DATA', 9, 'incoming'),
            Packet('H2', 'H1', 5, 'RESPONSE', 0, 'wrong'),
            Packet('H2', 'H1', 5, 'RESPONSE', 0, 'hello'),
        ])
        with patch('host.sys.stdin', terminal), patch('host.select.select', side_effect=[
            ([terminal], [], []),
            ([host.sock], [], []),
            ([host.sock], [], []),
            ([host.sock], [], []),
            KeyboardInterrupt,
        ]), patch('builtins.print') as output:
            with self.assertRaises(KeyboardInterrupt):
                host.interactive('H2')
        packets = [Packet.from_bytes(call.args[0]) for call in host.sock.sendto.call_args_list]
        self.assertEqual([packet.type for packet in packets], ['DATA', 'RESPONSE'])
        self.assertEqual(packets[1].id, 9)
        matches = [call for call in output.call_args_list if 'MATCHED' in str(call)]
        self.assertEqual(len(matches), 1)

    @patch('host.socket.socket')
    def test_timeout_sends_once_and_keeps_listening(self, socket_mock):
        host = Host('H1', Config())
        self.addCleanup(host.close)
        terminal = io.StringIO('hello\n')
        clock = [0.0]

        def ready(*args):
            if host.packet_id == 0:
                return [terminal], [], []
            if clock[0] == 0:
                clock[0] = 4.0
                return [], [], []
            raise KeyboardInterrupt

        with patch('host.sys.stdin', terminal), patch('host.select.select', side_effect=ready), \
                patch('host.time.monotonic', side_effect=lambda: clock[0]), \
                patch('builtins.print') as output:
            with self.assertRaises(KeyboardInterrupt):
                host.interactive('H2', timeout=3)
        host.sock.sendto.assert_called_once()
        self.assertTrue(any('TIMEOUT' in str(call) for call in output.call_args_list))

    @patch('host.socket.socket')
    def test_late_reply_is_logged_after_timeout(self, socket_mock):
        host = Host('H1', Config())
        self.addCleanup(host.close)
        terminal = io.StringIO('hello\n')
        host.receive = MagicMock(return_value=Packet('H2', 'H1', 5, 'RESPONSE', 0, 'hello'))
        clock = [0.0]
        calls = [0]

        def ready(*args):
            calls[0] += 1
            if calls[0] == 1:
                return [terminal], [], []
            if calls[0] == 2:
                clock[0] = 4.0
                return [host.sock], [], []
            raise KeyboardInterrupt

        with patch('host.sys.stdin', terminal), patch('host.select.select', side_effect=ready), \
                patch('host.time.monotonic', side_effect=lambda: clock[0]), \
                patch('builtins.print'), self.assertLogs(level='INFO') as captured:
            with self.assertRaises(KeyboardInterrupt):
                host.interactive('H2', timeout=3)
        trace = '\n'.join(captured.output)
        self.assertIn('event=timeout', trace)
        self.assertIn('event=late_response', trace)
        self.assertNotIn('event=probe_success', trace)
        host.sock.sendto.assert_called_once()
