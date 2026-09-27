from config import Config
from packet import Packet, PacketSizeError
import socket


class Host:
    def __init__(self, host_id, config):
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
        self.sock.bind((self.address["ip"], self.address["port"]))

    def _send_packet(self, packet_bytes):
        self.sock.sendto(packet_bytes, self.config.get_address(self.neighbor))

    def send(self, data, dst):
        remaining = data
        while remaining:
            candidate = remaining
            packet = Packet(self.id, dst, 8, "DATA", self.packet_id, candidate)
            while True:
                try:
                    packet_bytes = packet.to_bytes()
                    self._send_packet(packet_bytes)
                    break
                except PacketSizeError:
                    candidate = candidate[: len(candidate) // 2 + len(candidate) % 2]
                    packet = Packet(self.id, dst, 8, "DATA", self.packet_id, candidate)

            remaining = remaining[len(candidate) :]
        self.packet_id += 1
        self.packet_id %= 4294967296

    def receive(self, data):
        return data
