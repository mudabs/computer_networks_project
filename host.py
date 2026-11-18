import math
import select
import sys
import socket
import time

from config import Config
from link import LinkSender, event
from packet import MAX_SIZE, Packet, PacketError


class Host:
    def __init__(
        self, host_id, config: Config, run_id="default", timeout=1.0, max_retries=3
    ):

        if config.get_device(host_id)["type"] != "host":
            raise ValueError(f"'{host_id}' is not a host")

        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")

        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be non-negative")

        self.id = host_id
        self.config = config
        self.address = config.get_address(host_id)
        self.packet_id = 0
        self.run_id = run_id

        # Reliable transfer state
        self.seq = 0
        self.expected = {}
        self.pending = None
        self.timeout = timeout
        self.max_retries = max_retries
        self.failed = False

        neighbors = config.get_neighbors(host_id)

        if len(neighbors) != 1:
            raise ValueError("Host must have exactly one neighbor")

        self.neighbor = neighbors[0]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            self.sock.bind((self.address["ip"], self.address["port"]))
        except OSError:
            self.sock.close()
            raise

        self.sender = LinkSender(self.sock, config, host_id, run_id)

        event(run_id, host_id, "start", next_hop=self.neighbor)

    def _send_packet(self, data):
        self.sender.send(data, self.neighbor)

    # Reliable sending

    def send(self, data, dst):
        if self.failed:
            raise RuntimeError("Sender failed; restart host")

        if self.pending is not None:
            raise RuntimeError("Waiting for previous ACK")

        packet = Packet(self.id, dst, 8, "DATA", self.packet_id, self.seq, data)

        encoded = packet.to_bytes()
        self.packet_id = (self.packet_id + 1) % (2**32)

        self.pending = {
            "packet": packet,
            "retries": 0,
            "deadline": time.monotonic() + self.timeout,
        }

        self._send_packet(encoded)
        return packet

    def _handle_ack(self, packet):
        if self.pending is None:
            return

        request = self.pending["packet"]

        if (
            packet.src == request.dst
            and packet.dst == request.src
            and packet.id == request.id
            and packet.seq == request.seq
        ):
            self.pending = None
            self.seq = 1 - self.seq

    def _retry(self):
        if self.pending is None:
            return

        if time.monotonic() < self.pending["deadline"]:
            return

        request = self.pending["packet"]

        if self.pending["retries"] >= self.max_retries:
            event(
                self.run_id,
                self.id,
                "timeout",
                request,
                self.neighbor,
                reason="retry_limit",
            )

            self.pending = None
            self.failed = True
            return

        self.pending["retries"] += 1

        event(
            self.run_id,
            self.id,
            "retransmit",
            request,
            self.neighbor,
            retries=self.pending["retries"],
        )

        self._send_packet(request.to_bytes())
        self.pending["deadline"] = time.monotonic() + self.timeout

    # Receiving

    def _receive_packet(self):
        data, _ = self.sock.recvfrom(MAX_SIZE + 1)

        if len(data) > MAX_SIZE:
            event(self.run_id, self.id, "drop", reason="oversized_datagram")
            return None

        return data

    def receive(self):
        data = self._receive_packet()

        if data is None:
            return None

        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            event(self.run_id, self.id, "drop", reason="invalid_packet")
            return None

        if packet.dst != self.id:
            event(self.run_id, self.id, "drop", packet, reason="wrong_destination")
            return None

        event(self.run_id, self.id, "receive", packet)
        return packet

    def _handle_data(self, packet):
        expected = self.expected.get(packet.src, 0)

        # ACK both new packets and duplicates.
        ack = Packet(self.id, packet.src, 8, "ACK", packet.id, packet.seq, "")
        self._send_packet(ack.to_bytes())

        if packet.seq != expected:
            return False

        self.expected[packet.src] = 1 - expected
        return True

    def _process(self, packet):
        if packet.type == "ACK":
            self._handle_ack(packet)

        elif packet.type == "DATA":
            if self._handle_data(packet):
                self.echo(packet)

        return packet

    # Application

    def probe(self, dst, payload, timeout=1.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive")

        request = self.send(payload, dst)
        deadline = time.monotonic() + timeout

        while True:
            self._retry()

            if self.failed:
                return None

            remaining = deadline - time.monotonic()

            if remaining <= 0:
                if self.pending is not None:
                    self.pending = None
                    self.failed = True
                event(self.run_id, self.id, "timeout", request, self.neighbor)
                return None

            if self.pending is not None:
                remaining = min(remaining, self.pending["deadline"] - time.monotonic())

            ready, _, _ = select.select([self.sock], [], [], max(0, remaining))

            if not ready:
                continue

            packet = self.receive()

            if packet is None:
                continue

            self._process(packet)

            if (
                packet.type == "RESPONSE"
                and packet.src == dst
                and packet.id == request.id
                and packet.payload == payload
            ):
                event(self.run_id, self.id, "probe_success", packet)
                return packet.payload

    def echo(self, packet):
        if packet.type != "DATA":
            return

        response = Packet(
            self.id, packet.src, 8, "RESPONSE", packet.id, packet.seq, packet.payload
        )

        self._send_packet(response.to_bytes())

    # Interactive

    def interactive(self, dst, timeout=3.0):
        responses = {}
        expired = {}
        inputs = [self.sock, sys.stdin]

        print(f"Listening as {self.id}. Type a message to {dst}: ")

        while True:
            deadlines = [self.pending["deadline"]] if self.pending else []

            deadlines += [deadline for _, deadline in responses.values()]

            wait = None if not deadlines else max(0, min(deadlines) - time.monotonic())

            ready, _, _ = select.select(inputs, [], [], wait)
            now = time.monotonic()

            self._retry()

            # Application response timeouts
            for msg_id, (request, deadline) in list(responses.items()):
                if deadline <= now:
                    event(self.run_id, self.id, "timeout", request, self.neighbor)
                    print(f"TIMEOUT id={msg_id}")
                    expired[msg_id] = request
                    del responses[msg_id]

            # Incoming packets
            if self.sock in ready:
                packet = self.receive()

                if packet is not None:
                    self._process(packet)

                    if packet.type == "DATA":
                        if packet.seq == self.expected.get(packet.src, 0) ^ 1:
                            pass  # Duplicate packets are already ACKed.

                        print(f"Received from {packet.src}: " f"{packet.payload!r}")

                    elif packet.type == "RESPONSE":
                        if packet.id in responses:
                            request, deadline = responses[packet.id]

                            if (
                                packet.src == request.dst
                                and packet.payload == request.payload
                            ):
                                print(f"MATCHED id={packet.id}")
                                del responses[packet.id]

                        elif packet.id in expired:
                            print(f"LATE id={packet.id}")
                            del expired[packet.id]

            # User input
            if sys.stdin in ready:
                line = sys.stdin.readline()

                if not line:
                    inputs.remove(sys.stdin)
                    continue

                if self.pending is not None:
                    print("Waiting for ACK of previous message.")
                    continue

                if self.failed:
                    print("Sender failed; restart host.")
                    continue

                message = line.rstrip("\n")

                try:
                    request = self.send(message, dst)
                except PacketError as exc:
                    print(f"Message rejected: {exc}")
                    continue

                responses[request.id] = (request, time.monotonic() + timeout)

    def run(self):
        while True:
            wait = (
                None
                if self.pending is None
                else max(0, self.pending["deadline"] - time.monotonic())
            )

            ready, _, _ = select.select([self.sock], [], [], wait)

            self._retry()

            if ready:
                try:
                    packet = self.receive()
                except OSError:
                    break

                if packet is not None:
                    self._process(packet)

    def close(self):
        self.sender.close()
        self.sock.close()
        event(self.run_id, self.id, "shutdown")