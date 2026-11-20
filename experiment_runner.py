"""Experiment runner: repeatable raw measurements over the real UDP network.

Each run builds an isolated in-process copy of the network (routers, the
destination receiver and a source host) on ephemeral loopback ports, applies a
seeded loss/delay condition through the existing ``LinkSender``, sends a finite
deterministic workload in ``best_effort`` or ``reliable`` mode, and appends one
JSON Lines record with the exact configuration, workload, outcome and every
protocol event (with relative timestamps).  It computes no comparison metrics;
later tooling consumes the raw records.

Example:

    python experiment_runner.py --mode reliable --messages 20 --loss 0.05 \\
        --seed 42 --repeats 3 --output results/raw.jsonl
"""

import argparse
import copy
import json
import logging
import math
import socket
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import link
from config import Config
from host import Host
from packet import Packet, PacketError
from router import Router

SCHEMA_VERSION = 1
MODES = ("best_effort", "reliable")
JOIN_TIMEOUT = 3.0
# Elapsed times and event timestamps use perf_counter: time.monotonic() ticks at
# ~15.6 ms on Windows, which would quantize loopback RTTs.


@dataclass(frozen=True)
class ExperimentSettings:
    """Everything that affects an experiment (all of it is saved per record)."""

    mode: str = "reliable"
    messages: int = 10
    message_prefix: str = "msg"
    timeout: float = 1.0
    max_retries: int = 3
    loss: float = 0.0
    delay_ms: float = 0.0
    link_from: str = "H1"
    link_to: str = "A"
    source: str = "H1"
    destination: str = "H2"
    seed: int = 42
    repeats: int = 1
    output: str = "results/raw.jsonl"

    def validate(self, base_config=None):
        """Raise ValueError for an invalid combination of settings."""
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if type(self.messages) is not int or self.messages < 1:
            raise ValueError("messages must be a positive integer")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(self.max_retries) is not int or self.max_retries < 0:
            raise ValueError("max-retries must be a non-negative integer")
        if not math.isfinite(self.loss) or not 0 <= self.loss <= 1:
            raise ValueError("loss must be between 0 and 1")
        if not math.isfinite(self.delay_ms) or self.delay_ms < 0:
            raise ValueError("delay-ms must be finite and non-negative")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if type(self.repeats) is not int or self.repeats < 1:
            raise ValueError("repeats must be a positive integer")
        if not self.output:
            raise ValueError("output path must not be empty")
        config = base_config or Config()
        for role, device in (("source", self.source), ("destination", self.destination)):
            entry = config.data["devices"].get(device)
            if entry is None or entry["type"] != "host":
                raise ValueError(f"{role} {device!r} must be a configured host")
        if self.source == self.destination:
            raise ValueError("source and destination must differ")
        try:
            longest = generate_workload(self.messages, self.message_prefix)[-1]
            Packet(self.source, self.destination, 8, "DATA", 0, 0, longest).to_bytes()
        except PacketError as exc:
            raise ValueError(f"messages cannot be sent: {exc}") from None
        if (self.link_from not in config.data["devices"]
                or self.link_to not in config.get_neighbors(self.link_from)):
            raise ValueError(
                f"{self.link_from}->{self.link_to} is not a configured directed link")
        build_config(self, run_seed=self.seed, base_config=config)  # validates the rule


def generate_workload(count, prefix="msg"):
    """The finite, deterministic message list shared by every repeat."""
    return [f"{prefix}-{index:04d}" for index in range(count)]


def derive_run_seed(base_seed, repeat):
    """Explicit per-repeat seed: repeats differ, reruns of a repeat do not."""
    return base_seed + repeat


def conditions_for(settings, run_seed):
    """The directed loss/delay rule (none when the link is unimpaired)."""
    if settings.loss == 0 and settings.delay_ms == 0:
        return []
    return [{"from": settings.link_from, "to": settings.link_to,
             "loss": settings.loss, "delay_ms": settings.delay_ms, "seed": run_seed}]


