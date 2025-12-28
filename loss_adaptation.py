import argparse
import subprocess
import sys
import time
import os
from typing import Dict

import matplotlib.pyplot as plt  # type: ignore

VARIANTS = ["tahoe", "reno", "newreno"]


def run_once(directory: str, variant: str, port: int, loss: float) -> int:
    """Start server+client for *variant* at given loss and return chosen bitrate (kbps)."""
    # Launch server
    server_cmd = [
        sys.executable,
        "variant_file_server.py",
        "--dir",
        directory,
        "--port",
        str(port),
        "--variant",
        variant,
        "--loss",
        str(loss),
    ]
    server = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    time.sleep(1.0)  # allow manifest to be ready

    # Launch client
    client_cmd = [
        sys.executable,
        "variant_file_client.py",
        "--port",
        str(port),
        "--output",
        os.devnull,
    ]
    proc = subprocess.run(client_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    server.terminate()

    # Parse chosen bitrate
    for line in proc.stdout.splitlines():
        if line.startswith("[CHOSEN_BITRATE]"):
            return int(line.split()[1])
    return 0  # fallback if parse failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Run bitrate adaptation for all variants at a given loss rate")
    parser.add_argument("--dir", required=True, help="Directory with <bitrate>k files")
    parser.add_argument("--loss", type=float, default=0.001, help="Packet loss rate (e.g., 0.001 for 0.1 %)")
    parser.add_argument("--start-port", type=int, default=6300)
    parser.add_argument("--plot", default="adaptation_single_loss.png")
    args = parser.parse_args()

    results: Dict[str, int] = {}
    port = args.start_port
    for variant in VARIANTS:
        print(f"=== {variant.upper()} === (loss={args.loss*100:.2f} %)".center(60, "-"))
        br = run_once(args.dir, variant, port, args.loss)
        results[variant] = br
        print(f"Chosen bitrate: {br} kbps\n")
        port += 1

    # Plot results
    plt.figure(figsize=(6, 4))
    variants = list(results.keys())
    bitrates = [results[v] for v in variants]
    plt.bar(variants, bitrates, color=["#4c72b0", "#55a868", "#c44e52"])
    plt.ylabel("Chosen bitrate (kbps)")
    plt.title(f"Adaptive bitrate at loss {args.loss*100:.2f} %")
    plt.tight_layout()
    plt.savefig(args.plot)
    print(f"Plot saved to {args.plot}")

    # Summary
    print("\nSummary")
    for v, br in results.items():
        print(f"{v:>7}: {br} kbps")


if __name__ == "__main__":
    main() 