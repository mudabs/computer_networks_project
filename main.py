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
    args = parser.parse_args()
    if args.probe:
        if args.device is None or config.get_device(args.device)["type"] != "host":
            parser.error("--probe requires a source host device")
        if args.probe not in device_ids or config.get_device(args.probe)["type"] != "host":
            parser.error("--probe destination must be a configured host")
        if args.probe == args.device:
            parser.error("probe destination must differ from source")
    return args


def configure_logging(level):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_device(device_id, config, run_id, probe=None):
    device_type = config.get_device(device_id)["type"]
    if device_type == "host":
        device = Host(device_id, config, run_id=run_id)
    elif device_type == "router":
        device = Router(device_id, config, run_id=run_id)
    else:
        raise ValueError(f"Unknown device type {device_type!r} for {device_id}")
    try:
        if probe:
            message = input(f"Message from {device_id} to {probe}: ")
            result = device.probe(probe, message, timeout=PROBE_TIMEOUT)
            print("TIMEOUT" if result is None else f"MATCHED {result!r}")
            return 1 if result is None else 0
        if device_type == "host" and sys.stdin.isatty():
            peers = [name for name, entry in config.data["devices"].items()
                     if entry["type"] == "host" and name != device_id]
            if len(peers) != 1:
                raise ValueError("Interactive mode requires exactly one other host")
            device.interactive(peers[0], timeout=PROBE_TIMEOUT)
        else:
            device.run()
        return 0
    finally:
        device.close()


def run_all(device_ids, run_id, log_level):
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
        run_all(device_ids, args.run_id, args.log_level)
        return 0

    if args.device is None:
        logging.error("Specify a device ID or use --all")
        return 2

    try:
        return run_device(args.device, config, args.run_id,
                          args.probe)
    except (KeyboardInterrupt, EOFError):
        logging.info("Shutdown requested")
    except (OSError, KeyError, ValueError) as exc:
        logging.error("Could not start device: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
