import socket
import time

from config import Config
from packet import MAX_SIZE, Packet, PacketError


class Host:
    def __init__(self, host_id, config: Config):
        if config.get_device(host_id)["type"] != "host":
            raise ValueError(f"'{host_id}' is not a host")

        self.id = host_id
        self.config = config
        self.address = config.get_address(self.id)
        self.packet_id = 0

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

    def _send_packet(self, packet_bytes):
        neighbor_address = self.config.get_address(self.neighbor)
        self.sock.sendto(
            packet_bytes,
            (neighbor_address["ip"], neighbor_address["port"]),
        )

    def send(self, data, dst):
        packet = Packet(
            src=self.id,
            dst=dst,
            hop_limit=8,
            type="DATA",
            id=self.packet_id,
            payload=data,
        )

        packet_bytes = packet.to_bytes()
        self._send_packet(packet_bytes)

        self.packet_id = (self.packet_id + 1) % (2**32)

    def _receive_packet(self):
        data, _ = self.sock.recvfrom(MAX_SIZE + 1)
        return data

    def receive(self):
        data = self._receive_packet()

        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            # Protocol error logging will be added later.
            return None

        if packet.dst != self.id:
            return None

        return packet

    def probe(self, dst, payload, timeout=1.0):
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        expected_id = self.packet_id
        deadline = time.monotonic() + timeout

        self.send(payload, dst)

        old_timeout = self.sock.gettimeout()

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

                self.sock.settimeout(remaining)

                try:
                    packet = self.receive()
                except socket.timeout:
                    return None

                if packet is None:
                    continue

                if (
                    packet.type == "RESPONSE"
                    and packet.src == dst
                    and packet.dst == self.id
                    and packet.id == expected_id
                ):
                    return packet.payload
        finally:
            self.sock.settimeout(old_timeout)

    def echo(self, packet):
        if packet.type != "DATA":
            return

        response = Packet(
            src=self.id,
            dst=packet.src,
            hop_limit=8,
            type="RESPONSE",
            id=packet.id,
            payload=packet.payload,
        )

        self._send_packet(response.to_bytes())

    def run(self):
        while True:
            try:
                packet = self.receive()
            except socket.timeout:
                continue
            except OSError:
                # Socket was closed during shutdown.
                break

            if packet is None:
                continue

            if packet.type == "DATA":
                self.echo(packet)

    def close(self):
        self.sock.close()
