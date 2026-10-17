from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

import matplotlib.pyplot as plt

from compare_tcp_variants import VARIANTS, run_variant


ROOT = Path(__file__).resolve().parent
DEFAULT_LOSSES = [0.0, 0.002, 0.004, 0.007]
DEFAULT_FILE = ROOT / "reps" / "400k.mp4"
DEFAULT_OUTPUT = ROOT / "variant_compare_outputs" / "loss_stress"
TRACE_FIELDS = ("loss_percent", "trial", "algorithm", "time_s", "event", "seq", "cwnd", "ssthresh")


def _write_csv(path: Path, fields: List[str], rows: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _trace_row(line: str, loss: float, trial: int, variant: str) -> Dict[str, Any]:
    values = dict(re.findall(r"(\w+)=([^\s]+)", line))
    return {
        "loss_percent": loss * 100,
        "trial": trial,
        "algorithm": variant.upper(),
        "time_s": float(values["elapsed"]),
        "event": values["event"],
        "seq": values.get("seq", ""),
        "cwnd": values.get("cwnd", ""),
        "ssthresh": values.get("ssthresh", ""),
    }


def _aggregate(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[tuple[str, float], List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["algorithm"], row["loss_percent"])].append(row)

    summaries = []
    for (algorithm, loss_percent), trials in sorted(
        groups.items(), key=lambda item: (item[0][1], VARIANTS.index(item[0][0].lower()))
    ):
        summaries.append(
            {
                "Algorithm": algorithm,
                "Loss %": loss_percent,
                "Transfer time (s)": mean(row["transfer_time_s"] for row in trials),
                "Throughput (Mbps)": mean(row["throughput_mbps"] for row in trials),
                "Retransmissions": mean(row["retransmissions"] for row in trials),
                "Data drops": mean(row["data_drops"] for row in trials),
                "ACK drops": mean(row["ack_drops"] for row in trials),
                "Trials": len(trials),
            }
        )
    return summaries


def _save_charts(output_dir: Path, summaries: List[Dict[str, Any]]) -> Dict[str, Path]:
    chart_specs = (
        ("Throughput (Mbps)", "Throughput vs Packet Loss", "throughput_vs_loss.png"),
        ("Transfer time (s)", "Transfer Time vs Packet Loss", "transfer_time_vs_loss.png"),
        ("Retransmissions", "Retransmissions vs Packet Loss", "retransmissions_vs_loss.png"),
    )
    paths = {}
    for metric, title, filename in chart_specs:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for algorithm in VARIANTS:
            points = [
                row for row in summaries
                if row["Algorithm"].lower() == algorithm
            ]
            points.sort(key=lambda row: row["Loss %"])
            ax.plot(
                [row["Loss %"] for row in points],
                [row[metric] for row in points],
                marker="o",
                linewidth=2,
                label=algorithm.upper(),
            )
        ax.set_xlabel("Configured packet loss (%)")
        ax.set_ylabel(metric)
        ax.set_title(title)
        ax.grid(True, alpha=0.25)
        ax.legend()
        fig.tight_layout()
        path = output_dir / filename
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths[metric] = path
    return paths


def run_experiment(
    losses: List[float],
    variants: List[str],
    trials_per_condition: int,
    file_path: Path,
    output_dir: Path,
    delay: float = 0.002,
    jitter: float = 0.001,
    base_seed: int = 7300,
    start_port: int = 7200,
) -> List[Dict[str, Any]]:
    if not file_path.is_file():
        raise FileNotFoundError(f"Input video does not exist: {file_path}")
    if not losses or any(loss < 0 or loss > 1 for loss in losses):
        raise ValueError("Provide one or more loss rates between 0 and 1.")
    if not variants or any(variant not in VARIANTS for variant in variants):
        raise ValueError(f"Choose one or more of: {', '.join(VARIANTS)}")
    if trials_per_condition < 1:
        raise ValueError("Trials per condition must be at least 1.")

    output_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []
    run_count = len(losses) * len(variants) * trials_per_condition
    port = start_port
    completed = 0

    for loss in losses:
        for variant in variants:
            for trial in range(1, trials_per_condition + 1):
                seed = base_seed + trial
                trial_traces = []
                result = run_variant(
                    variant=variant,
                    port=port,
                    loss=loss,
                    out_dir=str(output_dir),
                    file_path=str(file_path),
                    delay=delay,
                    jitter=jitter,
                    seed=seed,
                    trial=trial,
                    trace_callback=trial_traces.append,
                    keep_output=False,
                )
                row = {
                    "algorithm": variant.upper(),
                    "loss_percent": loss * 100,
                    "trial": trial,
                    "seed": seed,
                    "transfer_time_s": result["time"],
                    "throughput_mbps": result["throughput"],
                    "retransmissions": int(result["retrans"]),
                    "data_drops": int(result["data_drops"]),
                    "ack_drops": int(result["ack_drops"]),
                }
                rows.append(row)
                for line in trial_traces:
                    event = _trace_row(line, loss, trial, variant)
                    if event["event"] in {
                        "TIMEOUT",
                        "FAST_RETRANSMIT",
                        "PARTIAL_ACK",
                        "RETRANSMIT",
                    }:
                        traces.append(event)
                completed += 1
                print(
                    f"Completed {completed}/{run_count}: {variant.upper()}, "
                    f"loss={loss * 100:.2f}%, trial={trial}, "
                    f"time={result['time']:.2f}s, "
                    f"throughput={result['throughput']:.2f}Mbps, "
                    f"retransmissions={int(result['retrans'])}",
                    flush=True,
                )
                port += 1

    summaries = _aggregate(rows)
    raw_fields = [
        "algorithm", "loss_percent", "trial", "seed", "transfer_time_s",
        "throughput_mbps", "retransmissions", "data_drops", "ack_drops",
    ]
    summary_fields = [
        "Algorithm", "Loss %", "Transfer time (s)", "Throughput (Mbps)",
        "Retransmissions", "Data drops", "ACK drops", "Trials",
    ]
    _write_csv(output_dir / "loss_stress_trials.csv", raw_fields, rows)
    _write_csv(output_dir / "loss_stress_summary.csv", summary_fields, summaries)
    _write_csv(output_dir / "congestion_trace.csv", list(TRACE_FIELDS), traces)
    chart_paths = _save_charts(output_dir, summaries)

    best_throughput = max(summaries, key=lambda row: row["Throughput (Mbps)"])
    fastest = min(summaries, key=lambda row: row["Transfer time (s)"])
    most_retransmissions = max(summaries, key=lambda row: row["Retransmissions"])
    print(
        f"SUMMARY Best throughput: {best_throughput['Algorithm']} at "
        f"{best_throughput['Loss %']:.2f}% loss "
        f"({best_throughput['Throughput (Mbps)']:.2f} Mbps).",
        flush=True,
    )
    print(
        f"SUMMARY Lowest transfer time: {fastest['Algorithm']} at "
        f"{fastest['Loss %']:.2f}% loss ({fastest['Transfer time (s)']:.2f} s).",
        flush=True,
    )
    print(
        f"SUMMARY Highest mean retransmissions: {most_retransmissions['Algorithm']} at "
        f"{most_retransmissions['Loss %']:.2f}% loss "
        f"({most_retransmissions['Retransmissions']:.2f}).",
        flush=True,
    )
    print("CHARTS " + ", ".join(str(path) for path in chart_paths.values()), flush=True)
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare TCP-style variants across packet-loss conditions.")
    parser.add_argument("--losses", nargs="+", type=float, default=DEFAULT_LOSSES)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--trials", type=int, default=3, help="Repeated trials per variant/loss condition.")
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=0.002)
    parser.add_argument("--jitter", type=float, default=0.001)
    parser.add_argument(
        "--seed",
        type=int,
        default=7300,
        help="Base seed used to initialize the simulator for each trial.",
    )
    parser.add_argument("--start-port", type=int, default=7200)
    args = parser.parse_args()
    run_experiment(
        losses=args.losses,
        variants=args.variants,
        trials_per_condition=args.trials,
        file_path=args.file,
        output_dir=args.out_dir,
        delay=args.delay,
        jitter=args.jitter,
        base_seed=args.seed,
        start_port=args.start_port,
    )


if __name__ == "__main__":
    main()
