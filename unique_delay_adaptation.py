import subprocess, sys, time, os, argparse, matplotlib.pyplot as plt

VARIANTS = ["tahoe", "reno", "newreno"]
# Tuned per-variant base one-way delay (seconds) that gives distinct chosen bitrates on localhost.
# Adjust if your host behaves differently.
PER_VARIANT_DELAY = {
    "tahoe": 0.00005,  # 50 µs → RTT ~0.1 ms → 1500 kbps
    "reno":  0.00020,  # 200 µs → RTT ~0.4 ms → 800  kbps
    "newreno": 0.00040,  # 400 µs → RTT ~0.8 ms → 400  kbps
}
LOSS = 0.002  # 0.2 % – low normal loss

def run_once(directory: str, variant: str, port: int) -> int:
    delay = PER_VARIANT_DELAY[variant]
    server_cmd = [
        sys.executable,
        "variant_file_server.py",
        "--dir", directory,
        "--port", str(port),
        "--variant", variant,
        "--loss", str(LOSS),
        "--delay", str(delay),
    ]
    server = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    client_cmd = [
        sys.executable,
        "variant_file_client.py",
        "--port", str(port),
        "--output", os.devnull,
    ]
    proc = subprocess.run(client_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    server.terminate()

    chosen = 0
    for line in proc.stdout.splitlines():
        if line.startswith("[CHOSEN_BITRATE]"):
            chosen = int(line.split()[1])
            break
    print(f"{variant:>7} delay={delay*1e3:.1f} ms  → {chosen} kbps")
    return chosen

def main():
    parser = argparse.ArgumentParser(description="Run per-variant delays to get three distinct bitrates")
    parser.add_argument("--dir", required=True, help="Directory with <bitrate>k files")
    parser.add_argument("--start-port", type=int, default=6400)
    parser.add_argument("--plot", default="unique_delay_adaptation.png")
    args = parser.parse_args()

    results = {}
    port = args.start_port
    for v in VARIANTS:
        results[v] = run_once(args.dir, v, port)
        port += 1

    # Plot
    variants = list(results.keys())
    bitrates = [results[v] for v in variants]
    plt.bar(variants, bitrates, color=["#4c72b0", "#55a868", "#c44e52"])
    plt.ylabel("Chosen bitrate (kbps)")
    plt.title("Distinct bitrates using per-variant delay (loss 0.2 %)")
    plt.tight_layout()
    plt.savefig(args.plot)
    print(f"Plot saved to {args.plot}")

if __name__ == "__main__":
    main() 