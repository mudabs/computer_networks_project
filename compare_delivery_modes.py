"""Pair best-effort baseline runs with matching stop-and-wait (reliable) runs.

The best-effort side is *reused* from the committed baseline
(``results/baseline_matrix.jsonl`` / ``results/baseline_metrics.jsonl``) and is
not rerun.  For each of those cases (H1 -> H2, directed condition A->C, 0 ms
delay, loss 0 and 0.05, seeds 42/43/44, the same 20-message workload and
timeout) one reliable run is executed through ``experiment_runner.run_once``
with the identical workload, route and seeded condition, ``max_retries=3`` and
the same sequential one-message-at-a-time pacing.  Both sides are verified to be
compatible before they are paired, and the reliable metrics come from the public
``calculate_metrics.compute_metrics``.

    python compare_delivery_modes.py \\
        --raw results/baseline_matrix.jsonl --metrics results/baseline_metrics.jsonl \\
        --output results/mode_comparison.jsonl

Outputs one pairing row per case (``--output``) with metrics for both modes and
the best-effort run only referenced by id, plus the raw reliable records
(``--reliable-raw-output``) so reliable failures can be diagnosed.
"""

import argparse
import json
import sys
from pathlib import Path

import calculate_metrics
import experiment_runner as runner

SCHEMA_VERSION = 1
SOURCE, DESTINATION = "H1", "H2"
CONDITION_LINK = "A->C"
DELAY_MS = 0
LOSSES = (0.0, 0.05)
SEEDS = (42, 43, 44)
MESSAGES = 20
MESSAGE_PREFIX = "probe"
TIMEOUT = 1.0
MAX_RETRIES = 3
PACING = "sequential: one message outstanding at a time, no concurrency"
BASE_SEED = 42


class IncompatiblePair(ValueError):
    """Best-effort and reliable runs differ in more than the delivery mode."""


def load_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def select_best_effort_cases(raw_records):
    """The six reusable baseline records, ordered by loss then seed."""
    chosen = {}
    for record in raw_records:
        meta = record.get("matrix") or {}
        if (record.get("mode") == "best_effort" and meta.get("case_type") == "core"
                and meta.get("source") == SOURCE and meta.get("destination") == DESTINATION
                and meta.get("condition_link") == CONDITION_LINK
                and meta.get("delay_ms") == DELAY_MS and meta.get("loss") in LOSSES
                and meta.get("seed") in SEEDS):
            key = (meta["loss"], meta["seed"])
            if key in chosen:
                raise ValueError(f"duplicate baseline case loss={key[0]} seed={key[1]}")
            chosen[key] = record
    wanted = [(loss, seed) for loss in LOSSES for seed in SEEDS]
    missing = [key for key in wanted if key not in chosen]
    if missing:
        raise ValueError(f"baseline is missing cases (loss, seed): {missing}")
    cases = [chosen[key] for key in wanted]
    for record in cases:
        settings = record["settings"]
        expected = {"message_count": MESSAGES, "message_prefix": MESSAGE_PREFIX,
                    "timeout": TIMEOUT}
        wrong = {k: settings.get(k) for k, v in expected.items() if settings.get(k) != v}
        if wrong:
            raise ValueError(f"baseline {record['run_id']} has unexpected settings {wrong}")
    return cases


def reliable_settings_for(best_effort, max_retries=MAX_RETRIES):
    """Reliable-run settings that copy everything but the mode from the baseline."""
    settings = best_effort["settings"]
    return runner.ExperimentSettings(
        mode="reliable", messages=settings["message_count"],
        message_prefix=settings["message_prefix"], timeout=settings["timeout"],
        max_retries=max_retries, loss=settings["loss"], delay_ms=settings["delay_ms"],
        link_from=settings["link_from"], link_to=settings["link_to"],
        source=best_effort["config"]["source"],
        destination=best_effort["config"]["destination"],
        seed=best_effort["base_seed"], repeats=1, output="-")


def reliable_run_id(best_effort):
    meta = best_effort["matrix"]
    return f"compare-reliable-loss{meta['loss']}-delay{meta['delay_ms']}-seed{meta['seed']}"


def check_compatible(best_effort, reliable, max_retries=MAX_RETRIES):
    """Raise IncompatiblePair unless the runs differ only in delivery mode."""
    problems = []

    def same(label, a, b):
        if a != b:
            problems.append(f"{label}: best_effort={a!r} reliable={b!r}")

    if best_effort.get("mode") != "best_effort" or reliable.get("mode") != "reliable":
        problems.append(f"modes must be best_effort/reliable, found "
                        f"{best_effort.get('mode')!r}/{reliable.get('mode')!r}")
    for key in ("seed", "base_seed", "repeat"):
        same(key, best_effort.get(key), reliable.get(key))
    same("workload", best_effort["workload"]["messages"], reliable["workload"]["messages"])
    for key in ("source", "destination", "path", "routes", "conditions"):
        same(f"config.{key}", best_effort["config"].get(key), reliable["config"].get(key))
    for key in ("loss", "delay_ms", "timeout", "message_count", "message_prefix",
                "link_from", "link_to"):
        same(f"settings.{key}", best_effort["settings"].get(key),
             reliable["settings"].get(key))
    same("settings.max_retries (reliable)", max_retries, reliable["settings"].get("max_retries"))
    if best_effort["settings"].get("max_retries") is not None:
        problems.append("best_effort run must not have max_retries")
    if problems:
        raise IncompatiblePair(
            f"{best_effort.get('run_id')} vs {reliable.get('run_id')}: " + "; ".join(problems))


