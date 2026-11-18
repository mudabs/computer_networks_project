import math
import select
import socket
import sys
import time

from config import Config
from link import LinkSender, event
from packet import MAX_ID, MAX_SIZE, Packet, PacketError


class Host:
    def __init__(
        self,
        host_id,
        config: Config,
        run_id="default",
        timeout=1.0,
        max_retries=3,
        mode="best_effort",
    ):
        if config.get_device(host_id)["type"] != "host":
            raise ValueError(f"{host_id!r} is not a host")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if mode not in ("best_effort", "reliable"):
            raise ValueError("mode must be best_effort or reliable")

        self.id = host_id
        self.config = config
        self.run_id = run_id
        self.mode = mode
        self.timeout = timeout
        self.max_retries = max_retries
        self.address = config.get_address(host_id)
        self.packet_id = 0

        neighbors = config.get_neighbors(host_id)
        if len(neighbors) != 1:
            raise ValueError("Host must have exactly one neighbor")
        self.neighbor = neighbors[0]

        self.expected = {}
        self.completed = set()
        self.transfer = None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((self.address["ip"], self.address["port"]))
            self.sock.settimeout(0.2)
        except OSError:
            self.sock.close()
            raise

        self.sender = LinkSender(self.sock, config, host_id, run_id)
        event(run_id, host_id, "start", next_hop=self.neighbor)

    def _log(self, name, packet=None, **details):
        if packet is not None:
            details.setdefault("seq", packet.seq)
        event(self.run_id, self.id, name, packet, self.neighbor, **details)

    def _new_id(self):
        packet_id = self.packet_id
        self.packet_id = (self.packet_id + 1) % (MAX_ID + 1)
        return packet_id

    def _send_packet(self, packet):
        self.sender.send(packet.to_bytes(), self.neighbor)

    def _send_best_effort(self, data, dst):
        packet = Packet(self.id, dst, 8, "DATA", self._new_id(), 0, data)
        self._send_packet(packet)
        return packet

    def send(self, data, dst, mode=None):
        mode = self.mode if mode is None else mode

        if mode == "best_effort":
            return self._send_best_effort(data, dst)
        if mode == "reliable":
            return self.start_transfer([data], dst)

        raise ValueError("Invalid mode")

    # Reliable finite transfer

    def start_transfer(self, messages, dst):
        messages = list(messages)

        if not messages:
            raise ValueError("Transfer must contain at least one message")
        if len(messages) - 1 > MAX_ID:
            raise ValueError("Transfer has too many messages")
        if any(not isinstance(message, str) for message in messages):
            raise ValueError("All messages must be strings")

        transfer_id = self._new_id()
        self.transfer = {
            "id": transfer_id,
            "status": "ACTIVE",
            "count": len(messages),
            "sent": 0,
        }

        event(
            self.run_id,
            self.id,
            "transfer_start",
            transfer_id=transfer_id,
            count=len(messages),
        )

        for seq, message in enumerate(messages):
            packet = Packet(self.id, dst, 8, "DATA", transfer_id, seq, message)

            if not self._send_reliably(packet):
                self.transfer["status"] = "FAILED"
                event(
                    self.run_id,
                    self.id,
                    "failure",
                    packet,
                    self.neighbor,
                    reason="retry_limit",
                )
                return self.transfer

            self.transfer["sent"] += 1

        fin = Packet(self.id, dst, 8, "FIN", transfer_id, len(messages), "")

        if not self._send_reliably(fin):
            self.transfer["status"] = "FAILED"
            event(
                self.run_id,
                self.id,
                "failure",
                fin,
                self.neighbor,
                reason="fin_retry_limit",
            )
            return self.transfer

        self.transfer["status"] = "COMPLETE"
        event(
            self.run_id,
            self.id,
            "transfer_complete",
            fin,
            self.neighbor,
            count=len(messages),
        )
        return self.transfer

    def _send_reliably(self, packet):
        data = packet.to_bytes()
        retries = 0

        self._send_packet(packet)
        deadline = time.monotonic() + self.timeout

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                if retries >= self.max_retries:
                    return False

                retries += 1
                self._log("retransmit", packet, retries=retries)
                self._send_packet(packet)
                deadline = time.monotonic() + self.timeout
                continue

            ready, _, _ = select.select([self.sock], [], [], remaining)
            if not ready:
                continue

            incoming = self.receive()
            if incoming is None:
                continue

            if (
                incoming.type == "ACK"
                and incoming.src == packet.dst
                and incoming.dst == packet.src
                and incoming.id == packet.id
                and incoming.seq == packet.seq
            ):
                self._log("ack_received", incoming)
                return True

            self._process(incoming)

    # Receiving and reliability

    def receive(self):
        try:
            data, _ = self.sock.recvfrom(MAX_SIZE + 1)
        except socket.timeout:
            return None

        if len(data) > MAX_SIZE:
            event(self.run_id, self.id, "drop", reason="oversized_datagram")
            return None

        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            event(self.run_id, self.id, "drop", reason="invalid_packet")
            return None

        if packet.dst != self.id:
            self._log("drop", packet, reason="wrong_destination")
            return None

        self._log("receive", packet)
        return packet

    def _send_ack(self, packet):
        ack = Packet(self.id, packet.src, 8, "ACK", packet.id, packet.seq, "")
        self._send_packet(ack)
        self._log("ack_sent", ack)

    def _handle_data(self, packet):
        key = (packet.src, packet.id)
        expected = self.expected.get(key, 0)

        if packet.seq < expected:
            self._log("duplicate", packet)
            self._send_ack(packet)
            return False

        if packet.seq > expected:
            self._log("drop", packet, reason="out_of_order")
            return False

        self._send_ack(packet)
        self.expected[key] = expected + 1
        self._log("deliver", packet)
        return True

    def _handle_fin(self, packet):
        key = (packet.src, packet.id)

        if key in self.completed:
            self._log("duplicate", packet)
            self._send_ack(packet)
            return False

        expected = self.expected.get(key, 0)
        if packet.seq != expected:
            self._log("drop", packet, reason="unexpected_fin_sequence")
            return False

        self._send_ack(packet)
        self.completed.add(key)
        self._log("complete", packet)
        return True

    def _process(self, packet):
        if packet.type == "DATA":
            if self._handle_data(packet):
                self.echo(packet)
                return True

        elif packet.type == "FIN":
            return self._handle_fin(packet)

        elif packet.type == "ACK":
            self._log("ack_received", packet)

        return False

    # Existing best-effort probe and echo

    def probe(self, dst, payload, timeout=1.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")

        request = self._send_best_effort(payload, dst)
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([self.sock], [], [], remaining)
            if not ready:
                break

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

        event(
            self.run_id,
            self.id,
            "timeout",
            request,
            self.neighbor,
            timeout_seconds=timeout,
        )
        return None

    def echo(self, packet):
        if packet.type != "DATA":
            return

        response = Packet(
            self.id, packet.src, 8, "RESPONSE", packet.id, packet.seq, packet.payload
        )
        self._send_packet(response)

    # Interactive mode

    def interactive(self, dst, timeout=3.0):
        pending = {}
        expired = {}
        inputs = [self.sock, sys.stdin]

        print(f"Listening as {self.id}. Type a message to {dst}:")
        print("Type /finish to exit.")

        while True:
            deadlines = [deadline for _, deadline in pending.values()]
            wait = None if not deadlines else max(0, min(deadlines) - time.monotonic())

            ready, _, _ = select.select(inputs, [], [], wait)
            now = time.monotonic()

            for msg_id, (request, deadline) in list(pending.items()):
                if deadline <= now:
                    event(
                        self.run_id,
                        self.id,
                        "timeout",
                        request,
                        self.neighbor,
                        timeout_seconds=timeout,
                    )
                    print(f"TIMEOUT id={msg_id}")
                    expired[msg_id] = request
                    del pending[msg_id]

            if self.sock in ready:
                packet = self.receive()
                if packet is not None:
                    delivered = self._process(packet)

                    if packet.type == "DATA" and delivered:
                        print(f"Received from {packet.src}: {packet.payload!r}")
                    elif packet.type == "FIN" and delivered:
                        print(f"Transfer from {packet.src} completed.")
                    elif packet.type == "RESPONSE":
                        request = pending.get(packet.id)
                        if request:
                            request = request[0]
                            if (
                                packet.src == request.dst
                                and packet.payload == request.payload
                            ):
                                event(self.run_id, self.id, "probe_success", packet)
                                print(f"MATCHED id={packet.id}")
                                del pending[packet.id]
                        elif packet.id in expired:
                            print(f"LATE id={packet.id}")
                            del expired[packet.id]

            if sys.stdin in ready:
                line = sys.stdin.readline()
                if not line:
                    inputs.remove(sys.stdin)
                    continue

                message = line.rstrip("\n")
                if message == "/finish":
                    return

                if self.mode == "reliable":
                    result = self.start_transfer([message], dst)
                    print(result["status"])
                else:
                    try:
                        request = self._send_best_effort(message, dst)
                        pending[request.id] = (request, time.monotonic() + timeout)
                    except PacketError as exc:
                        print(f"Message rejected: {exc}")

    def run(self):
        while True:
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
