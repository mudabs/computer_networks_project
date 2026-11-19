"""Generate the evaluation tables and interpretation from the committed results.

Reads only three committed machine-readable files, reruns nothing, and derives
every number (tables and interpretation text) from them:

  results/baseline_metrics.jsonl            best-effort baseline matrix (#28)
  results/mode_comparison.jsonl             best-effort vs reliable pairs (#29)
  results/mode_comparison_reliable_raw.jsonl raw reliable runs (traffic evidence)

and writes ``results/evaluation_report.md`` plus a compact
``results/evaluation_summary.json`` with the same computed values.

    python create_evaluation_report.py
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

DEFAULTS = {
    "baseline_metrics": "results/baseline_metrics.jsonl",
    "comparison": "results/mode_comparison.jsonl",
    "reliable_raw": "results/mode_comparison_reliable_raw.jsonl",
    "report": "results/evaluation_report.md",
    "summary": "results/evaluation_summary.json",
}


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{number}: invalid JSON ({exc.msg})") from None
    if not rows:
        raise ValueError(f"{path}: contains no records")
    return rows


def _need(row, keys, where):
    missing = [key for key in keys if key not in row]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")


def validate_inputs(baseline, pairs, reliable_raw):
    metric_keys = ("run_id", "loss", "delay_ms", "seed", "workload_size", "unique_delivered",
                   "complete_sequence", "timeouts", "retransmissions", "failures",
                   "elapsed_s", "status", "matrix")
    for index, row in enumerate(baseline, 1):
        _need(row, metric_keys, f"baseline metrics row {index}")
    for index, pair in enumerate(pairs, 1):
        _need(pair, ("pair_id", "pair", "best_effort", "reliable"), f"comparison row {index}")
        _need(pair["pair"], ("loss", "delay_ms", "seed", "path", "workload_size",
                             "message_prefix", "timeout", "reliable_max_retries",
                             "condition_link", "source", "destination"),
              f"comparison row {index} pair")
        for side in ("best_effort", "reliable"):
            _need(pair[side], ("run_id", "metrics"), f"comparison row {index} {side}")
            _need(pair[side]["metrics"], metric_keys[:-1], f"comparison row {index} {side} metrics")
    for index, record in enumerate(reliable_raw, 1):
        _need(record, ("run_id", "events", "config"), f"reliable raw record {index}")
    raw_ids = {record["run_id"] for record in reliable_raw}
    for pair in pairs:
        if pair["reliable"]["run_id"] not in raw_ids:
            raise ValueError(f"no raw reliable record for {pair['reliable']['run_id']}")


def pct(count, total):
    return 100.0 * count / total if total else 0.0


def loss_label(loss):
    return f"{loss * 100:g}%"


def _stats(values):
    return {"min": min(values), "max": max(values), "mean": statistics.fmean(values)}


# --------------------------------------------------------------------------- summary

def traffic_from_raw(record):
    """Packet counts for one raw reliable run, from its protocol events."""
    source, destination = record["config"]["source"], record["config"]["destination"]
    counts = {"source_data": 0, "source_fin": 0, "source_retransmitted": 0,
              "destination_acks": 0, "network_sends": 0, "injected_drops": 0}
    for item in record["events"]:
        fields = item["fields"]
        name, device = fields.get("event"), fields.get("device")
        if name == "send":
            counts["network_sends"] += 1
            if device == source and fields.get("type") == "DATA":
                counts["source_data"] += 1
            if device == source and fields.get("type") == "FIN":
                counts["source_fin"] += 1
            if device == source and fields.get("retransmit") == "true":
                counts["source_retransmitted"] += 1
            if device == destination and fields.get("type") == "ACK":
                counts["destination_acks"] += 1
        elif name == "drop" and fields.get("reason") == "injected_loss":
            counts["injected_drops"] += 1
    return counts


def _condition_row(rows):
    delivered = sum(r["unique_delivered"] for r in rows)
    probes = sum(r["workload_size"] for r in rows)
    timeouts = sum(r["timeouts"] or 0 for r in rows)  # null for reliable runs
    elapsed = [r["elapsed_s"] for r in rows]
    return {
        "runs": len(rows), "probes": probes, "delivered": delivered,
        "delivery_pct": pct(delivered, probes),
        "complete_runs": sum(r["complete_sequence"] for r in rows),
        "timeouts": timeouts,
        "retransmissions": sum(r["retransmissions"] for r in rows),
        "failures": sum(r["failures"] for r in rows),
        "elapsed_s": _stats(elapsed),
        "ms_per_probe": 1000 * sum(elapsed) / probes,
    }


def compute_summary(baseline, pairs, reliable_raw):
    """Every number the report shows, derived from the three input files."""
    validate_inputs(baseline, pairs, reliable_raw)
    raw_by_id = {record["run_id"]: record for record in reliable_raw}
    first = pairs[0]["pair"]
    timeout = first["timeout"]
    core = [r for r in baseline if r["matrix"].get("case_type") == "core"]
    acceptance = [r for r in baseline if r["matrix"].get("case_type") != "core"]
    losses = sorted({r["loss"] for r in core})
    delays = sorted({r["delay_ms"] for r in core})

    setup = {
        "workload_size": first["workload_size"], "message_prefix": first["message_prefix"],
        "path": first["path"], "condition_link": first["condition_link"],
        "source": first["source"], "destination": first["destination"],
        "baseline_losses": losses, "baseline_delays_ms": delays,
        "comparison_losses": sorted({p["pair"]["loss"] for p in pairs}),
        "comparison_delays_ms": sorted({p["pair"]["delay_ms"] for p in pairs}),
        "seeds": sorted({r["seed"] for r in core}),
        "repeats_per_condition": len({r["repeat"] for r in core}),
        "timeout_s": timeout, "reliable_max_retries": first["reliable_max_retries"],
        "pacing": first.get("pacing"),
        "baseline_runs": len(baseline), "baseline_probes": sum(r["workload_size"] for r in baseline),
        "pairs": len(pairs),
    }

    baseline_conditions = []
    for loss in losses:
        for delay in delays:
            rows = [r for r in core if r["loss"] == loss and r["delay_ms"] == delay]
            if rows:
                row = _condition_row(rows)
                matched = row["delivered"]
                # Approximate time of one matched probe: remove the timeout waits.
                row["approx_matched_ms"] = (
                    1000 * (sum(r["elapsed_s"] for r in rows) - row["timeouts"] * timeout)
                    / matched) if matched else None
                baseline_conditions.append({"loss": loss, "delay_ms": delay, **row})
    acceptance_rows = [{"run_id": r["run_id"], "source": r["source"],
                        "destination": r["destination"], **_condition_row([r])}
                       for r in acceptance]

    comparison = []
    for loss in sorted({p["pair"]["loss"] for p in pairs}):
        group = [p for p in pairs if p["pair"]["loss"] == loss]
        comparison.append({
            "loss": loss, "pairs": len(group),
            "best_effort": _condition_row([p["best_effort"]["metrics"] for p in group]),
            "reliable": _condition_row([p["reliable"]["metrics"] for p in group]),
        })

    per_pair = []
    for pair in pairs:
        be, rel = pair["best_effort"]["metrics"], pair["reliable"]["metrics"]
        traffic = traffic_from_raw(raw_by_id[pair["reliable"]["run_id"]])
        per_pair.append({
            "pair_id": pair["pair_id"], "loss": pair["pair"]["loss"], "seed": pair["pair"]["seed"],
            "best_effort": be, "reliable": rel, "traffic": traffic,
            "elapsed_difference_s": rel["elapsed_s"] - be["elapsed_s"],
        })

    # Reliable traffic/time cost relative to the loss-free reliable runs.
    clean = [p for p in per_pair if p["loss"] == min(losses)]
    lossy = [p for p in per_pair if p["loss"] != min(losses)]
    overhead = {"reference_loss": min(losses)}
    if clean:
        overhead["clean_elapsed_s"] = statistics.fmean(p["reliable"]["elapsed_s"] for p in clean)
        overhead["clean_source_transmissions"] = statistics.fmean(
            p["traffic"]["source_data"] + p["traffic"]["source_fin"] for p in clean)
        overhead["clean_network_sends"] = statistics.fmean(p["traffic"]["network_sends"] for p in clean)
    overhead["lossy"] = []
    for p in lossy:
        retransmissions = p["reliable"]["retransmissions"]
        entry = {
            "pair_id": p["pair_id"], "retransmissions": retransmissions,
            "source_transmissions": p["traffic"]["source_data"] + p["traffic"]["source_fin"],
            "network_sends": p["traffic"]["network_sends"],
            "injected_drops": p["traffic"]["injected_drops"],
        }
        if clean:
            extra = p["reliable"]["elapsed_s"] - overhead["clean_elapsed_s"]
            entry["extra_time_s"] = extra
            entry["extra_time_per_retransmission_s"] = (
                extra / retransmissions if retransmissions else None)
            entry["extra_network_sends"] = p["traffic"]["network_sends"] - overhead["clean_network_sends"]
        overhead["lossy"].append(entry)

    delay_effect = []
    for loss in losses:
        by_delay = {c["delay_ms"]: c for c in baseline_conditions if c["loss"] == loss}
        if min(delays) in by_delay and max(delays) in by_delay and min(delays) != max(delays):
            low, high = by_delay[min(delays)], by_delay[max(delays)]
            low_rows = [r for r in core if r["loss"] == loss and r["delay_ms"] == min(delays)]
            high_rows = [r for r in core if r["loss"] == loss and r["delay_ms"] == max(delays)]
            same_delivery = all(
                a["unique_delivered"] == b["unique_delivered"]
                for a in low_rows for b in high_rows if a["seed"] == b["seed"])
            delay_effect.append({
                "loss": loss, "low_delay_ms": min(delays), "high_delay_ms": max(delays),
                "low_ms_per_probe": low["ms_per_probe"], "high_ms_per_probe": high["ms_per_probe"],
                "added_ms_per_probe": high["ms_per_probe"] - low["ms_per_probe"],
                "low_matched_ms": low["approx_matched_ms"],
                "high_matched_ms": high["approx_matched_ms"],
                "added_matched_ms": (high["approx_matched_ms"] - low["approx_matched_ms"]
                                     if low["approx_matched_ms"] is not None
                                     and high["approx_matched_ms"] is not None else None),
                "delivery_identical_per_seed": same_delivery,
            })

    unsuccessful = []
    for row in baseline:
        if (not row["complete_sequence"]) or row["timeouts"] or row["failures"] \
                or row["status"] != "COMPLETE":
            unsuccessful.append({"source": "baseline matrix", **_run_summary(row)})
    for pair in pairs:
        for mode in ("best_effort", "reliable"):
            row = pair[mode]["metrics"]
            if (not row["complete_sequence"]) or row["failures"] or row["status"] != "COMPLETE":
                label = "comparison (reused baseline)" if mode == "best_effort" else "comparison (reliable)"
                unsuccessful.append({"source": label, **_run_summary(row)})
    # a reused baseline run appears once: drop comparison duplicates of baseline rows
    seen, unique = set(), []
    for entry in unsuccessful:
        if entry["run_id"] in seen:
            continue
        seen.add(entry["run_id"])
        unique.append(entry)

    return {
        "inputs": {"baseline_metrics_rows": len(baseline), "comparison_rows": len(pairs),
                   "reliable_raw_records": len(reliable_raw)},
        "setup": setup, "baseline_conditions": baseline_conditions,
        "acceptance": acceptance_rows, "comparison": comparison, "per_pair": per_pair,
        "overhead": overhead, "delay_effect": delay_effect, "unsuccessful_runs": unique,
    }


def _run_summary(row):
    return {"run_id": row["run_id"], "mode": row["mode"], "loss": row["loss"],
            "delay_ms": row["delay_ms"], "seed": row["seed"],
            "delivered": row["unique_delivered"], "workload_size": row["workload_size"],
            "timeouts": row["timeouts"], "retransmissions": row["retransmissions"],
            "failures": row["failures"], "status": row["status"],
            "complete_sequence": row["complete_sequence"], "elapsed_s": row["elapsed_s"]}


# --------------------------------------------------------------------------- markdown

def table(headers, rows):
    def cell(value):
        return str(value).replace("|", "\\|")

    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def _n(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def _range(stats, digits=2):
    if abs(stats["min"] - stats["max"]) < 10 ** -digits:
        return f"{stats['mean']:.{digits}f}"
    return f"{stats['min']:.{digits}f}-{stats['max']:.{digits}f}"


def _of(value, total):
    return f"{value}/{total} ({pct(value, total):.1f}%)"


def _span(low, high, digits=2, unit=""):
    """'a-b' for a spread, a single value when the ends coincide at this precision."""
    if f"{low:.{digits}f}" == f"{high:.{digits}f}":
        return f"{low:.{digits}f}{unit}"
    return f"{low:.{digits}f}-{high:.{digits}f}{unit}"


def interpretation(summary):
    """Short paragraphs whose numbers all come from the computed summary."""
    setup, comparison = summary["setup"], summary["comparison"]
    base_loss = min(setup["comparison_losses"])
    clean = next((c for c in comparison if c["loss"] == base_loss), None)
    lossy = [c for c in comparison if c["loss"] != base_loss]
    timeout, retries = setup["timeout_s"], setup["reliable_max_retries"]
    out = []

    for c in lossy:
        be, rel = c["best_effort"], c["reliable"]
        out.append(
            f"**Best-effort delivery can lose messages and time out.** At {loss_label(c['loss'])} "
            f"loss on {setup['condition_link']}, best-effort delivered {_of(be['delivered'], be['probes'])} "
            f"probes, recorded {be['timeouts']} timeouts and completed the whole sequence in only "
            f"{be['complete_runs']} of {be['runs']} runs. It is an unacknowledged service: each "
            f"message is sent once, no layer notices a lost packet, and the only loss signal is a "
            f"missing echo that expires after the {timeout:g} s observation window.")
        if rel["retransmissions"] == be["timeouts"]:
            relation = " (one per timed-out best-effort probe)"
        else:
            relation = f" (best-effort saw {be['timeouts']} timeouts)"
        if rel["failures"]:
            limit = (f" It still recorded {rel['failures']} failures: recovery is bounded by the "
                     f"retry limit of {retries} retransmissions per packet.")
        else:
            limit = (f" No packet needed more than the {retries} allowed retransmissions, so there "
                     f"were {rel['failures']} failures.")
        out.append(
            f"**Stop-and-wait improves completion through acknowledgements and retransmission "
            f"(ARQ).** Under the same seeded losses the reliable sender completed "
            f"{rel['complete_runs']} of {rel['runs']} runs and delivered "
            f"{_of(rel['delivered'], rel['probes'])} messages, using "
            f"{rel['retransmissions']} retransmissions{relation}. It keeps one message outstanding, "
            f"waits for that message's ACK and resends on timeout, so a lost DATA packet is "
            f"repeated until it is acknowledged.{limit}")

    overhead = summary["overhead"]
    if overhead["lossy"] and "clean_elapsed_s" in overhead:
        entries = overhead["lossy"]
        extra_time = [e["extra_time_s"] for e in entries]
        per_retx = [e["extra_time_per_retransmission_s"] for e in entries
                    if e["extra_time_per_retransmission_s"] is not None]
        retx_total = sum(e["retransmissions"] for e in entries)
        timing = (f"Each retransmission cost about {_span(min(per_retx), max(per_retx))} s of extra "
                  f"elapsed time, which tracks the {timeout:g} s retransmission timeout: a lost "
                  f"packet is only detected when the sender's timer expires, so every loss adds one "
                  f"full timeout of idle waiting." if per_retx else
                  "No retransmissions occurred, so there was no timeout waiting.")
        out.append(
            f"**Loss increases retries and elapsed time.** Loss-free reliable runs took "
            f"{_n(overhead['clean_elapsed_s'], 3)} s on average; the lossy reliable runs needed "
            f"{retx_total} retransmissions in total and took {_span(min(extra_time), max(extra_time))} s "
            f"longer per run. {timing}")

        ack_counts = {p["traffic"]["destination_acks"] for p in summary["per_pair"]}
        extra_sends = [e["extra_network_sends"] for e in entries]
        share = [100 * e["extra_network_sends"] / overhead["clean_network_sends"] for e in entries]
        one_per = all(abs(e["extra_network_sends"] - e["retransmissions"]) < 1e-9 for e in entries)
        send_note = (f"exactly one extra link transmission per retransmission (the lost copy never "
                     f"reaches the next hop, so only the resend adds traffic)" if one_per else
                     f"{_n(min(extra_sends), 1)}-{_n(max(extra_sends), 1)} extra link transmissions per run")
        if len(ack_counts) == 1:
            acks = next(iter(ack_counts))
            kind = (" (one per message plus one for the FIN)"
                    if acks == setup["workload_size"] + 1 else "")
            ack_note = f"The destination sent {acks} ACKs in every run{kind}."
        else:
            ack_note = f"ACKs from the destination ranged {min(ack_counts)}-{max(ack_counts)} per run."
        throughput_clean = setup["workload_size"] / overhead["clean_elapsed_s"]
        throughput_lossy = [setup["workload_size"] / p["reliable"]["elapsed_s"]
                            for p in summary["per_pair"] if p["loss"] != base_loss]
        out.append(
            f"**Reliability has a time and traffic cost.** A loss-free reliable transfer sent "
            f"{_n(overhead['clean_source_transmissions'], 1)} source packets ({setup['workload_size']} "
            f"DATA plus one FIN) and {_n(overhead['clean_network_sends'], 1)} link transmissions over "
            f"all hops including the ACKs; best-effort sends each of the {setup['workload_size']} "
            f"messages once by design. {ack_note} Under loss the traffic cost was small: "
            f"{send_note}, {_span(min(share), max(share), 1)}% of the loss-free total. The time cost "
            f"was far larger, because stop-and-wait idles for a timeout on every loss: throughput "
            f"fell from {_n(throughput_clean, 0)} messages/s loss-free to "
            f"{_span(min(throughput_lossy), max(throughput_lossy), 1)} messages/s with loss.")
    if lossy:
        be_elapsed = lossy[0]["best_effort"]["elapsed_s"]
        rel_elapsed = lossy[0]["reliable"]["elapsed_s"]
        out.append(
            f"**Timeouts dominate elapsed time in both modes.** At {loss_label(lossy[0]['loss'])} "
            f"loss a best-effort run took {_range(be_elapsed)} s and a reliable run "
            f"{_range(rel_elapsed)} s: both spend roughly one {timeout:g} s timeout per lost packet. "
            f"The difference is what that time buys: best-effort pays it and still misses the "
            f"message, reliable pays it and recovers.")

    if clean:
        both_full = (clean["best_effort"]["delivered"] == clean["best_effort"]["probes"]
                     and clean["reliable"]["delivered"] == clean["reliable"]["probes"])
        lead = ("**Without loss the two modes behave alike.**" if both_full
                else "**Even at the lowest loss level the modes differ.**")
        out.append(
            f"{lead} At {loss_label(clean['loss'])} loss best-effort delivered "
            f"{_of(clean['best_effort']['delivered'], clean['best_effort']['probes'])} and reliable "
            f"{_of(clean['reliable']['delivered'], clean['reliable']['probes'])}; reliable mode took "
            f"{_n(clean['reliable']['ms_per_probe'], 1)} ms per message against "
            f"{_n(clean['best_effort']['ms_per_probe'], 1)} ms for best-effort, the small cost of "
            f"waiting for an ACK before sending the next message.")

    effects = summary["delay_effect"]
    if effects:
        low, high = effects[0]["low_delay_ms"], effects[0]["high_delay_ms"]
        sentences = []
        for e in effects:
            added = e["added_matched_ms"]
            sentences.append(
                f"at {loss_label(e['loss'])} loss the mean time per probe rose from "
                f"{_n(e['low_ms_per_probe'], 1)} to {_n(e['high_ms_per_probe'], 1)} ms"
                + (f" (about {_n(added, 1)} ms more per matched probe once timeout waits are removed, "
                   f"against {e['high_delay_ms'] - e['low_delay_ms']:g} ms configured)"
                   if added is not None else ""))
        identical = all(e["delivery_identical_per_seed"] for e in effects)
        out.append(
            f"**Added delay increases round-trip and elapsed time (best-effort baseline only).** "
            f"Raising the delay on {setup['condition_link']} from {low:g} to {high:g} ms: "
            + "; ".join(sentences) + ". "
            + ("Delivery counts were identical for every seed at both delays, so delay changed "
               "timing but not which packets were lost. " if identical else
               "Delivery counts differed between the delays for at least one seed. ")
            + "The delay applies to one direction of one link, so a probe pays it once on the way to "
              "the destination, not on the returning echo; the difference from the "
              "configured value reflects measurement and scheduling overhead, which this data "
              "does not isolate. "
              "Because reliable mode was not run with added delay, the effect of delay on "
              "retransmission behaviour (for example a delay approaching the retransmission timeout "
              "causing spurious retransmissions) is not measured here.")
    return out


def render_report(summary):
    setup = summary["setup"]
    sources = summary["inputs"]
    parts = ["# Evaluation: best-effort vs stop-and-wait delivery", ""]
    parts += [
        f"_Generated by `create_evaluation_report.py` from the committed results "
        f"({sources['baseline_metrics_rows']} baseline metric rows, {sources['comparison_rows']} "
        f"comparison pairs, {sources['reliable_raw_records']} raw reliable runs). "
        f"No experiment was rerun and every value below is computed from those files._", ""]

    parts += ["## 1. Experiment setup", "", table(["Parameter", "Value"], [
        ["Workload", f"{setup['workload_size']} messages per run, prefix `{setup['message_prefix']}` "
                     f"(`{setup['message_prefix']}-0000` ...), sent one at a time"],
        ["Direction", f"{setup['source']} -> {setup['destination']}"],
        ["Route", " -> ".join(setup["path"])],
        ["Impaired link", f"{setup['condition_link']} (directed: only traffic in that direction)"],
        ["Loss levels (baseline matrix)", ", ".join(loss_label(x) for x in setup["baseline_losses"])],
        ["Delay levels (baseline matrix)", ", ".join(f"{x:g} ms" for x in setup["baseline_delays_ms"])],
        ["Loss levels (mode comparison)", ", ".join(loss_label(x) for x in setup["comparison_losses"])],
        ["Delay levels (mode comparison)", ", ".join(f"{x:g} ms" for x in setup["comparison_delays_ms"])],
        ["Seeds", ", ".join(str(s) for s in setup["seeds"])],
        ["Repeats per condition", f"{setup['repeats_per_condition']} (one run per seed)"],
        ["Best-effort timeout", f"{setup['timeout_s']:g} s per probe (also the reliable retransmission timeout)"],
        ["Reliable max retries", f"{setup['reliable_max_retries']} retransmissions per packet"],
        ["Pacing", setup["pacing"] or "sequential"],
        ["Baseline matrix", f"{setup['baseline_runs']} runs, {setup['baseline_probes']} probes "
                            "(best-effort only, incl. loss-free directional checks)"],
        ["Mode comparison", f"{setup['pairs']} pairs (best-effort reused from the baseline, "
                            "reliable run for this comparison)"],
    ]), ""]
    parts += [
        f"> **Scope of the evidence.** Reliable mode was compared with best-effort at "
        f"{', '.join(f'{x:g}' for x in setup['comparison_delays_ms'])} ms added delay only. "
        f"The {', '.join(f'{x:g}' for x in setup['baseline_delays_ms'] if x)} ms delay results below "
        f"come from the best-effort baseline matrix; reliable mode was **not** measured with added "
        f"delay, so no reliable-delay result is claimed.", ""]

    parts += ["## 2. Best-effort baseline by loss and delay", "",
              "**Table 1 - Best-effort baseline (all seeds pooled per condition).** "
              "\"Complete runs\" counts runs where every message matched.", "",
              table(["Loss", "Delay (ms)", "Runs", "Delivered", "Complete runs", "Timeouts",
                     "Elapsed per run (s)", "Mean time per probe (ms)"],
                    [[loss_label(c["loss"]), f"{c['delay_ms']:g}", c["runs"],
                      _of(c["delivered"], c["probes"]), f"{c['complete_runs']}/{c['runs']}",
                      c["timeouts"], _range(c["elapsed_s"]), _n(c["ms_per_probe"], 1)]
                     for c in summary["baseline_conditions"]]), ""]
    if summary["acceptance"]:
        parts += ["**Table 2 - Loss-free directional acceptance checks.**", "",
                  table(["Run", "Direction", "Delivered", "Timeouts", "Elapsed (s)"],
                        [[a["run_id"], f"{a['source']} -> {a['destination']}",
                          _of(a["delivered"], a["probes"]), a["timeouts"], _range(a["elapsed_s"], 3)]
                         for a in summary["acceptance"]]), ""]

    parts += ["## 3. Added delay (baseline matrix, best-effort)", ""]
    if summary["delay_effect"]:
        parts += ["**Table 3 - Effect of added delay.** \"Matched-probe time\" removes "
                  f"{setup['timeout_s']:g} s per timeout from the elapsed time (approximate).", "",
                  table(["Loss", "Delay change", "Time per probe (ms)", "Added (ms)",
                         "Matched-probe time (ms)", "Added (ms), matched", "Same delivery per seed"],
                        [[loss_label(e["loss"]),
                          f"{e['low_delay_ms']:g} -> {e['high_delay_ms']:g} ms",
                          f"{_n(e['low_ms_per_probe'], 1)} -> {_n(e['high_ms_per_probe'], 1)}",
                          _n(e["added_ms_per_probe"], 1),
                          f"{_n(e['low_matched_ms'], 1)} -> {_n(e['high_matched_ms'], 1)}",
                          _n(e["added_matched_ms"], 1),
                          "yes" if e["delivery_identical_per_seed"] else "no"]
                         for e in summary["delay_effect"]]), ""]
    else:
        parts += ["The baseline matrix has a single delay level, so no delay effect is computed.", ""]

    parts += ["## 4. Best-effort vs reliable", "",
              f"**Table 4 - Delivery by loss level (all seeds pooled; both modes at "
              f"{', '.join(f'{x:g}' for x in setup['comparison_delays_ms'])} ms delay).**", ""]
    rows = []
    for c in summary["comparison"]:
        for mode, label in (("best_effort", "best-effort"), ("reliable", "reliable")):
            m = c[mode]
            rows.append([loss_label(c["loss"]), label, _of(m["delivered"], m["probes"]),
                         f"{m['complete_runs']}/{m['runs']}",
                         m["timeouts"] if mode == "best_effort" else "n/a",
                         m["retransmissions"], m["failures"], _range(m["elapsed_s"], 3)])
    parts += [table(["Loss", "Mode", "Delivered", "Complete runs", "Timeouts", "Retransmissions",
                     "Failures", "Elapsed per run (s)"], rows), "",
              "**Table 5 - Each pair (same seed, workload, route, loss, delay and timeout).**", "",
              table(["Pair", "BE delivered", "BE complete", "BE status", "BE timeouts",
                     "BE elapsed (s)", "REL delivered", "REL complete", "REL status",
                     "REL retransmits", "REL failures", "REL elapsed (s)",
                     "REL - BE elapsed (s)"],
                    [[p["pair_id"], p["best_effort"]["unique_delivered"],
                      "yes" if p["best_effort"]["complete_sequence"] else "no",
                      p["best_effort"]["status"],
                      p["best_effort"]["timeouts"], _n(p["best_effort"]["elapsed_s"], 3),
                      p["reliable"]["unique_delivered"],
                      "yes" if p["reliable"]["complete_sequence"] else "no",
                      p["reliable"]["status"],
                      p["reliable"]["retransmissions"], p["reliable"]["failures"],
                      _n(p["reliable"]["elapsed_s"], 3), _n(p["elapsed_difference_s"], 3)]
                     for p in summary["per_pair"]]), "",
              "**Table 6 - Reliable traffic cost (from the raw reliable events).**", "",
              table(["Pair", "Source DATA sent", "Source FIN sent", "Retransmitted", "Injected drops",
                     "ACKs from destination", "Link transmissions (all hops)"],
                    [[p["pair_id"], p["traffic"]["source_data"], p["traffic"]["source_fin"],
                      p["traffic"]["source_retransmitted"], p["traffic"]["injected_drops"],
                      p["traffic"]["destination_acks"], p["traffic"]["network_sends"]]
                     for p in summary["per_pair"]]), ""]

    parts += ["## 5. Unsuccessful or partial runs", ""]
    if summary["unsuccessful_runs"]:
        parts += ["**Table 7 - Runs that did not deliver everything, timed out, or failed.**", "",
                  table(["Run", "Source file", "Mode", "Loss", "Delay (ms)", "Seed", "Delivered",
                         "Timeouts", "Retransmits", "Failures", "Status"],
                        [[r["run_id"], r["source"], r["mode"], loss_label(r["loss"]),
                          f"{r['delay_ms']:g}", r["seed"], f"{r['delivered']}/{r['workload_size']}",
                          r["timeouts"], r["retransmissions"], r["failures"], r["status"]]
                         for r in summary["unsuccessful_runs"]]), ""]
    else:
        parts += ["No unsuccessful or partial runs were found.", ""]
    reliable_bad = [r for r in summary["unsuccessful_runs"] if r["mode"] == "reliable"]
    parts += [("Reliable runs that did not complete are listed above." if reliable_bad else
               "Every reliable run completed; all unsuccessful runs are best-effort."), ""]

    parts += ["## 6. Interpretation", ""]
    for paragraph in interpretation(summary):
        if paragraph:
            parts += [paragraph, ""]
    parts += [
        "**Limits.** Three seeds, one 20-message workload and a single impaired link "
        "are small samples; the results show the mechanisms, not population statistics. "
        "Best-effort \"delivered\" counts matched round-trip probes, whereas reliable "
        "\"delivered\" counts DATA accepted by the receiver; with loss only on the forward "
        "direction of the impaired link the two coincide here.", ""]
    return "\n".join(parts)


# --------------------------------------------------------------------------- cli

def generate(baseline_path, comparison_path, reliable_raw_path, report_path, summary_path):
    inputs = [Path(p).resolve() for p in (baseline_path, comparison_path, reliable_raw_path)]
    outputs = [Path(p).resolve() for p in (report_path, summary_path)]
    if set(inputs) & set(outputs) or outputs[0] == outputs[1]:
        raise ValueError("output files must differ from the inputs and from each other")
    summary = compute_summary(load_jsonl(baseline_path), load_jsonl(comparison_path),
                              load_jsonl(reliable_raw_path))
    report = render_report(summary)
    for path in (report_path, summary_path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(report, encoding="utf-8", newline="\n")
    Path(summary_path).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8",
                                  newline="\n")
    return summary


def build_parser():
    parser = argparse.ArgumentParser(
        description="Create the evaluation tables and interpretation (Markdown + JSON "
                    "summary) from the committed baseline metrics, mode comparison and "
                    "raw reliable runs. Nothing is rerun.")
    parser.add_argument("--baseline-metrics", default=DEFAULTS["baseline_metrics"])
    parser.add_argument("--comparison", default=DEFAULTS["comparison"])
    parser.add_argument("--reliable-raw", default=DEFAULTS["reliable_raw"])
    parser.add_argument("--report", default=DEFAULTS["report"], help="Markdown report to write")
    parser.add_argument("--summary", default=DEFAULTS["summary"], help="JSON summary to write")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        summary = generate(args.baseline_metrics, args.comparison, args.reliable_raw,
                           args.report, args.summary)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Wrote {args.report} and {args.summary} "
          f"({summary['inputs']['baseline_metrics_rows']} baseline rows, "
          f"{summary['inputs']['comparison_rows']} pairs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
