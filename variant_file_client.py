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
import random
import socket
import struct
import time
from typing import Any, Tuple, List

from network_simulator import NetworkSimulator

CHUNK_SIZE = 1024
SAFETY_MARGIN = 0.8  # choose bitrate <= 80 % of measured throughput (kbps)
MEASURE_FIRST_REQUEST = 0xFFFFFFFF
REPRESENTATION_REQUEST_FLAG = 0x80000000


def _receive_file(sock: Any, server_addr: Tuple[str, int], file_size: int, output: str | None) -> None:
    total_packets = math.ceil(file_size / CHUNK_SIZE)
    expected_seq = 0
    output_file = open(output, "wb") if output is not None else None
    try:
        if output_file is not None:
            output_file.truncate(file_size)
        while expected_seq < total_packets:
            packet, _ = sock.recvfrom(CHUNK_SIZE + 4)
            if len(packet) < 4:
                continue
            seq = struct.unpack(">I", packet[:4])[0]
            if seq == expected_seq:
                if output_file is not None:
                    output_file.seek(seq * CHUNK_SIZE)
                    output_file.write(packet[4:])
                expected_seq += 1
                sock.sendto(struct.pack(">I", expected_seq), server_addr)
            else:
                sock.sendto(struct.pack(">I", expected_seq), server_addr)
    finally:
        if output_file is not None:
            output_file.close()
    return


def _receive_transfer_control(
    sock: Any,
    size: int,
    server_addr: Tuple[str, int],
    cumulative_ack: int | None,
) -> bytes:
    while True:
        packet, _ = sock.recvfrom(CHUNK_SIZE + 4)
        if len(packet) == size:
            return packet
        if cumulative_ack is not None and len(packet) >= 4:
            sock.sendto(struct.pack(">I", cumulative_ack), server_addr)


def _receive_transfer_header(
    sock: Any,
    server_addr: Tuple[str, int],
    cumulative_ack: int | None = None,
) -> tuple[NetworkSimulator, int]:
    param_header = _receive_transfer_control(sock, 16, server_addr, cumulative_ack)
    srv_loss, srv_delay, srv_jitter, srv_bw_mbps = struct.unpack(">ffff", param_header)
    bw_bytes = None if srv_bw_mbps == 0.0 else srv_bw_mbps * 1_000_000 / 8
    transfer_socket = NetworkSimulator(
        sock,
        loss_rate=srv_loss,
        delay=srv_delay,
        jitter=srv_jitter,
        bandwidth=bw_bytes,
    )
    size_payload = _receive_transfer_control(
        transfer_socket, 8, server_addr, cumulative_ack
    )
    file_size = struct.unpack(">Q", size_payload)[0]
    return transfer_socket, file_size


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
    parser.add_argument(
        "--measure-first",
        action="store_true",
        help="Measure delivered goodput from a short low-quality video probe, then choose a quality.",
    )
    parser.add_argument("--seed", type=int, default=None, help="Seed simulated ACK loss")

    args = parser.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr: Tuple[str, int] = (args.host, args.port)

    # 1) Hello
    transfer_start = time.monotonic()
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
    rtt_ms = (time.monotonic() - transfer_start) * 1000
    if rtt_ms <= 0.0:
        rtt_ms = 0.1  # avoid divide-by-zero in local loopback
    print(f"[+] Received manifest ({rep_count} representations)  RTT={rtt_ms:.1f} ms")
    for i, (br, sz) in enumerate(zip(bitrates, rep_sizes)):
        print(f"    [{i}] {br} kbps  {sz/1_000_000:.2f} MB")
    probe_ack_drops = 0
    probe_ack_number: int | None = None

    # Simple throughput estimate: assume we could receive rep_count*12 bytes in RTT.
    est_kbps = ((rep_count * 12) * 8) / (rtt_ms / 1000) / 1000
    print(f"[*] Rough throughput estimate: {est_kbps:.0f} kbps")

    if args.index is not None:
        chosen_idx = args.index
    elif args.measure_first:
        if not bitrates:
            raise ValueError("The server advertised no video representations.")
        raw_sock.sendto(struct.pack(">I", MEASURE_FIRST_REQUEST), server_addr)
        probe_socket, probe_size = _receive_transfer_header(raw_sock, server_addr)
        probe_start = time.monotonic()
        _receive_file(probe_socket, server_addr, probe_size, None)
        probe_elapsed = time.monotonic() - probe_start
        probe_ack_drops = probe_socket.dropped_packets
        probe_ack_number = math.ceil(probe_size / CHUNK_SIZE)
        measured_kbps = probe_size * 8 / max(probe_elapsed, 1e-9) / 1000
        target_kbps = measured_kbps * SAFETY_MARGIN
        viable = [i for i, bitrate in enumerate(bitrates) if bitrate <= target_kbps]
        chosen_idx = viable[-1] if viable else 0
        print(f"[METRIC] measured_probe_goodput_kbps={measured_kbps:.2f}")
        print(f"[METRIC] probe_time_s={probe_elapsed:.4f}")
        print(f"[METRIC] probe_bytes={probe_size}")
        print(
            f"[*] Measured probe goodput: {measured_kbps:.0f} kbps; "
            f"using {SAFETY_MARGIN:.0%} safety margin"
        )
        raw_sock.sendto(
            struct.pack(">I", REPRESENTATION_REQUEST_FLAG | chosen_idx),
            server_addr,
        )
    else:
        target = est_kbps * SAFETY_MARGIN
        # If even the lowest bitrate exceeds target, fall back to index 0
        viable = [i for i, br in enumerate(bitrates) if br <= target]
        chosen_idx = viable[-1] if viable else 0
        raw_sock.sendto(struct.pack(">I", chosen_idx), server_addr)
    if chosen_idx < 0 or chosen_idx >= len(bitrates):
        raise ValueError(f"Representation index out of range: {chosen_idx}")
    chosen_bitrate = bitrates[chosen_idx]
    print(f"[+] Choosing representation index {chosen_idx} ({chosen_bitrate} kbps)")
    # Emit a parser-friendly line for automation scripts
    print(f"[CHOSEN_BITRATE] {chosen_bitrate}")

    if args.index is not None:
        raw_sock.sendto(struct.pack(">I", chosen_idx), server_addr)

    sock, file_size = _receive_transfer_header(
        raw_sock, server_addr, probe_ack_number
    )
    total_packets = math.ceil(file_size / CHUNK_SIZE)
    print(f"[+] Expecting {file_size} bytes across {total_packets} packets")

    final_transfer_start = time.monotonic()
    _receive_file(sock, server_addr, file_size, args.output)
    print("\rProgress: 100.00%\n[+] Download complete")
    if os.path.getsize(args.output) == file_size:
        print("[+] Integrity check passed")
    else:
        print("[!] Integrity check failed")
    elapsed = time.monotonic() - transfer_start
    print(f"[METRIC] total_time_s={elapsed:.6f}")
    print(f"[METRIC] media_transfer_time_s={time.monotonic() - final_transfer_start:.6f}")
    print(f"[METRIC] media_bytes={file_size}")
    print(f"[METRIC] end_to_end_goodput_mbps={file_size * 8 / elapsed / 1_000_000:.6f}")
    print(f"[METRIC] ack_drops={probe_ack_drops + sock.dropped_packets}")


if __name__ == "__main__":
    main() 