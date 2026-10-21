"""Real UDP exchanges on isolated ephemeral ports; no external devices needed."""
import select
import socket
import threading
import unittest

from config import Config
from host import Host
from router import Router


class NetworkExchangeTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
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

    def exchange(self, source, destination):
        for router_id in self.config.route_data:
            self.devices.append(Router(router_id, self.config))
        receiver = Host(destination, self.config)
        self.devices.append(receiver)
        for device in self.devices:
            thread = threading.Thread(target=self.serve, args=(device,), daemon=True)
            thread.start()
            self.threads.append(thread)
        sender = Host(source, self.config)
        self.devices.append(sender)
        self.assertEqual(sender.probe(destination, 'hello', timeout=1), 'hello')
        self.assertEqual(sender.packet_id, 1)  # Exactly one transmission.

    def test_h1_to_h2(self):
        self.exchange('H1', 'H2')

    def test_h2_to_h1(self):
        self.exchange('H2', 'H1')

    def test_lower_path(self):
        self.config.route_data['A']['H2'] = 'C'
        self.config.route_data['D']['H1'] = 'C'
        self.exchange('H1', 'H2')
