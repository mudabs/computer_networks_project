import socket

from link import LinkSender, event
from packet import MAX_SIZE, Packet, PacketError


class Router:
    def __init__(self, router_id, config, run_id="default"):
        if config.get_device(router_id)["type"] != "router":
            raise ValueError(f"{router_id!r} is not a router")

        self.id = router_id
        self.config = config
        self.run_id = run_id
        self.address = config.get_address(router_id)
        self.table = config.get_routes(router_id)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((self.address["ip"], self.address["port"]))
            self.sock.settimeout(0.2)
        except OSError:
            self.sock.close()
            raise

        self.sender = LinkSender(self.sock, config, router_id, run_id)
        event(run_id, router_id, "start")

    def forward(self, data):
        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            event(self.run_id, self.id, "drop", reason="invalid_packet")
            return

        next_hop = self.table.get(packet.dst)
        event(self.run_id, self.id, "receive", packet, next_hop or "-")

        reason = None
        if packet.ttl <= 1:
            reason = "hop_limit_exhausted"
        elif next_hop is None:
            reason = "no_route"
        elif next_hop not in self.config.get_neighbors(self.id):
            reason = "invalid_next_hop"
        elif next_hop not in self.config.data["addresses"]:
            reason = "missing_address"

        if reason:
            event(
                self.run_id,
                self.id,
                "drop",
                packet,
                next_hop or "-",
                seq=packet.seq,
                reason=reason,
            )
            return

        forwarded = Packet(
            packet.src,
            packet.dst,
            packet.ttl - 1,
            packet.type,
            packet.id,
            packet.seq,
            packet.payload,
        )

        event(self.run_id, self.id, "forward", forwarded, next_hop, seq=forwarded.seq)
        self.sender.send(forwarded.to_bytes(), next_hop)

    def run(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(MAX_SIZE + 1)
            except (ConnectionResetError, socket.timeout):
                continue
            except OSError:
                break

            self.forward(data)

    def close(self):
        self.sender.close()
        self.sock.close()
        event(self.run_id, self.id, "shutdown")
