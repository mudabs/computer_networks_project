import socket

from config import Config
from packet import MAX_SIZE, Packet, PacketError


class Router:
    def __init__(self, router_id, config):
        if config.get_device(router_id)["type"] != "router":
            raise ValueError(f"'{router_id}' is not a router")

        self.id = router_id
        self.config = config
        self.address = config.get_address(self.id)
        self.table = config.get_routes(self.id)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        try:
            self.sock.bind((self.address["ip"], self.address["port"]))
        except OSError:
            self.sock.close()
            raise

    def _receive_packet(self):
        data, _ = self.sock.recvfrom(MAX_SIZE + 1)
        return data

    def _send_packet(self, packet_bytes, next_hop):
        self.sock.sendto(
            packet_bytes,
            (
                self.config.get_address(next_hop)["ip"],
                self.config.get_address(next_hop)["port"],
            ),
        )

    def forward(self, data):
        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            # Add structured logging later.
            return

        if packet.hop_limit <= 1:
            # Log exhausted hop limit.
            return

        next_hop = self.table.get(packet.dst)
        if next_hop is None:
            # Log unknown destination.
            return

        try:
            next_hop_address = self.config.get_address(next_hop)
        except KeyError:
            # Log invalid next hop.
            return

        if next_hop not in self.config.get_neighbors(self.id):
            # Log next hop that is not directly connected.
            return

        forwarded_packet = Packet(
            src=packet.src,
            dst=packet.dst,
            hop_limit=packet.hop_limit - 1,
            type=packet.type,
            id=packet.id,
            payload=packet.payload,
        )

        try:
            packet_bytes = forwarded_packet.to_bytes()
        except PacketError:
            return

        self._send_packet(packet_bytes, next_hop)

    def run(self):
        print(
            f"Router {self.id} running on "
            f"{self.address['ip']}:{self.address['port']}"
        )

        while True:
            try:
                data = self._receive_packet()
            except ConnectionResetError:
                # Windows may report this when a neighbor is unavailable.
                continue
            except OSError:
                break

            self.forward(data)

    def close(self):
        self.sock.close()