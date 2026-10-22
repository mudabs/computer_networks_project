import threading
import unittest
from unittest.mock import MagicMock

from config import Config
from link import LinkSender
from packet import Packet
from routes_fixture import use_upper_path


class LinkTests(unittest.TestCase):
    def make_sender(self, loss=0, delay_ms=0, seed=42):
        config = Config()
        config.data['conditions'] = [dict(
            **{'from': 'A', 'to': 'B'}, loss=loss, delay_ms=delay_ms, seed=seed)]
        config.validate_conditions()
        sock = MagicMock()
        sender = LinkSender(sock, config, 'A', 'test')
        self.addCleanup(sender.close)
        return sender, sock

    def test_delay_does_not_block_other_links(self):
        sender, sock = self.make_sender(delay_ms=100)
        delivered = threading.Event()
        sock.sendto.side_effect = lambda *args: delivered.set()
        data = Packet('H1', 'H2', 7, 'DATA', 1, 'hello').to_bytes()
        sender.send(data, 'B')
        sock.sendto.assert_not_called()
        sender.send(data, 'C')
        self.assertEqual(sock.sendto.call_args.args[1][1], 6003)
        delivered.clear()
        self.assertTrue(delivered.wait(1))
        self.assertEqual(sock.sendto.call_args.args[1][1], 6002)

    def test_seed_repeats_loss_for_both_packet_types(self):
        first, first_sock = self.make_sender(loss=0.5)
        second, second_sock = self.make_sender(loss=0.5)
        decisions = []
        for sender, sock in ((first, first_sock), (second, second_sock)):
            sequence = []
            for number in range(20):
                before = sock.sendto.call_count
                sender.send(Packet('H1', 'H2', 7, ('DATA', 'RESPONSE')[number % 2],
                                   number, 'hello').to_bytes(), 'B')
                sequence.append(sock.sendto.call_count > before)
            decisions.append(sequence)
        self.assertEqual(*decisions)
        self.assertIn(True, decisions[0])
        self.assertIn(False, decisions[0])

    def test_invalid_conditions_rejected(self):
        for change in ({'loss': 1.1}, {'delay_ms': -1}, {'seed': '42'},
                       {'to': 'D'}, {'loss': float('nan')}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                config = Config()
                rule = {'from': 'A', 'to': 'B', 'loss': 0, 'delay_ms': 0, 'seed': 42}
                rule.update(change)
                config.data['conditions'] = [rule]
                config.validate_conditions()

    def test_router_drop_logs_include_packet_context(self):
        from router import Router
        from unittest.mock import patch
        with patch('router.socket.socket'):
            router = Router('A', use_upper_path(Config()), run_id='drop-test')
            self.addCleanup(router.close)
            with self.assertLogs(level='INFO') as captured:
                router.forward(Packet('H1', 'H2', 1, 'DATA', 7, 'hello').to_bytes())
                router.forward(Packet('H1', 'unknown', 8, 'DATA', 8, 'hello').to_bytes())
            trace = '\n'.join(captured.output)
            self.assertIn('reason=hop_limit_exhausted', trace)
            self.assertIn('reason=no_route', trace)
            self.assertIn('msg_id=7 type=DATA src=H1 dst=H2 next_hop=B hop_limit=1', trace)
            router.sock.sendto.assert_not_called()
