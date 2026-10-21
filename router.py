import socket

from link import LinkSender, event
from packet import MAX_SIZE, Packet, PacketError


class Router:
    def __init__(self, router_id, config, run_id='default'):
        if config.get_device(router_id)['type'] != 'router':
            raise ValueError(f"'{router_id}' is not a router")
        self.id, self.config, self.run_id = router_id, config, run_id
        self.address = config.get_address(self.id)
        self.table = config.get_routes(self.id)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((self.address['ip'], self.address['port']))
        except OSError:
            self.sock.close()
            raise
        self.sender = LinkSender(self.sock, config, self.id, run_id)
        event(run_id, self.id, 'start')

    def _receive_packet(self):
        data, _ = self.sock.recvfrom(MAX_SIZE + 1)
        return data

    def _send_packet(self, packet_bytes, next_hop):
        self.sender.send(packet_bytes, next_hop)

    def forward(self, data):
        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            event(self.run_id, self.id, 'drop', reason='invalid_packet')
            return
        next_hop = self.table.get(packet.dst)
        event(self.run_id, self.id, 'receive', packet, next_hop or '-')
        reason = None
        if packet.hop_limit <= 1:
            reason = 'hop_limit_exhausted'
        elif next_hop is None:
            reason = 'no_route'
        elif next_hop not in self.config.get_neighbors(self.id):
            reason = 'invalid_next_hop'
        elif next_hop not in self.config.data['addresses']:
            reason = 'missing_address'
        if reason:
            event(self.run_id, self.id, 'drop', packet, next_hop or '-', reason=reason)
            return
        forwarded = Packet(packet.src, packet.dst, packet.hop_limit - 1,
                           packet.type, packet.id, packet.payload)
        event(self.run_id, self.id, 'forward', forwarded, next_hop)
        self._send_packet(forwarded.to_bytes(), next_hop)

    def run(self):
        while True:
            try:
                data = self._receive_packet()
            except (ConnectionResetError, socket.timeout):
                continue
            except OSError:
                break
            self.forward(data)

    def close(self):
        self.sender.close()
        self.sock.close()
        event(self.run_id, self.id, 'shutdown')
