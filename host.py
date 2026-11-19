import math
import select
import sys
import socket
import time

from config import Config
from link import LinkSender, event
from packet import MAX_ID, MAX_SIZE, Packet, PacketError


class Host:
    def __init__(self, host_id, config: Config, run_id="default",
                 timeout=1.0, max_retries=3, mode="best_effort"):
        if config.get_device(host_id)["type"] != "host":
            raise ValueError(f"'{host_id}' is not a host")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(max_retries) is not int or max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if mode not in ("best_effort", "reliable"):
            raise ValueError("mode must be best_effort or reliable")
        self.mode = mode
        self.timeout = timeout
        self.max_retries = max_retries
        # Reliable-transfer state (stop-and-wait): next expected seq per
        # (source, transfer id), finished transfers, and the active send.
        self.expected = {}
        self.completed = set()
        self.transfer = None
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
            # A short timeout lets a GUI controller stop and restart a host
            # cleanly without waiting forever in recvfrom().
            self.sock.settimeout(0.2)
        except OSError:
            self.sock.close()
            raise
        self.sender = LinkSender(self.sock, config, self.id, run_id)
        event(run_id, self.id, "start", next_hop=self.neighbor)

    def _send_packet(self, packet_bytes, **marks):
        # ``marks`` (retransmit/duplicate) only annotate log events; they are
        # passed on only when set, so ordinary sends are unchanged.
        self.sender.send(packet_bytes, self.neighbor, **marks)

    def send(self, data, dst, mode=None):
        mode = self.mode if mode is None else mode
        if mode == "reliable":
            return self.start_transfer([data], dst)
        if mode != "best_effort":
            raise ValueError("Invalid mode")
        packet = Packet(self.id, dst, 8, "DATA", self.packet_id, 0, data)
        self._send_packet(packet.to_bytes())
        self.packet_id = (self.packet_id + 1) % (MAX_ID + 1)

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
        request = Packet(self.id, dst, 8, "DATA", self.packet_id, 0, payload)
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
                if packet.type != "RESPONSE":
                    self._process(packet)
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

    def probe_with_late(self, dst, payload, timeout=1.0, late_window=3.0):
        """Probe while optionally observing a matching response after timeout.

        The normal probe API retains its original one-shot semantics.  This
        variant is used by the visual demo so a delayed response can be shown
        as LATE without changing the CLI behavior.
        """
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if not math.isfinite(late_window) or late_window < 0:
            raise ValueError("late_window must be finite and nonnegative")

        request = Packet(self.id, dst, 8, "DATA", self.packet_id, 0, payload)
        self.send(payload, dst)
        old_timeout = self.sock.gettimeout()

        def wait_for_response(deadline):
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
                if packet.type != "RESPONSE":
                    self._process(packet)
                elif (packet.type == "RESPONSE" and packet.src == dst
                      and packet.dst == self.id and packet.id == request.id
                      and packet.payload == payload):
                    return packet

        try:
            packet = wait_for_response(time.monotonic() + timeout)
            if packet is not None:
                event(self.run_id, self.id, "probe_success", packet)
                return "MATCHED", packet.payload

            event(self.run_id, self.id, "timeout", request, self.neighbor,
                  timeout_seconds=timeout)
            packet = wait_for_response(time.monotonic() + late_window)
            if packet is not None:
                event(self.run_id, self.id, "late_response", packet)
                return "LATE", packet.payload
            return "TIMEOUT", None
        finally:
            self.sock.settimeout(old_timeout)

    # Reliable finite transfer (stop-and-wait). Used only in "reliable" mode;
    # best-effort probe/echo behavior is unchanged.

    def start_transfer(self, messages, dst):
        messages = list(messages)
        if not messages:
            raise ValueError("Transfer must contain at least one message")
        if len(messages) > MAX_ID:
            raise ValueError("Transfer has too many messages")
        if any(not isinstance(message, str) for message in messages):
            raise ValueError("All messages must be strings")

        transfer_id = self.packet_id
        self.packet_id = (self.packet_id + 1) % (MAX_ID + 1)
        self.transfer = {
            "id": transfer_id,
            "status": "ACTIVE",
            "count": len(messages),
            "sent": 0,
        }
        event(self.run_id, self.id, "transfer_start",
              transfer_id=transfer_id, count=len(messages))

        for seq, message in enumerate(messages):
            packet = Packet(self.id, dst, 8, "DATA", transfer_id, seq, message)
            if not self._send_reliably(packet):
                self.transfer["status"] = "FAILED"
                event(self.run_id, self.id, "failure", packet, self.neighbor,
                      seq=seq, reason="retry_limit")
                return self.transfer
            self.transfer["sent"] += 1

        fin = Packet(self.id, dst, 8, "FIN", transfer_id, len(messages), "")
        if not self._send_reliably(fin):
            self.transfer["status"] = "FAILED"
            event(self.run_id, self.id, "failure", fin, self.neighbor,
                  seq=fin.seq, reason="fin_retry_limit")
            return self.transfer

        self.transfer["status"] = "COMPLETE"
        event(self.run_id, self.id, "transfer_complete", fin, self.neighbor,
              seq=fin.seq, count=len(messages))
        return self.transfer

    def _log_seq(self, name, packet, **details):
        event(self.run_id, self.id, name, packet, self.neighbor,
              seq=packet.seq, **details)

    def _send_reliably(self, packet):
        """Send one packet and wait for its ACK; False once retries run out."""
        data = packet.to_bytes()
        retries = 0
        self._send_packet(data)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if retries >= self.max_retries:
                    return False
                retries += 1
                self._log_seq("retransmit", packet, retries=retries)
                self._send_packet(data, retransmit=True)
                deadline = time.monotonic() + self.timeout
                continue
            ready, _, _ = select.select([self.sock], [], [], remaining)
            if not ready:
                continue
            incoming = self.receive()
            if incoming is None:
                continue
            if (incoming.type == "ACK" and incoming.src == packet.dst
                    and incoming.dst == packet.src and incoming.id == packet.id
                    and incoming.seq == packet.seq):
                self._log_seq("ack_received", incoming)
                return True
            self._process(incoming)

    def _send_ack(self, packet, duplicate=False):
        ack = Packet(self.id, packet.src, 8, "ACK", packet.id, packet.seq, "")
        self._send_packet(ack.to_bytes(), **({"duplicate": True} if duplicate else {}))
        self._log_seq("ack_sent", ack)

    def _handle_data(self, packet):
        key = (packet.src, packet.id)
        expected = self.expected.get(key, 0)
        if packet.seq < expected:
            self._log_seq("duplicate", packet)
            self._send_ack(packet, duplicate=True)  # the earlier ACK may have been lost
            return False
        if packet.seq > expected:
            self._log_seq("drop", packet, reason="out_of_order")
            return False
        self._send_ack(packet)
        self.expected[key] = expected + 1
        self._log_seq("deliver", packet)
        return True

    def _handle_fin(self, packet):
        key = (packet.src, packet.id)
        if key in self.completed:
            self._log_seq("duplicate", packet)
            self._send_ack(packet, duplicate=True)
            return False
        if packet.seq != self.expected.get(key, 0):
            self._log_seq("drop", packet, reason="unexpected_fin_sequence")
            return False
        self._send_ack(packet)
        self.completed.add(key)
        self._log_seq("complete", packet)
        return True

    def _process(self, packet):
        """Handle one received packet; True if new DATA/FIN was accepted.

        Best-effort hosts echo DATA exactly as before. Reliable hosts
        acknowledge DATA/FIN instead and do not echo it.
        """
        if self.mode == "best_effort":
            if packet.type == "DATA":
                self.echo(packet)
                return True
            return False
        if packet.type == "DATA":
            return self._handle_data(packet)
        if packet.type == "FIN":
            return self._handle_fin(packet)
        if packet.type == "ACK":
            self._log_seq("ack_received", packet)
        return False

    def echo(self, packet):
        if packet.type == "DATA":
            response = Packet(self.id, packet.src, 8, "RESPONSE", packet.id, packet.seq, packet.payload)
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
                    if packet.type in ("DATA", "FIN", "ACK"):
                        delivered = self._process(packet)
                        if delivered and packet.type == "DATA":
                            print(f"Received from {packet.src}: {packet.payload!r}")
                        elif delivered and packet.type == "FIN":
                            print(f"Transfer from {packet.src} completed.")
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
                    request = Packet(self.id, dst, 8, "DATA", self.packet_id, 0, message)
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
                self._process(packet)

    def close(self):
        self.sender.close()
        self.sock.close()
        event(self.run_id, self.id, "shutdown")
