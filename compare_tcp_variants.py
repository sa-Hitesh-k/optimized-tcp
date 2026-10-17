import subprocess, sys, time, os, argparse, matplotlib.pyplot as plt
from pathlib import Path
from typing import Callable, Dict, List, Optional
from threading import Event, Thread

VARIANTS = ["tahoe", "reno", "newreno"]
FILE_PATH = "reps/1500k.mp4"
PORT_BASE = 7100


def run_variant(
    variant: str,
    port: int,
    loss: float,
    out_dir: str,
    file_path: str = FILE_PATH,
    delay: float = 0.02,
    jitter: float = 0.005,
    seed: Optional[int] = None,
    trial: Optional[int] = None,
    trace_callback: Optional[Callable[[str], None]] = None,
    keep_output: bool = True,
) -> Dict[str, float]:
    """Run a single transfer, return metrics dict."""
    server_cmd = [
        sys.executable,
        "-u",
        "tahoe_server.py",
        "--file", file_path,
        "--port", str(port),
        "--variant", variant,
        "--loss", str(loss),
        "--delay", str(delay),
        "--jitter", str(jitter),
        "--bandwidth", "10",    # 10 Mbps link cap
    ]
    if seed is not None:
        server_cmd.extend(["--seed", str(seed)])
    # Launch server and continuously read its stdout to avoid pipe blocking
    server = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    # Background thread to drain stdout
    srv_lines: List[str] = []
    ready = Event()

    def _reader(proc, collector):
        for ln in proc.stdout:
            collector.append(ln)
            if "[+] Listening on" in ln:
                ready.set()
            if trace_callback is not None and ln.startswith("[TRACE]"):
                trace_callback(ln.strip())

    reader_t = Thread(target=_reader, args=(server, srv_lines), daemon=True)
    reader_t.start()

    if not ready.wait(timeout=10):
        server.terminate()
        server.wait(timeout=5)
        raise RuntimeError(f"{variant} server did not start on UDP port {port}")

    trial_suffix = f"_trial{trial}" if trial is not None else ""
    out_file = Path(out_dir) / f"{variant}_loss{loss:.4f}{trial_suffix}.mp4"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Run client and stream its output so the user sees progress live
    client_cmd = [
        sys.executable, "-u", "tahoe_client.py",
        "--port", str(port),
        "--output", str(out_file),
    ]
    if seed is not None:
        client_cmd.extend(["--seed", str(seed + 1)])

    start = time.monotonic()
    try:
        client = subprocess.run(
            client_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
        elapsed = time.monotonic() - start
        if client.returncode == 0:
            try:
                server.wait(timeout=5.0)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{variant} server did not confirm transfer completion") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{variant} transfer timed out at {loss * 100:.2f}% loss") from exc
    finally:
        if server.poll() is None:
            server.terminate()
            server.wait(timeout=5.0)
        reader_t.join(timeout=1.0)

    if client.returncode != 0:
        raise RuntimeError(f"{variant} client failed:\n{client.stdout}")
    if not out_file.is_file():
        raise RuntimeError(f"{variant} transfer completed without creating {out_file}")
    size_mb = out_file.stat().st_size / 1_000_000
    throughput_mbps = size_mb * 8 / elapsed if elapsed > 0 else 0.0
    if not keep_output:
        out_file.unlink()
    retrans = sum(1 for line in srv_lines if "event=RETRANSMIT " in line)
    data_drops = _metric_value(srv_lines, "data_drops")
    ack_drops = _metric_value(client.stdout.splitlines(), "ack_drops")

    return {
        "time": elapsed,
        "throughput": throughput_mbps,
        "retrans": retrans,
        "loss": loss,
        "data_drops": data_drops,
        "ack_drops": ack_drops,
    }


def _metric_value(lines: List[str], name: str) -> float:
    prefix = f"[METRIC] {name}="
    for line in lines:
        if line.startswith(prefix):
            return float(line[len(prefix):])
    return 0.0


def main():
    parser = argparse.ArgumentParser(description="Compare TCP variants at a given loss rate")
    parser.add_argument("--loss", type=float, default=0.0002, help="Packet loss rate (e.g. 0.0002 = 0.02 %% )")
    parser.add_argument("--out-dir", default="variant_compare_outputs")
    parser.add_argument("--file", default=FILE_PATH, help="Video file to transfer")
    parser.add_argument("--delay", type=float, default=0.02, help="One-way delay in seconds")
    parser.add_argument("--jitter", type=float, default=0.005, help="Delay jitter in seconds")
    args = parser.parse_args()

    if not os.path.isfile(args.file):
        print("File", args.file, "not found.")
        sys.exit(1)

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    metrics: Dict[str, Dict[str, float]] = {}
    port = PORT_BASE
    for v in VARIANTS:
        print(f"\n=== {v.upper()} (loss={args.loss*100:.3f} %) ===")
        m = run_variant(v, port, args.loss, args.out_dir, args.file, args.delay, args.jitter)
        metrics[v] = m
        print(f"Time: {m['time']:.2f} s  Throughput: {m['throughput']:.2f} Mbps  Retrans: {m['retrans']}")
        port += 1

    # Plot throughput and time
    variants = VARIANTS
    throughputs = [metrics[v]["throughput"] for v in variants]
    times = [metrics[v]["time"] for v in variants]
    retrans = [metrics[v]["retrans"] for v in variants]

    x = range(len(variants))
    width = 0.25

    fig, ax1 = plt.subplots(figsize=(8, 4))
    ax2 = ax1.twinx()
    ax3 = ax1.twinx()
    ax3.spines.right.set_position(("axes", 1.15))  # third axis

    bars_tp = ax1.bar([i - width for i in x], throughputs, width, label="Throughput (Mbps)", color="#4c72b0")
    bars_tm = ax2.bar(x, times, width, label="Time (s)", color="#55a868")
    bars_rt = ax3.bar([i + width for i in x], retrans, width, label="Retrans", color="#c44e52")

    ax1.set_ylabel("Throughput (Mbps)", color="#4c72b0")
    ax2.set_ylabel("Time (s)", color="#55a868")
    ax3.set_ylabel("Retransmissions", color="#c44e52")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([v.upper() for v in variants])
    ax1.set_title(f"TCP variant comparison at loss {args.loss*100:.2f} %")

    for bar in bars_tp:
        ax1.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1, f"{bar.get_height():.1f}", ha="center", color="#4c72b0")
    for bar in bars_tm:
        ax2.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.05, f"{bar.get_height():.2f}", ha="center", color="#55a868")
    for bar in bars_rt:
        ax3.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1, f"{int(bar.get_height())}", ha="center", color="#c44e52")

    fig.tight_layout()
    plot_path = os.path.join(args.out_dir, "tcp_variants_metrics.png")
    plt.savefig(plot_path)
    print("Plot saved to", plot_path)


if __name__ == "__main__":
    main() 