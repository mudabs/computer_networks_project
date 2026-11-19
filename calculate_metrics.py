"""Per-run evaluation metrics from raw experiment records.

Reads the JSON Lines written by ``experiment_runner.py`` /
``run_baseline_matrix.py`` and writes one machine-readable metrics record per
raw run.  It never reruns experiments or modifies its input, and it computes no
cross-run comparisons or plots.

    python calculate_metrics.py --input results/baseline_matrix.jsonl \\
        --output results/baseline_metrics.jsonl

Definitions
  best_effort   unique delivered = MATCHED probes; complete sequence = every
                workload message matched; elapsed = sum of the sequential
                per-probe elapsed times.
  reliable      unique delivered = distinct DATA ``deliver`` events keyed by
                (source, message id, sequence), so duplicates count once;
                complete sequence = outcome COMPLETE and every expected
                sequence number delivered; elapsed = the runner's transfer time.
  retransmissions / failures = counts of ``retransmit`` / ``failure`` events.
"""

import argparse
import json
import sys
from pathlib import Path

RAW_SCHEMA_VERSION = 1
METRICS_SCHEMA_VERSION = 1
MODES = ("best_effort", "reliable")
OUTCOME_TYPES = {"best_effort": "best_effort_probes", "reliable": "reliable_transfer"}


class MalformedRecord(ValueError):
    """A raw record does not follow the experiment runner schema."""


def _require(condition, where, message):
    if not condition:
        raise MalformedRecord(f"{where}: {message}")


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_record(record, where="record"):
    """Check the parts of the runner schema the metrics depend on."""
    _require(isinstance(record, dict), where, "must be a JSON object")
    for key in ("schema_version", "run_id", "repeat", "mode", "seed", "config",
                "settings", "workload", "outcome", "events"):
        _require(key in record, where, f"missing key {key!r}")
    _require(record["schema_version"] == RAW_SCHEMA_VERSION, where,
             f"unsupported schema_version {record['schema_version']!r}")
    _require(isinstance(record["run_id"], str) and record["run_id"], where,
             "run_id must be a non-empty string")
    _require(record["mode"] in MODES, where, f"unknown mode {record['mode']!r}")
    for key in ("repeat", "seed"):
        _require(isinstance(record[key], int) and not isinstance(record[key], bool),
                 where, f"{key} must be an integer")
    config, settings = record["config"], record["settings"]
    _require(isinstance(config, dict) and isinstance(config.get("source"), str)
             and isinstance(config.get("destination"), str), where,
             "config must contain string source and destination")
    _require(isinstance(settings, dict) and _is_number(settings.get("loss"))
             and _is_number(settings.get("delay_ms")), where,
             "settings must contain numeric loss and delay_ms")
    workload = record["workload"]
    _require(isinstance(workload, dict) and isinstance(workload.get("messages"), list)
             and all(isinstance(m, str) for m in workload["messages"]), where,
             "workload.messages must be a list of strings")
    events = record["events"]
    _require(isinstance(events, list), where, "events must be a list")
    for index, item in enumerate(events):
        _require(isinstance(item, dict) and _is_number(item.get("t"))
                 and isinstance(item.get("fields"), dict), where,
                 f"events[{index}] must have a numeric 't' and a 'fields' object")
    matrix = record.get("matrix")
    _require(matrix is None or isinstance(matrix, dict), where, "matrix must be an object")

    outcome = record["outcome"]
    if outcome is None:  # a run that never produced an outcome (runner error)
        _require(record.get("runner_error"), where,
                 "outcome is null but there is no runner_error")
        return
    _require(isinstance(outcome, dict), where, "outcome must be an object or null")
    expected = OUTCOME_TYPES[record["mode"]]
    _require(outcome.get("type") == expected, where,
             f"{record['mode']} run must have outcome type {expected!r}, "
             f"found {outcome.get('type')!r}")
    if record["mode"] == "reliable":
        result = outcome.get("result")
        _require(isinstance(result, dict) and isinstance(result.get("status"), str)
                 and _is_number(result.get("sent")) and _is_number(result.get("count")),
                 where, "reliable outcome.result needs status, sent and count")
        _require(_is_number(outcome.get("elapsed_s")), where,
                 "reliable outcome needs a numeric elapsed_s")
    else:
        probes = outcome.get("messages")
        _require(isinstance(probes, list), where, "outcome.messages must be a list")
        for index, probe in enumerate(probes):
            _require(isinstance(probe, dict) and isinstance(probe.get("message"), str)
                     and probe.get("status") in ("MATCHED", "TIMEOUT")
                     and _is_number(probe.get("elapsed_s")), where,
                     f"outcome.messages[{index}] needs message, status "
                     "MATCHED/TIMEOUT and numeric elapsed_s")


