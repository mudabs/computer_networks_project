"""Loss-free reliable transfer over real UDP on isolated ephemeral ports.

H1 -> A -> B -> D -> H2 with no injected loss: the transfer must complete and
the receiver must deliver every message exactly once, in sending order.
"""
import select
import socket
import threading
import types
import unittest

from config import Config
from host import Host
from packet import Packet
from router import Router

try:
    from routes_fixture import use_upper_path
except ImportError:  # run as ``python -m unittest tests.<module>``
    from tests.routes_fixture import use_upper_path

MESSAGES = ["one", "two", "three"]


class LossFreeOrderedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.config = use_upper_path(Config())
        self.config.data["conditions"] = []  # packet loss disabled

        reservations = []
        for address in self.config.data["addresses"].values():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", 0))
            address["port"] = sock.getsockname()[1]
            reservations.append(sock)
        for sock in reservations:
            sock.close()

        self.devices = []
        self.threads = []
        self.stop = threading.Event()
        self.addCleanup(self.cleanup_network)

    def cleanup_network(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(2)
        for device in self.devices:
            device.close()

    def serve(self, device):
        while not self.stop.is_set():
            if not select.select([device.sock], [], [], 0.01)[0]:
                continue
            if isinstance(device, Router):
                device.forward(device._receive_packet())
            else:
                packet = device.receive()
                if packet is not None:
                    device._process(packet)  # reliable receive-only, no echo

    def start(self, device):
        self.devices.append(device)
        thread = threading.Thread(target=self.serve, args=(device,), daemon=True)
        thread.start()
        self.threads.append(thread)

    def start_network(self):
        """Start routers A, B, D and a recording reliable H2 receiver."""
        for router_id in ("A", "B", "D"):
            self.start(Router(router_id, self.config, run_id="ordered"))

        receiver = Host("H2", self.config, run_id="ordered", mode="reliable")
        record = types.SimpleNamespace(
            receiver=receiver,
            seen=[],       # (seq, payload) of every DATA packet that reached H2
            delivered=[],  # payloads H2 accepted
            finished=threading.Event(),
        )
        handle_data, handle_fin = receiver._handle_data, receiver._handle_fin

        def record_data(packet):
            record.seen.append((packet.seq, packet.payload))
            accepted = handle_data(packet)
            if accepted:
                record.delivered.append(packet.payload)
            return accepted

        def record_fin(packet):
            completed = handle_fin(packet)
            if completed:
                record.finished.set()
            return completed

        receiver._handle_data = record_data
        receiver._handle_fin = record_fin
        self.start(receiver)
        return record

    def test_messages_are_delivered_exactly_once_and_in_order(self):
        record = self.start_network()
        receiver, seen, delivered = record.receiver, record.seen, record.delivered
        finished = record.finished

        sender = Host("H1", self.config, run_id="ordered", mode="reliable")
        self.devices.append(sender)

        result = sender.start_transfer(MESSAGES, "H2")

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["sent"], len(MESSAGES))
        self.assertTrue(finished.wait(5), "receiver never completed the transfer")
        self.assertEqual(delivered, MESSAGES)  # exactly once, in sending order
        # Each DATA packet arrived once, so nothing was retransmitted.
        self.assertEqual(seen, list(enumerate(MESSAGES)))
        self.assertIn(("H1", result["id"]), receiver.completed)
        self.assertEqual(receiver.expected[("H1", result["id"])], len(MESSAGES))

    def test_dropped_data_packet_is_retransmitted_and_delivered_once(self):
        record = self.start_network()
        receiver, seen, delivered = record.receiver, record.seen, record.delivered

        sender = Host(
            "H1", self.config, run_id="ordered", mode="reliable",
            timeout=0.2, max_retries=3,
        )
        self.devices.append(sender)

        transmissions = []  # (seq, serialized bytes) of every DATA H1 sends
        dropped = []
        link_send = sender.sender.send

        def lossy_send(data, next_hop):
            packet = Packet.from_bytes(data)
            if packet.type == "DATA":
                transmissions.append((packet.seq, data))
                if packet.seq == 1 and not dropped:
                    dropped.append(data)  # deliberate one-time drop
                    return
            link_send(data, next_hop)

        sender.sender.send = lossy_send

        result = sender.start_transfer(MESSAGES, "H2")

        self.assertEqual(len(dropped), 1, "the deliberate drop did not occur")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["sent"], len(MESSAGES))
        self.assertEqual([seq for seq, _ in transmissions], [0, 1, 1, 2])
        first, retransmission = (data for seq, data in transmissions if seq == 1)
        self.assertEqual(first, retransmission)  # identical serialized bytes
        self.assertEqual(dropped[0], retransmission)
        self.assertTrue(record.finished.wait(5), "receiver never completed")
        self.assertEqual(delivered, MESSAGES)  # once each, in order, no duplicates
        # The dropped copy never reached H2, so it saw each DATA exactly once.
        self.assertEqual(seen, list(enumerate(MESSAGES)))
        self.assertIn(("H1", result["id"]), receiver.completed)


if __name__ == "__main__":
    unittest.main()
