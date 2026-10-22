import math
import select
import sys
import socket
import time

from config import Config
from link import LinkSender, event
from packet import MAX_SIZE, Packet, PacketError


class Host:
    def __init__(self, host_id, config: Config, run_id="default"):
        if config.get_device(host_id)["type"] != "host":
            raise ValueError(f"'{host_id}' is not a host")
        self.id = host_id
        self.config = config
        self.address = config.get_address(self.id)
        self.packet_id = 0
        self.run_id = run_id

        neighbors = config.get_neighbors(self.id)
        if len(neighbors) != 1:
            raise ValueError("Host must have exactly one neighbor")
        self.neighbor = neighbors[0]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((self.address["ip"], self.address["port"]))
        except OSError:
            self.sock.close()
            raise
        self.sender = LinkSender(self.sock, config, self.id, run_id)
        event(run_id, self.id, "start", next_hop=self.neighbor)

    def _send_packet(self, packet_bytes):
        self.sender.send(packet_bytes, self.neighbor)

    def send(self, data, dst):
        packet = Packet(self.id, dst, 8, "DATA", self.packet_id, data)
        self._send_packet(packet.to_bytes())
        self.packet_id = (self.packet_id + 1) % (2**32)

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

    def probe(self, dst, payload, timeout=1.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        request = Packet(self.id, dst, 8, "DATA", self.packet_id, payload)
        deadline = time.monotonic() + timeout
        self.send(payload, dst)
        old_timeout = self.sock.gettimeout()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self.sock.settimeout(remaining)
                try:
                    packet = self.receive()
                except socket.timeout:
                    break
                if packet is None:
                    continue
                if packet.type == "DATA":
                    self.echo(packet)
                elif (packet.type == "RESPONSE"
                      and packet.src == dst and packet.dst == self.id
                      and packet.id == request.id and packet.payload == payload):
                    event(self.run_id, self.id, "probe_success", packet)
                    return packet.payload
            event(self.run_id, self.id, "timeout", request, self.neighbor,
                  timeout_seconds=timeout)
            return None
        finally:
            self.sock.settimeout(old_timeout)

    def echo(self, packet):
        if packet.type == "DATA":
            response = Packet(self.id, packet.src, 8, "RESPONSE", packet.id, packet.payload)
            self._send_packet(response.to_bytes())

    def interactive(self, dst, timeout=3.0):
        """Echo incoming packets and send typed messages, using one event loop."""
        pending = {}
        expired = {}
        inputs = [self.sock, sys.stdin]
        print(f"Listening as {self.id}. Type a message to {dst}: ")
        while True:
            wait = min((deadline - time.monotonic() for _, deadline in pending.values()),
                       default=None)
            ready, _, _ = select.select(inputs, [], [], None if wait is None else max(0, wait))
            now = time.monotonic()
            for msg_id, (request, deadline) in list(pending.items()):
                if deadline <= now:
                    event(self.run_id, self.id, "timeout", request, self.neighbor,
                          timeout_seconds=timeout)
                    print(f"TIMEOUT id={msg_id}")
                    expired[msg_id] = request
                    del pending[msg_id]
            if self.sock in ready:
                packet = self.receive()
                if packet is not None:
                    if packet.type == "DATA":
                        print(f"Received from {packet.src}: {packet.payload!r}")
                        self.echo(packet)
                    elif packet.id in pending:
                        request, deadline = pending[packet.id]
                        if (time.monotonic() < deadline and packet.src == request.dst
                                and packet.payload == request.payload):
                            event(self.run_id, self.id, "probe_success", packet)
                            print(f"MATCHED id={packet.id} {packet.payload!r}")
                            del pending[packet.id]
                    elif packet.id in expired:
                        request = expired[packet.id]
                        if packet.src == request.dst and packet.payload == request.payload:
                            event(self.run_id, self.id, "late_response", packet)
                            print(f"LATE id={packet.id} {packet.payload!r}")
                            del expired[packet.id]
            if sys.stdin in ready:
                line = sys.stdin.readline()
                if not line:
                    inputs.remove(sys.stdin)  # Keep echoing after terminal input closes.
                else:
                    message = line.rstrip("\n")
                    request = Packet(self.id, dst, 8, "DATA", self.packet_id, message)
                    try:
                        self.send(message, dst)
                    except PacketError as exc:
                        print(f"Message rejected: {exc}")
                        continue
                    pending[request.id] = (request, time.monotonic() + timeout)

    def run(self):
        while True:
            try:
                packet = self.receive()
            except socket.timeout:
                continue
            except OSError:
                break
            if packet is not None:
                self.echo(packet)

    def close(self):
        self.sender.close()
        self.sock.close()
        event(self.run_id, self.id, "shutdown")
