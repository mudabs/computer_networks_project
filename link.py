"""Seeded directed-link conditions and a nonblocking delivery scheduler."""
import heapq
import logging
import random
import threading
import time

from packet import Packet

logger = logging.getLogger(__name__)


def event(run_id, device, name, packet=None, next_hop='-', **details):
    fields = dict(run_id=run_id, device=device, event=name,
                  msg_id=packet.id if packet else '-',
                  type=packet.type if packet else '-',
                  src=packet.src if packet else '-', dst=packet.dst if packet else '-',
                  next_hop=next_hop, hop_limit=packet.hop_limit if packet else '-')
    fields.update(details)
    logger.info(' '.join(f'{key}={value}' for key, value in fields.items()))


class LinkSender:
    def __init__(self, sock, config, device_id, run_id):
        self.sock, self.config = sock, config
        self.device_id, self.run_id = device_id, run_id
        self.rules = {}
        for rule in config.data.get('conditions', []):
            if rule['from'] == device_id:
                self.rules[rule['to']] = (rule, random.Random(rule['seed']))
                event(run_id, device_id, 'conditions', next_hop=rule['to'],
                      seed=rule['seed'], loss=rule['loss'], delay_ms=rule['delay_ms'])
        self.queue = []
        self.sequence = 0
        self.condition = threading.Condition()
        self.closed = False
        self.worker = None

    def send(self, data, next_hop):
        packet = Packet.from_bytes(data)
        delay = 0
        if next_hop in self.rules:
            rule, rng = self.rules[next_hop]
            if rng.random() < rule['loss']:
                event(self.run_id, self.device_id, 'drop', packet, next_hop,
                      reason='injected_loss', seed=rule['seed'])
                return
            delay = rule['delay_ms'] / 1000
        if not delay:
            self.deliver(data, next_hop, packet)
            return
        with self.condition:
            if self.closed:
                return
            self.sequence += 1
            heapq.heappush(self.queue, (time.monotonic() + delay, self.sequence,
                                       data, next_hop, packet))
            event(self.run_id, self.device_id, 'delay', packet, next_hop,
                  delay_ms=delay * 1000)
            if self.worker is None:
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()
            self.condition.notify()

    def deliver(self, data, next_hop, packet):
        address = self.config.get_address(next_hop)
        self.sock.sendto(data, (address['ip'], address['port']))
        event(self.run_id, self.device_id, 'send', packet, next_hop)

    def run(self):
        with self.condition:
            while not self.closed:
                if not self.queue:
                    self.condition.wait()
                    continue
                remaining = self.queue[0][0] - time.monotonic()
                if remaining > 0:
                    self.condition.wait(remaining)
                    continue
                _, _, data, next_hop, packet = heapq.heappop(self.queue)
                try:
                    self.deliver(data, next_hop, packet)
                except OSError as exc:
                    event(self.run_id, self.device_id, 'drop', packet, next_hop,
                          reason='send_error', error=repr(exc))

    def close(self):
        with self.condition:
            self.closed = True
            for _, _, _, next_hop, packet in self.queue:
                event(self.run_id, self.device_id, 'drop', packet, next_hop,
                      reason='shutdown')
            self.queue.clear()
            self.condition.notify_all()
        if self.worker is not None:
            self.worker.join()
