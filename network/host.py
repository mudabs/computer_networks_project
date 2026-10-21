from config import Config
from packet import Packet, PacketError
import socket


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
        self.sock.sendto(packet_bytes, self.config.get_address(self.neighbor))

    def send(self, data, dst):
        packet = Packet(self.id, dst, 8, "DATA", self.packet_id, data)

        packet_bytes = packet.to_bytes()
        self._send_packet(packet_bytes)

        self.packet_id = (self.packet_id + 1) % (2**32)

    def _receive_packet(self):
        data, _ = self.sock.recvfrom(1200)
        return data

    def receive(self):
        data = self._receive_packet()

        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            # Add error logging later.
            return None

        if packet.dst != self.id:
            return None

        return packet.payload

    def close(self):
        self.sock.close()
