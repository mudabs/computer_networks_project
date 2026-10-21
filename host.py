import logging
import socket
import time

from config import Config
from packet import MAX_SIZE, Packet, PacketError

logger = logging.getLogger(__name__)


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
            logger.exception(
                "run_id=%s device=%s event=bind_error",
                self.run_id,
                self.id,
            )
            raise

        logger.info(
            "run_id=%s device=%s event=start address=%s:%s neighbor=%s",
            self.run_id,
            self.id,
            self.address["ip"],
            self.address["port"],
            self.neighbor,
        )

    def _send_packet(self, packet_bytes):
        neighbor_address = self.config.get_address(self.neighbor)
        endpoint = (neighbor_address["ip"], neighbor_address["port"])

        self.sock.sendto(packet_bytes, endpoint)

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

        logger.info(
            "run_id=%s device=%s event=send msg_id=%s type=%s "
            "src=%s dst=%s next_hop=%s hop_limit=%s bytes=%s",
            self.run_id,
            self.id,
            packet.id,
            packet.type,
            packet.src,
            packet.dst,
            self.neighbor,
            packet.hop_limit,
            len(packet_bytes),
        )

        self.packet_id = (self.packet_id + 1) % (2**32)

    def _receive_packet(self):
        data, sender = self.sock.recvfrom(MAX_SIZE + 1)

        if len(data) > MAX_SIZE:
            logger.warning(
                "run_id=%s device=%s event=drop reason=oversized_datagram "
                "sender=%s bytes=%s limit=%s",
                self.run_id,
                self.id,
                sender,
                len(data),
                MAX_SIZE,
            )
            return None

        return data

    def receive(self):
        data = self._receive_packet()

        if data is None:
            return None

        try:
            packet = Packet.from_bytes(data)
        except PacketError:
            logger.warning(
                "run_id=%s device=%s event=drop reason=invalid_packet",
                self.run_id,
                self.id,
            )
            return None

        if packet.dst != self.id:
            logger.warning(
                "run_id=%s device=%s event=drop reason=wrong_destination "
                "msg_id=%s type=%s src=%s dst=%s hop_limit=%s",
                self.run_id,
                self.id,
                packet.id,
                packet.type,
                packet.src,
                packet.dst,
                packet.hop_limit,
            )
            return None

        logger.info(
            "run_id=%s device=%s event=receive msg_id=%s type=%s "
            "src=%s dst=%s hop_limit=%s bytes=%s",
            self.run_id,
            self.id,
            packet.id,
            packet.type,
            packet.src,
            packet.dst,
            packet.hop_limit,
            len(data),
        )

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
                    logger.warning(
                        "run_id=%s device=%s event=timeout msg_id=%s "
                        "type=DATA src=%s dst=%s timeout_seconds=%s",
                        self.run_id,
                        self.id,
                        expected_id,
                        self.id,
                        dst,
                        timeout,
                    )
                    return None

                self.sock.settimeout(remaining)

                try:
                    packet = self.receive()
                except socket.timeout:
                    logger.warning(
                        "run_id=%s device=%s event=timeout msg_id=%s "
                        "type=DATA src=%s dst=%s timeout_seconds=%s",
                        self.run_id,
                        self.id,
                        expected_id,
                        self.id,
                        dst,
                        timeout,
                    )
                    return None

                if packet is None:
                    continue

                if (
                    packet.type == "RESPONSE"
                    and packet.src == dst
                    and packet.dst == self.id
                    and packet.id == expected_id
                ):
                    logger.info(
                        "run_id=%s device=%s event=probe_success "
                        "msg_id=%s type=RESPONSE src=%s dst=%s",
                        self.run_id,
                        self.id,
                        packet.id,
                        packet.src,
                        packet.dst,
                    )
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

        response_bytes = response.to_bytes()
        self._send_packet(response_bytes)

        logger.info(
            "run_id=%s device=%s event=send msg_id=%s type=%s "
            "src=%s dst=%s next_hop=%s hop_limit=%s bytes=%s",
            self.run_id,
            self.id,
            response.id,
            response.type,
            response.src,
            response.dst,
            self.neighbor,
            response.hop_limit,
            len(response_bytes),
        )

    def run(self):
        while True:
            try:
                packet = self.receive()
            except socket.timeout:
                continue
            except OSError:
                logger.info(
                    "run_id=%s device=%s event=shutdown",
                    self.run_id,
                    self.id,
                )
                break

            if packet is None:
                continue

            if packet.type == "DATA":
                try:
                    self.echo(packet)
                except (OSError, PacketError):
                    logger.exception(
                        "run_id=%s device=%s event=echo_error "
                        "msg_id=%s src=%s dst=%s",
                        self.run_id,
                        self.id,
                        packet.id,
                        packet.src,
                        packet.dst,
                    )

    def close(self):
        self.sock.close()