def build_config(settings, run_seed, base_config=None):
    """In-memory copy of the network with this run's condition applied."""
    base = base_config or Config()
    data = copy.deepcopy(base.data)
    data["conditions"] = conditions_for(settings, run_seed)
    return Config(network_data=data, route_data=copy.deepcopy(base.route_data))


def assign_ephemeral_ports(config):
    """Replace every fixed port with a free one; the JSON files are untouched."""
    reservations = []
    try:
        for address in config.data["addresses"].values():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            reservations.append(sock)
            sock.bind((address["ip"], 0))
            address["port"] = sock.getsockname()[1]
    finally:
        for sock in reservations:
            sock.close()
    return {device: address["port"] for device, address in config.data["addresses"].items()}


def route_path(config, source, destination):
    """Device sequence a packet follows from source to destination."""
    path, device = [source], config.get_neighbors(source)[0]
    while device != destination and device not in path:
        path.append(device)
        device = config.get_routes(device).get(destination)
        if device is None:
            return path  # unreachable: stops at the last router
    path.append(device)
    return path


def logical_config(config, source, destination):
    """Effective network/route configuration, without runtime ports."""
    return {
        "devices": copy.deepcopy(config.data["devices"]),
        "links": copy.deepcopy(config.data["links"]),
        "conditions": copy.deepcopy(config.data["conditions"]),
        "routes": copy.deepcopy(config.route_data),
        "source": source,
        "destination": destination,
        "path": route_path(config, source, destination),
    }


def parse_event(message):
    """key=value tokens of a link.event() log line."""
    fields = {}
    for token in message.split():
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


class _CaptureHandler(logging.Handler):
    def __init__(self, run_id, started):
        super().__init__(logging.INFO)
        self.run_id, self.started, self.events = run_id, started, []

    def emit(self, record):
        fields = parse_event(record.getMessage())
        if fields.get("run_id") == self.run_id:
            self.events.append({"t": round(time.perf_counter() - self.started, 6),
                                "fields": fields})


@contextmanager
def capture_events(run_id):
    """Collect this run's structured events; always restores the logger."""
    logger = link.logger
    saved = (logger.level, logger.propagate)
    handler = _CaptureHandler(run_id, time.perf_counter())
    logger.setLevel(logging.INFO)
    logger.propagate = False  # keep experiment batches quiet
    logger.addHandler(handler)
    try:
        yield handler.events
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved[0])
        logger.propagate = saved[1]


class IsolatedNetwork:
    """Routers + destination receiver + source host on ephemeral UDP ports."""

    def __init__(self, config, run_id, settings):
        self.config, self.run_id, self.settings = config, run_id, settings
        self.devices, self.threads, self.thread_errors = [], [], []
        self.source = None
        self.closing = False
        self.threads_alive = 0

    def _serve(self, device):
        try:
            device.run()
            if not self.closing:  # a serve loop only ends when we close its socket
                self.thread_errors.append(f"{device.id}: device loop exited unexpectedly")
        except OSError as exc:
            if not self.closing:  # closed sockets during shutdown are expected
                self.thread_errors.append(f"{device.id}: {exc!r}")
        except Exception as exc:  # recorded, never raised into the batch
            self.thread_errors.append(f"{device.id}: {exc!r}")

    def __enter__(self):
        settings, run_id = self.settings, self.run_id
        try:
            for device_id, entry in self.config.data["devices"].items():
                if entry["type"] == "router":
                    self.devices.append(Router(device_id, self.config, run_id=run_id))
            host_options = dict(run_id=run_id, mode=settings.mode, timeout=settings.timeout)
            if settings.mode == "reliable":
                host_options["max_retries"] = settings.max_retries
            receiver = Host(settings.destination, self.config, **host_options)
            self.devices.append(receiver)
            self.source = Host(settings.source, self.config, **host_options)
            self.devices.append(self.source)
            for device in self.devices:
                if device is not self.source:  # the source only sends
                    thread = threading.Thread(target=self._serve, args=(device,), daemon=True)
                    self.threads.append(thread)
                    thread.start()
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        self.closing = True
        for device in self.devices:
            try:
                device.close()
            except OSError:
                pass
        for thread in self.threads:
            thread.join(JOIN_TIMEOUT)
        self.threads_alive = sum(thread.is_alive() for thread in self.threads)
        self.devices = []

    def __exit__(self, *exc_info):
        self.close()


