import argparse
import logging
import subprocess
import sys
import time

from config import Config
from host import Host
from router import Router

DEVICES = ("H1", "H2", "A", "B", "C", "D")


def parse_args():
    parser = argparse.ArgumentParser(description="Mini-Internet device launcher")

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "device",
        nargs="?",
        choices=DEVICES,
        help="Device to start individually",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Start all six devices as separate processes",
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

    return parser.parse_args()


def configure_logging(level):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_device(device_id, config, run_id):
    device_type = config.get_device(device_id)["type"]

    if device_type == "host":
        device = Host(device_id, config, run_id=run_id)
    elif device_type == "router":
        device = Router(device_id, config)
    else:
        raise ValueError(f"Unknown device type {device_type!r} for {device_id}")

    try:
        logging.info("Starting %s (%s)", device_id, device_type)
        device.run()
    finally:
        device.close()
        logging.info("Stopped %s", device_id)


def run_all(run_id, log_level):
    processes = []

    try:
        for device_id in DEVICES:
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
                ]
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
    args = parse_args()
    configure_logging(args.log_level)

    if args.all:
        run_all(args.run_id, args.log_level)
        return 0

    if args.device is None:
        logging.error("Specify a device ID or use --all")
        return 2

    config = Config()

    try:
        run_device(args.device, config, args.run_id)
    except KeyboardInterrupt:
        logging.info("Shutdown requested")
    except (OSError, KeyError, ValueError) as exc:
        logging.error("Could not start device: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())