def check_metrics_match(raw_record, metrics_row):
    """The reused metrics row must be what the raw record computes to."""
    if metrics_row is None:
        raise ValueError(f"no metrics row for baseline run {raw_record['run_id']}")
    expected = calculate_metrics.compute_metrics(raw_record)
    if expected != metrics_row:
        raise ValueError(f"metrics row for {raw_record['run_id']} does not match its raw "
                         "record; regenerate it with calculate_metrics.py")


def build_pair(best_effort, best_effort_metrics, reliable, reliable_metrics,
               max_retries=MAX_RETRIES):
    check_compatible(best_effort, reliable, max_retries)
    meta = best_effort["matrix"]
    return {
        "schema_version": SCHEMA_VERSION,
        "pair_id": f"loss{meta['loss']}-delay{meta['delay_ms']}-seed{meta['seed']}",
        "pair": {
            "loss": meta["loss"], "delay_ms": meta["delay_ms"], "seed": best_effort["seed"],
            "base_seed": best_effort["base_seed"], "repeat": best_effort["repeat"],
            "source": best_effort["config"]["source"],
            "destination": best_effort["config"]["destination"],
            "condition_link": meta["condition_link"],
            "path": best_effort["config"]["path"],
            "workload_size": len(best_effort["workload"]["messages"]),
            "message_prefix": best_effort["settings"]["message_prefix"],
            "workload": best_effort["workload"]["messages"],
            "timeout": best_effort["settings"]["timeout"],
            "reliable_max_retries": max_retries,
            "pacing": PACING,
        },
        "best_effort": {"run_id": best_effort["run_id"], "source": "reused",
                        "metrics": best_effort_metrics},
        "reliable": {"run_id": reliable["run_id"], "source": "run for this comparison",
                     "metrics": reliable_metrics},
    }


def run_comparison(raw_path, metrics_path, output_path, reliable_raw_path,
                   max_retries=MAX_RETRIES, run=runner.run_once, progress=None):
    """Run the six reliable cases, pair them, and write both output files."""
    paths = {Path(p).resolve() for p in (raw_path, metrics_path)}
    if {Path(output_path).resolve(), Path(reliable_raw_path).resolve()} & paths \
            or Path(output_path).resolve() == Path(reliable_raw_path).resolve():
        raise ValueError("output files must differ from the inputs and from each other")
    cases = select_best_effort_cases(load_jsonl(raw_path))
    metrics = {row["run_id"]: row for row in load_jsonl(metrics_path)}
    for record in cases:  # validate everything before any run starts
        check_metrics_match(record, metrics.get(record["run_id"]))
    pairs, reliable_records = [], []
    for record in cases:
        settings = reliable_settings_for(record, max_retries)
        settings.validate()
        reliable = run(settings, record["repeat"], run_id=reliable_run_id(record))
        pairs.append(build_pair(record, metrics[record["run_id"]], reliable,
                                calculate_metrics.compute_metrics(reliable), max_retries))
        reliable_records.append(reliable)
        if progress:
            progress(pairs[-1])
    for path, rows in ((reliable_raw_path, reliable_records), (output_path, pairs)):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
    return pairs


def describe(pair):
    be, rel = pair["best_effort"]["metrics"], pair["reliable"]["metrics"]
    return (f"{pair['pair_id']}: best_effort {be['unique_delivered']}/{be['workload_size']} "
            f"({be['status']}, {be['timeouts']} timeouts, {be['elapsed_s']:.2f}s) | "
            f"reliable {rel['unique_delivered']}/{rel['workload_size']} ({rel['status']}, "
            f"{rel['retransmissions']} retransmits, {rel['failures']} failures, "
            f"{rel['elapsed_s']:.2f}s)")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Pair the baseline best-effort runs (reused, not rerun) with matching "
                    "reliable stop-and-wait runs and write machine-readable comparison rows.")
    parser.add_argument("--raw", default="results/baseline_matrix.jsonl",
                        help="raw baseline records (default: %(default)s)")
    parser.add_argument("--metrics", default="results/baseline_metrics.jsonl",
                        help="baseline metrics from calculate_metrics.py (default: %(default)s)")
    parser.add_argument("--output", default="results/mode_comparison.jsonl",
                        help="paired comparison rows (default: %(default)s)")
    parser.add_argument("--reliable-raw-output", default="results/mode_comparison_reliable_raw.jsonl",
                        help="raw reliable run records (default: %(default)s)")
    parser.add_argument("--max-retries", type=int, default=MAX_RETRIES,
                        help="reliable retransmissions per packet (default: %(default)s)")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.max_retries < 0:
        print("error: --max-retries must be non-negative", file=sys.stderr)
        return 2
    try:
        pairs = run_comparison(args.raw, args.metrics, args.output, args.reliable_raw_output,
                               args.max_retries, progress=lambda pair: print(describe(pair)))
    except (OSError, ValueError, KeyError) as exc:  # IncompatiblePair is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {len(pairs)} pair(s) to {args.output} and raw reliable records to "
          f"{args.reliable_raw_output}")
    # Reliable failures are results; only runner errors are failures of this tool.
    errors = [p["pair_id"] for p in pairs if p["reliable"]["metrics"]["runner_error"]]
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
