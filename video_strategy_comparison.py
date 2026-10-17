from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parent
REPRESENTATIONS_DIR = ROOT / "reps"
DEFAULT_OUTPUT = Path(os.getenv("TEMP", ".")) / "optimized-tcp-video-comparison"


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("127.0.0.1", 0))
        return udp_socket.getsockname()[1]


def _metric(output: str, name: str) -> Optional[float]:
    match = re.search(
        rf"^\[METRIC\] {re.escape(name)}=([\d.]+)$",
        output,
        re.MULTILINE,
    )
    return float(match.group(1)) if match else None


def _run_trial(
    strategy: str,
    trial: int,
    loss: float,
    delay: float,
    jitter: float,
    bandwidth: float,
    seed: int,
    output_dir: Path,
    timeout: float,
) -> Dict[str, Any]:
    port = _free_udp_port()
    output_path = output_dir / f"{strategy.lower()}_trial{trial}.mp4"
    server_command = [
        sys.executable,
        "-u",
        str(ROOT / "variant_file_server.py"),
        "--dir",
        str(REPRESENTATIONS_DIR),
        "--port",
        str(port),
        "--variant",
        "newreno",
        "--loss",
        str(loss),
        "--delay",
        str(delay),
        "--jitter",
        str(jitter),
        "--bandwidth",
        str(bandwidth),
        "--seed",
        str(seed),
    ]
    server = subprocess.Popen(
        server_command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    server_lines: List[str] = []
    server_ready = threading.Event()
    server_session_finished = threading.Event()

    def read_server_output() -> None:
        if server.stdout is None:
            return
        for line in server.stdout:
            server_lines.append(line)
            if "[+] Listening on" in line:
                server_ready.set()
            if "[+] Session finished" in line:
                server_session_finished.set()

    reader = threading.Thread(target=read_server_output, daemon=True)
    reader.start()
    try:
        if not server_ready.wait(timeout=10):
            raise RuntimeError(
                f"{strategy} server failed to start:\n{''.join(server_lines)}"
            )

        client_command = [
            sys.executable,
            "-u",
            str(ROOT / "variant_file_client.py"),
            "--port",
            str(port),
            "--output",
            str(output_path),
            "--seed",
            str(seed + 1),
        ]
        if strategy == "Measure-first adaptive":
            client_command.append("--measure-first")
        else:
            client_command.extend(["--index", "2"])

        client = subprocess.run(
            client_command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if client.returncode != 0:
            raise RuntimeError(
                f"{strategy} client failed with exit code {client.returncode}:\n"
                f"{client.stdout}\n{client.stderr}\n"
                f"Server output:\n{''.join(server_lines)}"
            )
        if not server_session_finished.wait(timeout=10):
            raise RuntimeError(
                f"{strategy} server did not confirm the transfer:\n"
                f"{''.join(server_lines[-30:])}"
            )

        client_output = client.stdout
        server_output = "".join(server_lines)
        media_bytes = output_path.stat().st_size
        total_time = _metric(client_output, "total_time_s")
        if total_time is None:
            raise RuntimeError(f"{strategy} output omitted total transfer time.")
        chosen_bitrate_match = re.search(
            r"^\[CHOSEN_BITRATE\]\s+(\d+)",
            client_output,
            re.MULTILINE,
        )
        if not chosen_bitrate_match:
            raise RuntimeError(f"{strategy} output omitted its selected bitrate.")
        probe_bytes = _metric(client_output, "probe_bytes") or 0
        probe_time = _metric(client_output, "probe_time_s") or 0
        measured_kbps = _metric(client_output, "measured_probe_goodput_kbps")
        media_time = _metric(client_output, "media_transfer_time_s")
        trace_events = re.findall(r"event=([A-Z_]+)", server_output)
        drop_count = _metric(server_output, "data_drops")
        ack_drops = _metric(client_output, "ack_drops")
        return {
            "strategy": strategy,
            "trial": trial,
            "seed": seed,
            "selected_bitrate_kbps": int(chosen_bitrate_match.group(1)),
            "measured_probe_goodput_kbps": measured_kbps,
            "probe_bytes": int(probe_bytes),
            "probe_time_s": probe_time,
            "media_bytes": media_bytes,
            "total_bytes_sent": media_bytes + int(probe_bytes),
            "total_time_s": total_time,
            "media_transfer_time_s": media_time,
            "end_to_end_media_rate_mbps": media_bytes * 8 / total_time / 1_000_000,
            "media_transfer_goodput_mbps": (
                media_bytes * 8 / media_time / 1_000_000 if media_time else None
            ),
            "retransmissions": trace_events.count("RETRANSMIT"),
            "fast_retransmits": trace_events.count("FAST_RETRANSMIT"),
            "partial_acks": trace_events.count("PARTIAL_ACK"),
            "timeouts": trace_events.count("TIMEOUT"),
            "data_drops": int(drop_count or 0),
            "ack_drops": int(ack_drops or 0),
            "output": str(output_path),
        }
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=5)
        reader.join(timeout=2)
        if server.stdout is not None:
            server.stdout.close()


def _summarize(trials: List[Dict[str, Any]], strategy: str) -> Dict[str, Any]:
    selected = [row for row in trials if row["strategy"] == strategy]
    if not selected:
        raise ValueError(f"No trial results for {strategy}.")

    keys = (
        "selected_bitrate_kbps",
        "measured_probe_goodput_kbps",
        "probe_bytes",
        "probe_time_s",
        "media_bytes",
        "total_bytes_sent",
        "total_time_s",
        "media_transfer_time_s",
        "end_to_end_media_rate_mbps",
        "media_transfer_goodput_mbps",
        "retransmissions",
        "fast_retransmits",
        "partial_acks",
        "timeouts",
        "data_drops",
        "ack_drops",
    )
    result = {"strategy": strategy, "trials": len(selected)}
    for key in keys:
        values = [row[key] for row in selected if row[key] is not None]
        result[key] = statistics.mean(values) if values else None
    result["selected_bitrates_kbps"] = [
        row["selected_bitrate_kbps"] for row in selected
    ]
    result["last_output"] = selected[-1]["output"]
    return result


def run_comparison(
    loss: float,
    trials_per_strategy: int,
    delay: float,
    jitter: float,
    bandwidth: float,
    seed: int,
    output_dir: Path,
    timeout: float = 240,
) -> Dict[str, Any]:
    if not REPRESENTATIONS_DIR.is_dir():
        raise FileNotFoundError(f"Representations directory is missing: {REPRESENTATIONS_DIR}")
    for bitrate in (400, 800, 1500):
        if not (REPRESENTATIONS_DIR / f"{bitrate}k.mp4").is_file():
            raise FileNotFoundError(
                f"Missing {bitrate} kbps representation in {REPRESENTATIONS_DIR}"
            )
    if not 0 <= loss <= 1:
        raise ValueError("Loss must be between 0 and 1.")
    if trials_per_strategy < 1:
        raise ValueError("Trials per strategy must be at least 1.")
    if delay < 0 or jitter < 0 or bandwidth <= 0:
        raise ValueError("Delay and jitter must be non-negative; bandwidth must be positive.")

    output_dir.mkdir(parents=True, exist_ok=True)
    trial_rows = []
    latest_outputs: Dict[str, Path] = {}
    run_count = 2 * trials_per_strategy
    completed = 0
    for trial in range(1, trials_per_strategy + 1):
        trial_seed = seed + trial
        for strategy in ("Traditional fixed quality", "Measure-first adaptive"):
            row = _run_trial(
                strategy,
                trial,
                loss,
                delay,
                jitter,
                bandwidth,
                trial_seed,
                output_dir,
                timeout,
            )
            trial_rows.append(row)
            previous_output = latest_outputs.get(strategy)
            if previous_output and previous_output != Path(row["output"]):
                previous_output.unlink(missing_ok=True)
            latest_outputs[strategy] = Path(row["output"])
            completed += 1
            print(
                f"Completed {completed}/{run_count}: {strategy}, trial={trial}, "
                f"quality={row['selected_bitrate_kbps']} kbps, "
                f"time={row['total_time_s']:.2f}s, "
                f"end-to-end video rate={row['end_to_end_media_rate_mbps']:.2f}Mbps",
                flush=True,
            )
            print("TRIAL_JSON " + json.dumps(row), flush=True)

    summary = {
        "loss_percent": loss * 100,
        "delay_ms": delay * 1000,
        "jitter_ms": jitter * 1000,
        "bandwidth_mbps": bandwidth,
        "traditional": _summarize(trial_rows, "Traditional fixed quality"),
        "adaptive": _summarize(trial_rows, "Measure-first adaptive"),
    }
    baseline_time = summary["traditional"]["total_time_s"]
    adaptive_time = summary["adaptive"]["total_time_s"]
    summary["transfer_time_change_percent"] = (
        (adaptive_time - baseline_time) / baseline_time * 100
        if baseline_time
        else None
    )
    print("SUMMARY_JSON " + json.dumps(summary), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare fixed-quality and measure-first adaptive video delivery."
    )
    parser.add_argument("--loss", type=float, default=0.002)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.0005)
    parser.add_argument("--jitter", type=float, default=0.0001)
    parser.add_argument("--bandwidth", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=9400)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()

    run_comparison(
        loss=args.loss,
        trials_per_strategy=args.trials,
        delay=args.delay,
        jitter=args.jitter,
        bandwidth=args.bandwidth,
        seed=args.seed,
        output_dir=args.out_dir,
        timeout=args.timeout,
    )


if __name__ == "__main__":
    main()
