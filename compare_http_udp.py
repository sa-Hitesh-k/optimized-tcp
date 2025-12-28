import subprocess, sys, time, os, argparse, threading, http.server, socketserver, requests, matplotlib.pyplot as plt
from pathlib import Path

FILE_NAME = "1500k.mp4"  # video file to transfer
HTTP_PORT = 8000
UDP_PORT = 7000


def start_http_server(directory: str) -> subprocess.Popen:
    """Start a simple HTTP server serving *directory* on HTTP_PORT."""
    handler = http.server.SimpleHTTPRequestHandler
    # Use --directory since Python 3.7
    cmd = [sys.executable, "-m", "http.server", str(HTTP_PORT), "--directory", directory]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def download_http(url: str, output: Path) -> float:
    start = time.time()
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with output.open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
    elapsed = time.time() - start
    return elapsed


def run_udp_transfer(file_path: str, out_path: str) -> float:
    # Start UDP server (Tahoe)
    server = subprocess.Popen([
        sys.executable,
        "tahoe_server.py",
        "--file", file_path,
        "--port", str(UDP_PORT),
        "--variant", "tahoe",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)

    start = time.time()
    client = subprocess.run([
        sys.executable,
        "tahoe_client.py",
        "--port", str(UDP_PORT),
        "--output", out_path,
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    elapsed = time.time() - start

    server.terminate()
    # print client stdout for diagnostics
    print(client.stdout)
    return elapsed


def main():
    parser = argparse.ArgumentParser(description="Compare HTTP (TCP) vs UDP Tahoe transfer of the same video")
    parser.add_argument("--dir", default="reps", help="Directory containing the video encodes")
    parser.add_argument("--out-dir", default="compare_outputs", help="Where to store downloaded files")
    args = parser.parse_args()

    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    file_path = os.path.join(args.dir, FILE_NAME)
    if not os.path.isfile(file_path):
        print("File", file_path, "not found")
        sys.exit(1)

    # HTTP part
    print("Starting HTTP server …")
    http_srv = start_http_server(args.dir)
    time.sleep(1.0)  # wait for bind
    http_output = Path(args.out_dir) / "http_" / FILE_NAME
    http_output.parent.mkdir(exist_ok=True)
    http_url = f"http://127.0.0.1:{HTTP_PORT}/{FILE_NAME}"
    print("Downloading via HTTP", http_url)
    http_time = download_http(http_url, http_output)
    http_size = http_output.stat().st_size / 1_000_000  # MB
    http_rate = http_size * 8 / http_time  # Mbps

    http_srv.terminate()

    # UDP part
    print("\nDownloading via UDP (Tahoe)…")
    udp_output = Path(args.out_dir) / "udp_" / FILE_NAME
    udp_output.parent.mkdir(exist_ok=True)
    udp_time = run_udp_transfer(file_path, str(udp_output))
    udp_size = udp_output.stat().st_size / 1_000_000
    udp_rate = udp_size * 8 / udp_time

    # Summary
    print("\n=== Comparison ===")
    print(f"HTTP: {http_size:.2f} MB in {http_time:.2f} s  → {http_rate:.2f} Mbps")
    print(f"UDP : {udp_size:.2f} MB in {udp_time:.2f} s  → {udp_rate:.2f} Mbps")

    # --- Plot performance metrics (throughput & time) ---
    protos = ["HTTP (TCP)", "UDP Tahoe"]
    rates  = [http_rate, udp_rate]
    times  = [http_time, udp_time]

    x = range(len(protos))
    width = 0.35

    fig, ax1 = plt.subplots(figsize=(7, 4))
    ax2 = ax1.twinx()

    bars1 = ax1.bar([i - width/2 for i in x], rates, width, label="Throughput (Mbps)", color="#4c72b0")
    bars2 = ax2.bar([i + width/2 for i in x], times, width, label="Time (s)", color="#55a868")

    ax1.set_ylabel("Throughput (Mbps)", color="#4c72b0")
    ax2.set_ylabel("Transfer time (s)",  color="#55a868")
    ax1.set_xticks(list(x))
    ax1.set_xticklabels(protos)
    ax1.set_title("HTTP vs UDP Tahoe – throughput & transfer time")

    # Annotate bars
    for bar in bars1:
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height()+1, f"{bar.get_height():.1f}", ha="center", color="#4c72b0")
    for bar in bars2:
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height()+0.02, f"{bar.get_height():.2f}", ha="center", color="#55a868")

    fig.tight_layout()
    plot_path = os.path.join(args.out_dir, "http_vs_udp_metrics.png")
    plt.savefig(plot_path)
    print("Plot saved to", plot_path)


if __name__ == "__main__":
    main() 