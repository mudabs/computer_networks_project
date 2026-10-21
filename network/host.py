import socket

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
            # Add protocol error logging later.
            return None

        if packet.dst != self.id:
            return None

        return packet

    def close(self):
        self.sock.close()