def run_workload(source, settings, messages):
    """Send the workload; a failure or timeout is a result, not an error."""
    if settings.mode == "reliable":
        started = time.perf_counter()
        result = source.start_transfer(messages, settings.destination)
        return {"type": "reliable_transfer", "result": dict(result),
                "elapsed_s": time.perf_counter() - started}
    outcomes = []
    for index, message in enumerate(messages):
        started = time.perf_counter()
        response = source.probe(settings.destination, message, timeout=settings.timeout)
        outcomes.append({
            "index": index, "message": message,
            "status": "MATCHED" if response is not None else "TIMEOUT",
            "response": response, "elapsed_s": time.perf_counter() - started,
        })
    return {"type": "best_effort_probes", "messages": outcomes}


def run_once(settings, repeat, base_config=None, run_id=None):
    """One isolated run; returns its raw record (never raises for FAILED/TIMEOUT).

    ``run_id`` (no spaces) overrides the default ``<mode>-s<seed>-r<repeat>`` so a
    caller running many conditions can keep every record's id unique.
    """
    run_seed = derive_run_seed(settings.seed, repeat)
    run_id = run_id or f"{settings.mode}-s{settings.seed}-r{repeat}"
    messages = generate_workload(settings.messages, settings.message_prefix)
    config = build_config(settings, run_seed, base_config)
    logical = logical_config(config, settings.source, settings.destination)
    ports = assign_ephemeral_ports(config)

    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "repeat": repeat,
        "mode": settings.mode,
        "seed": run_seed,
        "base_seed": settings.seed,
        "config": logical,
        "settings": {
            "timeout": settings.timeout,
            "max_retries": settings.max_retries if settings.mode == "reliable" else None,
            "loss": settings.loss,
            "delay_ms": settings.delay_ms,
            "link_from": settings.link_from,
            "link_to": settings.link_to,
            "message_count": settings.messages,
            "message_prefix": settings.message_prefix,
            "repeats": settings.repeats,
        },
        "workload": {"messages": messages},
        "runtime": {"ports": ports},
    }
    started_unix, started = time.time(), time.perf_counter()
    outcome, runner_error, network = None, None, None
    with capture_events(run_id) as events:
        try:
            network = IsolatedNetwork(config, run_id, settings)
            with network:
                outcome = run_workload(network.source, settings, messages)
        except Exception as exc:
            runner_error = f"{type(exc).__name__}: {exc}"
    if network is not None:  # unexpected device-thread trouble is a runner error
        problems = []
        if network.thread_errors:
            problems.append("device thread errors: " + "; ".join(network.thread_errors))
        if network.threads_alive:
            problems.append(f"{network.threads_alive} device thread(s) still alive "
                            "after cleanup")
        if problems:
            runner_error = "; ".join(filter(None, [runner_error, *problems]))
    record.update({
        "outcome": outcome,
        "events": events,
        "timing": {"started_unix": started_unix, "duration_s": time.perf_counter() - started},
        "cleanup": {"threads_alive": network.threads_alive if network else 0,
                    "thread_errors": list(network.thread_errors) if network else []},
        "runner_error": runner_error,
    })
    return record


