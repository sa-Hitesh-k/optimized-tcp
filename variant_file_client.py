"""variant_file_client.py

Adaptive UDP video client for *variant_file_server.py*.

Handshake sequence::
    1) send 1-byte hello
    2) receive manifest – 4-byte count, then per-representation: bitrate_kbps(I), size(Q)
    3) pick representation index based on a rough bandwidth estimate (or user
       choice) and send it back to server (4-byte int)
    4) receive the standard 16-byte network parameter header sent by *send_file*
    5) receive 8-byte file-size header
    6) download file exactly like tahoe_client.py

The current adaptation policy is very naïve: it picks the highest bitrate that
is below 80 % of the estimated *available* throughput measured during the
manifest round-trip.  You can improve this later with moving averages etc.
"""

from __future__ import annotations

import argparse
import math
import os
import socket
import struct
import time
from typing import Any, Tuple, List

from network_simulator import NetworkSimulator

CHUNK_SIZE = 1024
SAFETY_MARGIN = 0.8  # choose bitrate <= 80 % of measured throughput (kbps)


def recv_exact(sock: Any, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk, _ = sock.recvfrom(size - len(data))
        if not chunk:
            raise ConnectionError("Connection closed unexpectedly")
        data += chunk
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="Adaptive UDP video client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6000)
    parser.add_argument("--output", default="adaptive_output.mp4")
    # Optional override: choose a fixed bitrate index
    parser.add_argument("--index", type=int, default=None, help="Force representation index (0-based)")

    args = parser.parse_args()

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr: Tuple[str, int] = (args.host, args.port)

    # 1) Hello
    start = time.time()
    raw_sock.sendto(b"\x01", server_addr)

    # 2) Manifest
    rep_count_bytes = recv_exact(raw_sock, 4)
    rep_count = struct.unpack(">I", rep_count_bytes)[0]
    bitrates: List[int] = []
    rep_sizes: List[int] = []
    for _ in range(rep_count):
        entry = recv_exact(raw_sock, 12)  # I + Q
        br, sz = struct.unpack(">IQ", entry)
        bitrates.append(br)
        rep_sizes.append(sz)
    rtt_ms = (time.time() - start) * 1000
    if rtt_ms <= 0.0:
        rtt_ms = 0.1  # avoid divide-by-zero in local loopback
    print(f"[+] Received manifest ({rep_count} representations)  RTT={rtt_ms:.1f} ms")
    for i, (br, sz) in enumerate(zip(bitrates, rep_sizes)):
        print(f"    [{i}] {br} kbps  {sz/1_000_000:.2f} MB")

    # Simple throughput estimate: assume we could receive rep_count*12 bytes in RTT.
    est_kbps = ((rep_count * 12) * 8) / (rtt_ms / 1000) / 1000
    print(f"[*] Rough throughput estimate: {est_kbps:.0f} kbps")

    if args.index is not None:
        chosen_idx = args.index
    else:
        target = est_kbps * SAFETY_MARGIN
        # If even the lowest bitrate exceeds target, fall back to index 0
        viable = [i for i, br in enumerate(bitrates) if br <= target]
        chosen_idx = viable[-1] if viable else 0
    chosen_bitrate = bitrates[chosen_idx]
    print(f"[+] Choosing representation index {chosen_idx} ({chosen_bitrate} kbps)")
    # Emit a parser-friendly line for automation scripts
    print(f"[CHOSEN_BITRATE] {chosen_bitrate}")

    # 3) Send chosen index
    raw_sock.sendto(struct.pack(">I", chosen_idx), server_addr)

    # 4) Receive 16-byte network parameter header (loss, delay, jitter, bw)
    param_header = recv_exact(raw_sock, 16)
    srv_loss, srv_delay, srv_jitter, srv_bw_mbps = struct.unpack(">ffff", param_header)

    # Use server values (no overrides for simplicity)
    bw_bytes = None if srv_bw_mbps == 0.0 else srv_bw_mbps * 1_000_000 / 8
    sock = NetworkSimulator(
        raw_sock,
        loss_rate=srv_loss,
        delay=srv_delay,
        jitter=srv_jitter,
        bandwidth=bw_bytes,
    )

    # 5) Receive file size
    file_size = struct.unpack(">Q", recv_exact(sock, 8))[0]
    total_packets = math.ceil(file_size / CHUNK_SIZE)
    print(f"[+] Expecting {file_size} bytes across {total_packets} packets")

    expected_seq = 0
    with open(args.output, "wb") as f:
        f.truncate(file_size)
        while expected_seq < total_packets:
            packet, _ = sock.recvfrom(CHUNK_SIZE + 4)
            seq = struct.unpack(">I", packet[:4])[0]
            if seq == expected_seq:
                # Write in-order packet
                f.seek(seq * CHUNK_SIZE)
                f.write(packet[4:])

                # Send ACK for this sequence number so the server can advance
                sock.sendto(struct.pack(">I", seq), server_addr)

                expected_seq += 1

                if expected_seq % 100 == 0 or expected_seq == total_packets:
                    print(f"\rProgress: {expected_seq/total_packets*100:6.2f}%", end="")
    print("\n[+] Download complete")
    if os.path.getsize(args.output) == file_size:
        print("[+] Integrity check passed")
    else:
        print("[!] Integrity check failed")


if __name__ == "__main__":
    main() 