import argparse
import logging
import subprocess
import sys
import time

from config import Config
from host import Host
from router import Router


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
        help="Start all configured devices",
    )

    parser.add_argument("--run-id", default="default")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--mode",
        choices=["best_effort", "reliable"],
        default="best_effort",
        help="Host sending mode",
    )
    parser.add_argument("--probe", metavar="DEST")
    parser.add_argument("--message", default="hello")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--transfer", metavar="DEST")
    parser.add_argument("--messages", nargs="+")

    args = parser.parse_args()

    if args.probe and args.transfer:
        parser.error("Use either --probe or --transfer, not both")

    if args.probe or args.transfer:
        if args.device is None:
            parser.error("A source host device is required")

        if config.get_device(args.device)["type"] != "host":
            parser.error("Source device must be a host")

    for destination in (args.probe, args.transfer):
        if destination:
            if (
                destination not in device_ids
                or config.get_device(destination)["type"] != "host"
            ):
                parser.error("Destination must be a configured host")
            if destination == args.device:
                parser.error("Source and destination must differ")

    if args.transfer and not args.messages:
        parser.error("--transfer requires --messages")

    if args.messages and not args.transfer:
        parser.error("--messages requires --transfer")

    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be finite and positive")

    if args.all and (args.probe or args.transfer):
        parser.error("--all cannot be combined with probe or transfer")

    return args


def configure_logging(level):
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def run_device(
    device_id,
    config,
    run_id,
    probe=None,
    message="hello",
    timeout=3.0,
    mode="best_effort",
    transfer=None,
    messages=None,
):
    device_type = config.get_device(device_id)["type"]

    if device_type == "host":
        device = Host(
            device_id,
            config,
            run_id=run_id,
            timeout=timeout,
            mode=mode,
        )
    elif device_type == "router":
        device = Router(device_id, config, run_id=run_id)
    else:
        raise ValueError(f"Unknown device type {device_type!r}")

    try:
        if probe:
            result = device.probe(probe, message, timeout)
            print("TIMEOUT" if result is None else f"MATCHED {result!r}")
            return 1 if result is None else 0

        if transfer:
            result = device.start_transfer(messages, transfer)
            print(f"TRANSFER {result['status']}")
            return 0 if result["status"] == "COMPLETE" else 1

        if device_type == "host" and sys.stdin.isatty():
            peers = [
                name
                for name, entry in config.data["devices"].items()
                if entry["type"] == "host" and name != device_id
            ]
            if len(peers) != 1:
                raise ValueError("Interactive mode requires exactly one other host")
            device.interactive(peers[0], timeout)
        else:
            device.run()

        return 0
    finally:
        device.close()


def run_all(device_ids, run_id, log_level, mode):
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
                ],
                stdin=subprocess.DEVNULL,
            )
            processes.append(process)

        logging.info("Started all device processes")

        while True:
            for process in processes:
                return_code = process.poll()
                if return_code is not None:
                    raise RuntimeError(f"Device process exited with code {return_code}")
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
    args = parse_args(config)
    configure_logging(args.log_level)

    device_ids = tuple(config.data["devices"])

    if args.all:
        run_all(device_ids, args.run_id, args.log_level, args.mode)
        return 0

    if args.device is None:
        logging.error("Specify a device ID or use --all")
        return 2

    try:
        return run_device(
            args.device,
            config,
            args.run_id,
            probe=args.probe,
            message=args.message,
            timeout=args.timeout,
            mode=args.mode,
            transfer=args.transfer,
            messages=args.messages,
        )
    except KeyboardInterrupt:
        logging.info("Shutdown requested")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        logging.error("Could not run device: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