def run_experiment(settings, base_config=None, progress=None):
    """Run every repeat, appending each record to the output file as it finishes."""
    settings.validate(base_config)
    output = Path(settings.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with open(output, "w", encoding="utf-8") as handle:
        for repeat in range(settings.repeats):
            record = run_once(settings, repeat, base_config)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            records.append(record)
            if progress:
                progress(record)
    return records


def build_parser():
    defaults = ExperimentSettings()
    parser = argparse.ArgumentParser(
        description="Run repeatable best-effort/reliable experiments over real UDP "
                    "on isolated ephemeral ports and save raw JSON Lines records "
                    "(one per repeat; the output file is overwritten).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--mode", choices=MODES, default=defaults.mode,
                        help="best_effort probes each message once; reliable uses stop-and-wait")
    parser.add_argument("--messages", type=int, default=defaults.messages,
                        help="number of messages in the workload")
    parser.add_argument("--message-prefix", default=defaults.message_prefix,
                        help="messages are '<prefix>-0000', '<prefix>-0001', ...")
    parser.add_argument("--timeout", type=float, default=defaults.timeout,
                        help="best-effort probe timeout / reliable retransmission timeout (s)")
    parser.add_argument("--max-retries", type=int, default=defaults.max_retries,
                        help="reliable mode only: retransmissions per packet")
    parser.add_argument("--loss", type=float, default=defaults.loss,
                        help="loss probability on the directed link")
    parser.add_argument("--delay-ms", type=float, default=defaults.delay_ms,
                        help="delay in milliseconds on the directed link")
    parser.add_argument("--link-from", default=defaults.link_from,
                        help="directed link: sending device")
    parser.add_argument("--link-to", default=defaults.link_to,
                        help="directed link: receiving device")
    parser.add_argument("--source", default=defaults.source, help="source host")
    parser.add_argument("--destination", default=defaults.destination,
                        help="destination host")
    parser.add_argument("--seed", type=int, default=defaults.seed,
                        help="base seed; repeat N uses seed + N")
    parser.add_argument("--repeats", type=int, default=defaults.repeats,
                        help="number of independent runs (each gets its own seed)")
    parser.add_argument("--output", default=defaults.output, help="JSON Lines output path")
    return parser


def parse_args(argv=None):
    parser = build_parser()
    namespace = parser.parse_args(argv)
    settings = ExperimentSettings(
        mode=namespace.mode, messages=namespace.messages,
        message_prefix=namespace.message_prefix, timeout=namespace.timeout,
        max_retries=namespace.max_retries, loss=namespace.loss,
        delay_ms=namespace.delay_ms, link_from=namespace.link_from,
        link_to=namespace.link_to, source=namespace.source,
        destination=namespace.destination, seed=namespace.seed,
        repeats=namespace.repeats, output=namespace.output,
    )
    try:
        settings.validate()
    except ValueError as exc:
        parser.error(str(exc))  # exits with status 2
    return settings


def describe(record):
    outcome = record["outcome"]
    if record["runner_error"]:
        summary = f"RUNNER ERROR {record['runner_error']}"
    elif outcome["type"] == "reliable_transfer":
        result = outcome["result"]
        summary = f"{result['status']} {result['sent']}/{result['count']}"
    else:
        matched = sum(item["status"] == "MATCHED" for item in outcome["messages"])
        summary = f"{matched}/{len(outcome['messages'])} matched"
    return (f"repeat {record['repeat']} {record['mode']} seed={record['seed']}: "
            f"{summary} ({record['timing']['duration_s']:.2f}s, "
            f"{len(record['events'])} events)")


def main(argv=None):
    settings = parse_args(argv)
    records = run_experiment(settings, progress=lambda record: print(describe(record)))
    print(f"Wrote {len(records)} record(s) to {settings.output}")
    # FAILED transfers and timeouts are results; only runner errors are failures.
    return 1 if any(record["runner_error"] for record in records) else 0


if __name__ == "__main__":
    sys.exit(main())
