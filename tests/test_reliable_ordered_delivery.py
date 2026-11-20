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

    def start_routers(self):
        for router_id in ("A", "B", "D"):
            self.start(Router(router_id, self.config, run_id="ordered"))

    def start_network(self):
        """Start routers A, B, D and a recording reliable H2 receiver."""
        self.start_routers()

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

        def lossy_send(data, next_hop, **marks):
            packet = Packet.from_bytes(data)
            if packet.type == "DATA":
                transmissions.append((packet.seq, data))
                if packet.seq == 1 and not dropped:
                    dropped.append(data)  # deliberate one-time drop
                    return
            link_send(data, next_hop, **marks)

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

    def test_dropped_final_ack_is_reacked_for_duplicate_data(self):
        record = self.start_network()
        receiver, seen, delivered = record.receiver, record.seen, record.delivered
        last = len(MESSAGES) - 1

        sender = Host(
            "H1", self.config, run_id="ordered", mode="reliable",
            timeout=0.2, max_retries=3,
        )
        self.devices.append(sender)

        transmissions = []  # (seq, serialized bytes) of every DATA H1 sends
        sender_send = sender.sender.send

        def recording_send(data, next_hop, **marks):
            packet = Packet.from_bytes(data)
            if packet.type == "DATA":
                transmissions.append((packet.seq, data))
            sender_send(data, next_hop, **marks)

        sender.sender.send = recording_send

        final_acks = []  # serialized bytes of every H2 ACK for the final DATA
        dropped = []
        receiver_send = receiver.sender.send

        def lossy_ack_send(data, next_hop, **marks):
            packet = Packet.from_bytes(data)
            if packet.type == "ACK" and packet.seq == last:
                final_acks.append(data)
                if not dropped:
                    dropped.append(data)  # deliberate one-time drop
                    return
            receiver_send(data, next_hop, **marks)

        receiver.sender.send = lossy_ack_send

        result = sender.start_transfer(MESSAGES, "H2")

        self.assertEqual(len(dropped), 1, "the deliberate ACK drop did not occur")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["sent"], len(MESSAGES))
        self.assertEqual([seq for seq, _ in transmissions], [0, 1, 2, 2])
        first, retransmission = (data for seq, data in transmissions if seq == last)
        self.assertEqual(first, retransmission)  # identical serialized bytes
        self.assertTrue(record.finished.wait(5), "receiver never completed")
        # H2 saw the final DATA twice but delivered it only once.
        self.assertEqual([seq for seq, _ in seen], [0, 1, 2, 2])
        self.assertEqual(delivered, MESSAGES)
        # First ACK copy dropped, second is the re-ACK for the duplicate DATA.
        self.assertEqual(len(final_acks), 2)
        self.assertEqual(final_acks[0], final_acks[1])
        self.assertEqual(dropped[0], final_acks[0])
        self.assertIn(("H1", result["id"]), receiver.completed)

    def test_unreachable_destination_fails_after_bounded_retries(self):
        self.start_routers()

        # No H2 host: a plain socket owns H2's address but never reads or
        # replies, so routers' sends succeed on every platform.
        address = self.config.get_address("H2")
        black_hole = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(black_hole.close)
        black_hole.bind((address["ip"], address["port"]))

        sender = Host(
            "H1", self.config, run_id="ordered", mode="reliable",
            timeout=0.1, max_retries=2,
        )
        self.devices.append(sender)

        transmissions = []  # (type, seq, serialized bytes) of every DATA/FIN
        link_send = sender.sender.send

        def recording_send(data, next_hop, **marks):
            packet = Packet.from_bytes(data)
            if packet.type in ("DATA", "FIN"):
                transmissions.append((packet.type, packet.seq, data))
            link_send(data, next_hop, **marks)

        sender.sender.send = recording_send

        result = sender.start_transfer(["one"], "H2")  # returns, never hangs

        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["sent"], 0)
        # Initial send plus max_retries retransmissions, all the same DATA seq 0.
        self.assertEqual(len(transmissions), 3)
        self.assertEqual({(kind, seq) for kind, seq, _ in transmissions}, {("DATA", 0)})
        self.assertEqual(len({data for _, _, data in transmissions}), 1)
        self.assertFalse([t for t in transmissions if t[0] == "FIN"])

    def start_running_receiver(self, grace_period):
        """A reliable H2 driven by Host.run() itself; ``exited`` is set when run() returns."""
        self.start_routers()
        receiver = Host("H2", self.config, run_id="ordered", mode="reliable",
                        timeout=0.2, max_retries=3, grace_period=grace_period)
        self.devices.append(receiver)
        exited = threading.Event()

        def serve_receiver():
            receiver.run()
            exited.set()

        thread = threading.Thread(target=serve_receiver, daemon=True)
        thread.start()
        self.threads.append(thread)
        return receiver, exited

    def test_run_exits_after_the_grace_period_once_the_transfer_completes(self):
        receiver, exited = self.start_running_receiver(grace_period=0.4)
        sender = Host("H1", self.config, run_id="ordered", mode="reliable",
                      timeout=0.2, max_retries=3)
        self.devices.append(sender)

        result = sender.start_transfer(MESSAGES, "H2")

        self.assertEqual(result["status"], "COMPLETE")  # sender behavior unchanged
        self.assertTrue(exited.wait(5), "receiver run() never exited")  # bounded, no sleep
        self.assertIn(("H1", result["id"]), receiver.completed)
        self.assertIsNotNone(receiver.grace_deadline)
        self.assertNotEqual(receiver.sock.fileno(), -1)  # run() did not close the socket

    def test_dropped_final_ack_is_reacked_during_grace_by_run(self):
        receiver, exited = self.start_running_receiver(grace_period=0.6)
        final_acks, dropped = [], []
        receiver_send = receiver.sender.send

        def lossy_ack_send(data, next_hop, **marks):
            packet = Packet.from_bytes(data)
            if packet.type == "ACK" and packet.seq == len(MESSAGES):  # the FIN's ACK
                final_acks.append(data)
                if not dropped:
                    dropped.append(data)  # deliberate one-time drop
                    return
            receiver_send(data, next_hop, **marks)

        receiver.sender.send = lossy_ack_send
        sender = Host("H1", self.config, run_id="ordered", mode="reliable",
                      timeout=0.2, max_retries=3)
        self.devices.append(sender)

        result = sender.start_transfer(MESSAGES, "H2")

        self.assertEqual(len(dropped), 1)
        self.assertEqual(result["status"], "COMPLETE")  # the re-ACK arrived in the grace period
        self.assertEqual(len(final_acks), 2)            # dropped copy + re-ACK
        self.assertEqual(final_acks[0], final_acks[1])
        self.assertTrue(exited.wait(5), "receiver run() never exited")

if __name__ == "__main__":
    unittest.main()
