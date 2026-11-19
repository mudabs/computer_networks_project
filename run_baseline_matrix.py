"""Core baseline matrix: best-effort probes across loss/delay conditions.

Runs the fixed set of cases through ``experiment_runner`` (real UDP, isolated
ephemeral ports, existing LinkSender loss/delay) and writes every raw record,
with matrix metadata added, to one JSON Lines file.  It computes no aggregates;
timeouts and per-probe elapsed times are kept for later analysis.

    python run_baseline_matrix.py --output results/baseline_matrix.jsonl
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import experiment_runner as runner

MODE = "best_effort"
PROBES = 20
PROBE_TIMEOUT = 1.0
CONDITION_LINK = ("A", "C")  # on the default H1 -> H2 route
BASE_SEED = 42
REPEATS = 3                  # run seeds 42, 43, 44 (= BASE_SEED + repeat)
LOSSES = (0.0, 0.05)
DELAYS_MS = (0, 40)
DEFAULT_OUTPUT = "results/baseline_matrix.jsonl"


@dataclass(frozen=True)
class MatrixCase:
    case_type: str       # "core" or "directional_acceptance"
    source: str
    destination: str
    loss: float
    delay_ms: int
    repeat: int          # run seed = BASE_SEED + repeat
    probes: int = PROBES

    @property
    def seed(self):
        return runner.derive_run_seed(BASE_SEED, self.repeat)

    @property
    def case_id(self):
        if self.case_type == "core":
            return f"core-loss{self.loss}-delay{self.delay_ms}-seed{self.seed}"
        return f"accept-{self.source}-{self.destination}"

    @property
    def condition_link(self):
        """The directed link carrying the condition (none for acceptance checks)."""
        return None if self.case_type != "core" else "->".join(CONDITION_LINK)

    def settings(self, timeout=PROBE_TIMEOUT, output=DEFAULT_OUTPUT):
        return runner.ExperimentSettings(
            mode=MODE, messages=self.probes, message_prefix="probe", timeout=timeout,
            loss=self.loss, delay_ms=self.delay_ms,
            link_from=CONDITION_LINK[0], link_to=CONDITION_LINK[1],
            source=self.source, destination=self.destination,
            seed=BASE_SEED, repeats=REPEATS if self.case_type == "core" else 1,
            output=output)

    def metadata(self, index):
        return {
            "case_id": self.case_id, "case_type": self.case_type, "index": index,
            "source": self.source, "destination": self.destination,
            "loss": self.loss, "delay_ms": self.delay_ms,
            "seed": self.seed, "base_seed": BASE_SEED, "repeat": self.repeat,
            "probes": self.probes, "condition_link": self.condition_link,
        }


def core_cases(probes=PROBES):
    """The 12 core runs: loss x delay x seed, in a fixed order."""
    return [MatrixCase("core", "H1", "H2", loss, delay, repeat, probes)
            for loss in LOSSES for delay in DELAYS_MS for repeat in range(REPEATS)]


def acceptance_cases(probes=PROBES):
    """Loss-free, delay-free checks in each direction."""
    return [MatrixCase("directional_acceptance", source, destination, 0.0, 0, 0, probes)
            for source, destination in (("H1", "H2"), ("H2", "H1"))]


def all_cases(probes=PROBES):
    return core_cases(probes) + acceptance_cases(probes)


def run_matrix(output=DEFAULT_OUTPUT, cases=None, timeout=PROBE_TIMEOUT,
               overwrite=False, progress=None):
    """Run every case, writing all records to one file; returns the records.

    Timeouts are measurements, not errors.  An existing output file is never
    replaced unless ``overwrite`` is set.
    """
    cases = all_cases() if cases is None else list(cases)
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("matrix cases must have unique ids")
    path = Path(output)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} exists; use --overwrite to replace it")
    for case in cases:
        case.settings(timeout, output).validate()  # fail before running anything
    path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with open(path, "w", encoding="utf-8") as handle:
        for index, case in enumerate(cases):
            record = runner.run_once(case.settings(timeout, output), case.repeat,
                                     run_id=case.case_id)
            record["matrix"] = case.metadata(index)
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            records.append(record)
            if progress:
                progress(record)
    return records


def describe(record):
    matrix = record["matrix"]
    probes = record["outcome"]["messages"] if record["outcome"] else []
    matched = sum(item["status"] == "MATCHED" for item in probes)
    return (f"[{matrix['index'] + 1}] {matrix['case_id']}: {matched}/{len(probes)} matched, "
            f"{len(probes) - matched} timeout "
            f"({record['timing']['duration_s']:.2f}s)"
            + (f" RUNNER ERROR {record['runner_error']}" if record["runner_error"] else ""))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run the core baseline matrix (12 core runs of 20 best-effort probes "
                    "over loss {0, 0.05} x delay {0, 40} ms x seeds {42, 43, 44} on "
                    "A->C, plus two loss-free directional checks) and save all raw "
                    "records to one JSON Lines file.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="JSONL output path (default: %(default)s)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace the output file if it already exists")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        records = run_matrix(args.output, overwrite=args.overwrite,
                             progress=lambda record: print(describe(record)))
    except (FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {len(records)} record(s) to {args.output}")
    # Timeouts are results; only runner errors make the exit status nonzero.
    return 1 if any(record["runner_error"] for record in records) else 0


if __name__ == "__main__":
    sys.exit(main())
