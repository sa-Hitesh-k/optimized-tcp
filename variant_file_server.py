"""variant_file_server.py

UDP video sender that supports multiple pre-encoded representations (bit-rates).
The client first receives a *manifest* listing the available bit-rates and then
chooses one; the chosen file is then streamed using the existing send_file()
logic from tahoe_server.py (with TCP Tahoe/Reno/NewReno window control).

Usage example (directory layout)::

    representations/
        400k.mp4   # 400 kbps encode
        800k.mp4   # 800 kbps encode
        1500k.mp4  # 1.5 Mbps encode

    python variant_file_server.py --dir representations --port 6000 --variant reno

"""

from __future__ import annotations

import argparse
import os
import random
import re
import socket
import struct
import tempfile
from typing import List, Tuple, Any, Optional

from network_simulator import NetworkSimulator
from tahoe_server import send_file  # reuse existing implementation
from tcp_tahoe import TCPTahoe
from tcp_reno import TCPReno
from tcp_newreno import TCPNewReno

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
_BITRATE_RE = re.compile(r"(\d+)k", re.IGNORECASE)
MEASURE_FIRST_REQUEST = 0xFFFFFFFF
REPRESENTATION_REQUEST_FLAG = 0x80000000


def discover_representations(directory: str) -> List[Tuple[int, str, int]]:
    """Return [(bitrate_kbps, filepath, file_size), ...] sorted by bitrate."""
    reps = []
    for fname in os.listdir(directory):
        m = _BITRATE_RE.search(fname)
        if not m:
            continue
        bitrate = int(m.group(1))  # kbps
        path = os.path.join(directory, fname)
        if not os.path.isfile(path):
            continue
        reps.append((bitrate, path, os.path.getsize(path)))
    reps.sort(key=lambda t: t[0])
    if not reps:
        raise RuntimeError(f"No bitrate-labelled files found in {directory}")
    return reps


# -----------------------------------------------------------------------------
# Main logic
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-representation UDP video server")
    parser.add_argument("--dir", required=True, help="Directory containing <bitrate>k.<ext> encodes")
    parser.add_argument("--port", type=int, default=6000, help="UDP port")
    parser.add_argument("--variant", choices=["tahoe", "reno", "newreno"], default="tahoe")

    # Network parameters (same defaults as NetworkSimulator)
    parser.add_argument("--loss", type=float, default=0.0)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--jitter", type=float, default=0.0)
    parser.add_argument("--bandwidth", type=float, default=None, help="Mbps (0 = unlimited)")
    parser.add_argument("--seed", type=int, default=None, help="Seed simulated data loss")

    args = parser.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    reps = discover_representations(args.dir)
    print("[+] Found representations:")
    for br, path, sz in reps:
        print(f"    {br} kbps -> {os.path.basename(path)} ({sz/1_000_000:.2f} MB)")

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw_sock.bind(("0.0.0.0", args.port))

    bw_bytes = args.bandwidth * 1_000_000 / 8 if args.bandwidth else None
    sock = NetworkSimulator(
        raw_sock,
        loss_rate=args.loss,
        delay=args.delay,
        jitter=args.jitter,
        bandwidth=bw_bytes,
    )

    var_map = {"tahoe": TCPTahoe, "reno": TCPReno, "newreno": TCPNewReno}
    CongestionCls = var_map[args.variant]

    print(f"[+] Listening on 0.0.0.0:{args.port} (variant={args.variant})")

    waiting_msg_shown = False  # ensure we don't spam the same line continuously

    while True:
        if not waiting_msg_shown:
            print("[*] Waiting for client hello ...")
            waiting_msg_shown = True
        try:
            _, client_addr = sock.recvfrom(1)  # dummy byte
        except socket.timeout:
            # Keep waiting but don't print the message again
            continue  # loop and keep waiting
        print(f"[+] Client {client_addr} connected -> sending manifest")
        # Reset flag so next idle period prints the waiting message once
        waiting_msg_shown = False

        # ------------------------------------------------------------------
        # 1. Send manifest: 4-byte count, then for each: bitrate_kbps (I), size (Q)
        # ------------------------------------------------------------------
        manifest_hdr = struct.pack(">I", len(reps))
        sock.sendto(manifest_hdr, client_addr)
        for bitrate, _path, fsize in reps:
            sock.sendto(struct.pack(">IQ", bitrate, fsize), client_addr)

        # ------------------------------------------------------------------
        # 2. Receive 4-byte chosen representation index
        # ------------------------------------------------------------------
        idx_bytes, _ = sock.recvfrom(4)
        rep_idx = struct.unpack(">I", idx_bytes)[0]
        if rep_idx == MEASURE_FIRST_REQUEST:
            probe_bitrate, probe_path, _probe_size = reps[0]
            print(f"[+] Measure-first probe: first 256 KiB of {probe_bitrate} kbps video")
            probe_fd, probe_file = tempfile.mkstemp(prefix="optimized-tcp-probe-")
            try:
                with os.fdopen(probe_fd, "wb") as probe_output:
                    with open(probe_path, "rb") as source_video:
                        probe_output.write(source_video.read(256 * 1024))
                send_file(
                    sock,
                    client_addr,
                    probe_file,
                    CongestionCls,
                    loss=args.loss,
                    delay=args.delay,
                    jitter=args.jitter,
                    bandwidth_mbps=args.bandwidth,
                )
            finally:
                os.unlink(probe_file)
            while True:
                idx_bytes, _ = sock.recvfrom(4)
                request = struct.unpack(">I", idx_bytes)[0]
                if request & REPRESENTATION_REQUEST_FLAG:
                    rep_idx = request & ~REPRESENTATION_REQUEST_FLAG
                    break
                print(f"[TRACE] Ignoring non-selection packet during adaptation: {request}")
        if rep_idx >= len(reps):
            print("[!] Invalid representation index from client -> closing")
            continue
        bitrate_kbps, chosen_path, _fsize = reps[rep_idx]
        print(f"[+] Client chose {bitrate_kbps} kbps representation")

        # ------------------------------------------------------------------
        # 3. Stream the chosen file using existing send_file()
        # ------------------------------------------------------------------
        send_file(
            sock,
            client_addr,
            chosen_path,
            CongestionCls,
            loss=args.loss,
            delay=args.delay,
            jitter=args.jitter,
            bandwidth_mbps=args.bandwidth,
        )
        print(f"[METRIC] data_drops={sock.dropped_packets}")
        print("[+] Session finished\n")


if __name__ == "__main__":
    main() 