import subprocess, sys, time, os, argparse, matplotlib.pyplot as plt
from pathlib import Path
from typing import Dict, List
from threading import Thread

VARIANTS = ["tahoe", "reno", "newreno"]
FILE_PATH = "reps/1500k.mp4"
PORT_BASE = 7100


def run_variant(variant: str, port: int, loss: float, out_dir: str) -> Dict[str, float]:
    """Run a single transfer, return metrics dict."""
    server_cmd = [
        sys.executable,
        "tahoe_server.py",
        "--file", FILE_PATH,
        "--port", str(port),
        "--variant", variant,
        "--loss", str(loss),
        "--delay", "0.02",      # 20 ms one-way base delay
        "--jitter", "0.005",    # 5 ms jitter
        "--bandwidth", "10",    # 10 Mbps link cap
    ]
    # Launch server and continuously read its stdout to avoid pipe blocking
    server = subprocess.Popen(server_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    # Background thread to drain stdout
    srv_lines: List[str] = []

    def _reader(proc, collector):
        for ln in proc.stdout:
            collector.append(ln)

    reader_t = Thread(target=_reader, args=(server, srv_lines), daemon=True)
    reader_t.start()

    time.sleep(1.0)

    out_file = Path(out_dir) / f"{variant}_loss{loss:.4f}.mp4"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Run client and stream its output so the user sees progress live
    start = time.time()
    client = subprocess.Popen([
        sys.executable, "-u", "tahoe_client.py",
        "--port", str(port),
        "--output", str(out_file),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    chosen_bitrate = None
    captured = []
    while True:
        chunk = client.stdout.read(1)
        if not chunk:
            break
        sys.stdout.write(chunk)
        sys.stdout.flush()
        captured.append(chunk)
        # Check for bitrate line in the assembled buffer when newline encountered
        if chunk == "\n":
            line = "".join(captured).split("\n")[-2] if "\n" in "".join(captured) else ""
            if line.startswith("[CHOSEN_BITRATE]"):
                try:
                    chosen_bitrate = int(line.split()[1])
                except ValueError:
                    pass

    client.wait()
    elapsed = time.time() - start

    # Terminate server and wait
    server.terminate()
    server.wait(timeout=5.0)

    # Throughput
    size_mb = out_file.stat().st_size / 1_000_000
    throughput_mbps = size_mb * 8 / elapsed if elapsed > 0 else 0.0

    # Retransmissions = number of timeout lines in server log
    retrans = sum(1 for line in srv_lines if "Timeout for seq" in line)

    return {
        "time": elapsed,
        "throughput": throughput_mbps,
        "retrans": retrans,
        "bitrate": chosen_bitrate or 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Compare TCP variants at a given loss rate")
    parser.add_argument("--loss", type=float, default=0.0002, help="Packet loss rate (e.g. 0.0002 = 0.02 %% )")
    parser.add_argument("--out-dir", default="variant_compare_outputs")
    args = parser.parse_args()

    if not os.path.isfile(FILE_PATH):
        print("File", FILE_PATH, "not found. Make sure reps/1500k.mp4 exists.")
        sys.exit(1)

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    metrics: Dict[str, Dict[str, float]] = {}
    port = PORT_BASE
    for v in VARIANTS:
        print(f"\n=== {v.upper()} (loss={args.loss*100:.3f} %) ===")
        m = run_variant(v, port, args.loss, args.out_dir)
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