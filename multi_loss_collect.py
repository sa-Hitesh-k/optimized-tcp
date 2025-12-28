import argparse
import subprocess
import sys
import time
import os
from pathlib import Path
from typing import Dict, List

VARIANTS = ["tahoe", "reno", "newreno"]
DEFAULT_LOSSES = [0.0, 0.002, 0.003, 0.004, 0.007]  # 0 %, 0.2 %, 0.3 %, 0.4 %, 0.7 %


def run_transfer(directory: str, variant: str, port: int, loss: float, output_file: str) -> int:
    """Launch server + client for one variant/loss combo, save to *output_file* and return chosen bitrate."""
    # Start server (loss, no extra delay/jitter)
    server_cmd = [
        sys.executable,
        "variant_file_server.py",
        "--dir", directory,
        "--port", str(port),
        "--variant", variant,
        "--loss", str(loss),
        "--delay", "0.0",
        "--jitter", "0.0",
    ]
    server = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    # Ensure output dir exists
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    client_cmd = [
        sys.executable,
        "variant_file_client.py",
        "--port", str(port),
        "--output", output_file,
    ]
    proc = subprocess.run(client_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    server.terminate()

    bitrate = 0
    for line in proc.stdout.splitlines():
        if line.startswith("[CHOSEN_BITRATE]"):
            bitrate = int(line.split()[1])
            break
    print(proc.stdout)  # show client log for transparency
    return bitrate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run transfers for multiple loss rates and store received videos.")
    parser.add_argument("--dir", required=True, help="Directory with <bitrate>k files")
    parser.add_argument("--start-port", type=int, default=6800)
    parser.add_argument("--losses", nargs="*", type=float, default=DEFAULT_LOSSES,
                        help="Space-separated list of loss rates (e.g. 0.002 for 0.2 %)")
    parser.add_argument("--out-dir", default="received_videos", help="Directory to save received videos")
    args = parser.parse_args()

    results: Dict[str, List[int]] = {v: [] for v in VARIANTS}

    port = args.start_port
    for loss in args.losses:
        print("\n" + "="*70)
        print(f"Testing loss {loss*100:.2f} %")
        print("="*70)
        for variant in VARIANTS:
            outfile = os.path.join(args.out_dir, f"{variant}_loss{loss:.4f}.mp4")
            print(f"\n[{variant}] Starting transfer → {outfile}")
            bitrate = run_transfer(args.dir, variant, port, loss, outfile)
            results[variant].append(bitrate)
            print(f"[{variant}] Chosen bitrate: {bitrate} kbps\n")
            port += 1

    # Summary
    print("\nSummary table (kbps)")
    header = "Loss %".rjust(8) + " | " + " | ".join(v.rjust(7) for v in VARIANTS)
    print(header)
    print("-"*len(header))
    for idx, loss in enumerate(args.losses):
        row = f"{loss*100:7.2f} | " + " | ".join(f"{results[v][idx]:7d}" for v in VARIANTS)
        print(row)

    print("\nAll videos stored under", args.out_dir)


if __name__ == "__main__":
    main() 