"""Real UDP exchanges on isolated ephemeral ports; no external devices needed."""
import select
import socket
import threading
import unittest

from config import Config
from host import Host
from router import Router
from routes_fixture import use_upper_path


class NetworkExchangeTests(unittest.TestCase):
    def setUp(self):
        self.config = use_upper_path(Config())
        self.reservations = []
        for address in self.config.data['addresses'].values():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(('127.0.0.1', 0))
            address['port'] = sock.getsockname()[1]
            self.reservations.append(sock)
        for sock in self.reservations:
            sock.close()
        self.devices = []
        self.stop = threading.Event()
        self.threads = []
        self.addCleanup(self.cleanup_network)

    def cleanup_network(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(2)
        for device in self.devices:
            device.close()

    def serve(self, device):
        while not self.stop.is_set():
            if select.select([device.sock], [], [], 0.01)[0]:
                if isinstance(device, Router):
                    device.forward(device._receive_packet())
                else:
                    packet = device.receive()
                    if packet is not None:
                        device.echo(packet)

    def exchange(self, source, destination, expected="hello", timeout=1):
        for router_id in self.config.route_data:
            self.devices.append(Router(router_id, self.config, run_id="trace"))
        receiver = Host(destination, self.config, run_id="trace")
        self.devices.append(receiver)
        for device in self.devices:
            thread = threading.Thread(target=self.serve, args=(device,), daemon=True)
            thread.start()
            self.threads.append(thread)
        sender = Host(source, self.config, run_id="trace")
        self.devices.append(sender)
        self.assertEqual(sender.probe(destination, 'hello', timeout=timeout), expected)
        self.assertEqual(sender.packet_id, 1)  # Exactly one transmission.

    def test_h1_to_h2(self):
        self.exchange('H1', 'H2')

    def test_h2_to_h1(self):
        self.exchange('H2', 'H1')

    def test_lower_path(self):
        self.config.route_data['A']['H2'] = 'C'
        self.config.route_data['D']['H1'] = 'C'
        self.exchange('H1', 'H2')

    def test_request_loss_times_out_without_retry(self):
        self.config.data['conditions'] = [dict(
            **{'from': 'A', 'to': 'B'}, loss=1, delay_ms=0, seed=42)]
        self.exchange('H1', 'H2', expected=None, timeout=0.05)

    def test_response_loss_times_out_without_retry(self):
        self.config.data['conditions'] = [dict(
            **{'from': 'B', 'to': 'A'}, loss=1, delay_ms=0, seed=42)]
        self.exchange('H1', 'H2', expected=None, timeout=0.05)

    def test_delayed_exchange_and_complete_trace(self):
        self.config.data['conditions'] = [dict(
            **{'from': 'A', 'to': 'B'}, loss=0, delay_ms=20, seed=42)]
        with self.assertLogs(level='INFO') as captured:
            self.exchange('H1', 'H2')
        trace = '\n'.join(captured.output)
        for device in ('A', 'B', 'D'):
            for kind in ('DATA', 'RESPONSE'):
                self.assertTrue(any(f'device={device} event=forward' in line
                                    and f'type={kind}' in line
                                    for line in captured.output))
        self.assertIn('event=delay', trace)
        self.assertIn('seed=42', trace)