def _count_events(record, name):
    return sum(item["fields"].get("event") == name for item in record["events"])


def _delivered_data_keys(record):
    """Distinct (src, msg_id, seq) of DATA ``deliver`` events from the source."""
    source = record["config"]["source"]
    return {(f.get("src"), f.get("msg_id"), f.get("seq"))
            for f in (item["fields"] for item in record["events"])
            if f.get("event") == "deliver" and f.get("type") == "DATA"
            and f.get("src") == source}


def _best_effort(record, workload):
    probes = record["outcome"]["messages"]
    matched = [p for p in probes if p["status"] == "MATCHED"]
    delivered = len(matched)
    complete = ([p["message"] for p in probes] == workload
                and delivered == len(workload))
    if complete:
        status = "COMPLETE"
    else:
        status = "PARTIAL" if delivered else "FAILED"
    return {
        "unique_delivered": delivered, "complete_sequence": complete,
        "matched_probes": delivered,
        "timeouts": sum(p["status"] == "TIMEOUT" for p in probes),
        "elapsed_s": sum(p["elapsed_s"] for p in probes),
        "status": status, "transfer_status": None,
    }


def _reliable(record, workload):
    keys = _delivered_data_keys(record)
    seqs = {seq for _, _, seq in keys}
    result = record["outcome"]["result"]
    complete = (result["status"] == "COMPLETE"
                and {str(i) for i in range(len(workload))} <= seqs)
    return {
        "unique_delivered": len(keys), "complete_sequence": complete,
        "matched_probes": None, "timeouts": None,   # not applicable to transfers
        "elapsed_s": record["outcome"]["elapsed_s"],
        "status": result["status"], "transfer_status": result["status"],
    }


def compute_metrics(record):
    """Metrics for one validated raw record (unsuccessful runs are kept)."""
    workload = record["workload"]["messages"]
    matrix = record.get("matrix")
    if record["outcome"] is None:  # runner error before any outcome
        measured = {"unique_delivered": 0, "complete_sequence": False,
                    "matched_probes": None, "timeouts": None, "elapsed_s": None,
                    "status": "ERROR", "transfer_status": None}
    elif record["mode"] == "best_effort":
        measured = _best_effort(record, workload)
    else:
        measured = _reliable(record, workload)
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "run_id": record["run_id"],
        "mode": record["mode"],
        "source": record["config"]["source"],
        "destination": record["config"]["destination"],
        "matrix": matrix,
        "loss": record["settings"]["loss"],
        "delay_ms": record["settings"]["delay_ms"],
        "seed": record["seed"],
        "repeat": record["repeat"],
        "workload_size": len(workload),
        **measured,
        "retransmissions": _count_events(record, "retransmit"),
        "failures": _count_events(record, "failure"),
        "runner_error": record.get("runner_error"),
    }


def read_records(path):
    """Parse and validate every non-blank line; raises MalformedRecord."""
    records = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            where = f"{path}:{number}"
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MalformedRecord(f"{where}: invalid JSON ({exc.msg})") from None
            validate_record(record, where)
            records.append(record)
    if not records:
        raise MalformedRecord(f"{path}: contains no records")
    return records


def calculate(input_path, output_path):
    """Write one metrics row per raw row (nothing is written if any row is bad)."""
    if Path(input_path).resolve() == Path(output_path).resolve():
        raise ValueError("output must differ from the raw input file")
    metrics = [compute_metrics(record) for record in read_records(input_path)]
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        for row in metrics:
            handle.write(json.dumps(row) + "\n")
    return metrics


def build_parser():
    parser = argparse.ArgumentParser(
        description="Calculate per-run evaluation metrics from raw experiment JSONL "
                    "(one metrics row per raw run; the input is never modified).")
    parser.add_argument("--input", required=True, help="raw runner/matrix JSONL")
    parser.add_argument("--output", required=True, help="metrics JSONL to write")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        metrics = calculate(args.input, args.output)
    except (OSError, ValueError) as exc:  # MalformedRecord is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {len(metrics)} metrics record(s) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
