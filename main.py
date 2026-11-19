import argparse
import logging
import subprocess
import sys
import time

from config import Config
from host import Host
from router import Router

PROBE_TIMEOUT = 3.0

def parse_args(config):
    device_ids = tuple(config.data["devices"])
    parser = argparse.ArgumentParser(description="Mini-Internet device launcher")

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "device",
        nargs="?",
        choices=device_ids,
        help="Device to start individually",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Start all devices in data/network.json as separate processes",
    )

    parser.add_argument(
        "--run-id",
        default="default",
        help="Identifier included in log messages",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )

    parser.add_argument("--probe", metavar="DEST", help="Send one probe from the selected host")
    parser.add_argument(
        "--mode",
        choices=["best_effort", "reliable"],
        default="best_effort",
        help="Host mode; reliable hosts ACK DATA/FIN and can run --transfer",
    )
    parser.add_argument("--message", help="Probe message (prompted for if omitted)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=PROBE_TIMEOUT,
        help="Probe timeout and reliable-transfer retransmission timeout (seconds)",
    )
    parser.add_argument(
        "--transfer", metavar="DEST", help="Send --messages reliably to DEST (stop-and-wait)"
    )
    parser.add_argument("--messages", nargs="+", help="Messages for --transfer")
    parser.add_argument(
        "--headless",
        action="store_true",
        help=argparse.SUPPRESS,  # internal: set by --all so children never prompt
    )
    args = parser.parse_args()
    if args.probe and args.transfer:
        parser.error("use either --probe or --transfer, not both")
    if args.transfer and args.mode != "reliable":
        parser.error("--transfer requires --mode reliable")
    if args.probe and args.mode == "reliable":
        parser.error("--probe is best-effort only; use --transfer for reliable delivery")
    for option, destination in (("--probe", args.probe), ("--transfer", args.transfer)):
        if destination:
            if args.device is None or config.get_device(args.device)["type"] != "host":
                parser.error(f"{option} requires a source host device")
            if destination not in device_ids or config.get_device(destination)["type"] != "host":
                parser.error(f"{option} destination must be a configured host")
            if destination == args.device:
                parser.error(f"{option} destination must differ from source")
    if args.transfer and not args.messages:
        parser.error("--transfer requires --messages")
    if args.messages and not args.transfer:
        parser.error("--messages requires --transfer")
    if args.message is not None and not args.probe:
        parser.error("--message requires --probe")
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be finite and positive")
    if args.all and (args.probe or args.transfer):
        parser.error("--all cannot be combined with --probe or --transfer")
    return args


def configure_logging(level):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_device(device_id, config, run_id, probe=None, message=None,
               timeout=PROBE_TIMEOUT, mode="best_effort", transfer=None,
               messages=None, headless=False):
    device_type = config.get_device(device_id)["type"]
    if device_type == "host":
        device = Host(device_id, config, run_id=run_id, timeout=timeout, mode=mode)
    elif device_type == "router":
        device = Router(device_id, config, run_id=run_id)
    else:
        raise ValueError(f"Unknown device type {device_type!r} for {device_id}")
    try:
        if probe:
            if message is None:
                message = input(f"Message from {device_id} to {probe}: ")
            result = device.probe(probe, message, timeout=timeout)
            print("TIMEOUT" if result is None else f"MATCHED {result!r}")
            return 1 if result is None else 0
        if transfer:
            result = device.start_transfer(messages, transfer)
            print(f"TRANSFER {result['status']}")
            return 0 if result["status"] == "COMPLETE" else 1
        if (device_type == "host" and mode == "best_effort"
                and sys.stdin.isatty() and not headless):
            peers = [name for name, entry in config.data["devices"].items()
                     if entry["type"] == "host" and name != device_id]
            if len(peers) != 1:
                raise ValueError("Interactive mode requires exactly one other host")
            device.interactive(peers[0], timeout=timeout)
        else:
            device.run()
        return 0
    finally:
        device.close()


def run_all(device_ids, run_id, log_level, mode="best_effort", timeout=PROBE_TIMEOUT):
    processes = []

    try:
        for device_id in device_ids:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-u",
                    __file__,
                    device_id,
                    "--run-id",
                    run_id,
                    "--log-level",
                    log_level,
                    "--mode",
                    mode,
                    "--timeout",
                    str(timeout),
                    "--headless",
                ],
                stdin=subprocess.DEVNULL,
            )
            processes.append(process)

        logging.info("Started all device processes")

        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(
                        f"Device process exited unexpectedly "
                        f"with code {return_code}"
                    )
            time.sleep(0.2)

    except (KeyboardInterrupt, RuntimeError):
        logging.info("Stopping network processes")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()

        for process in processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    config = Config()
    device_ids = tuple(config.data["devices"])
    args = parse_args(config)
    configure_logging(args.log_level)

    if args.all:
        run_all(device_ids, args.run_id, args.log_level, args.mode, args.timeout)
        return 0

    if args.device is None:
        logging.error("Specify a device ID or use --all")
        return 2

    try:
        return run_device(args.device, config, args.run_id, args.probe,
                          message=args.message, timeout=args.timeout,
                          mode=args.mode, transfer=args.transfer,
                          messages=args.messages, headless=args.headless)
    except (KeyboardInterrupt, EOFError):
        logging.info("Shutdown requested")
    except (OSError, KeyError, ValueError) as exc:
        logging.error("Could not start device: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
