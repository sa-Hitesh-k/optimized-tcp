"""tahoe_server.py

Send a file to a single client over UDP while applying TCP Tahoe congestion
control in user space.

Usage:
    python tahoe_server.py --file 101191-video-720.mp4

The client must be started separately (see tahoe_client.py).
"""
import argparse
import math
import os
import socket
import struct
import time
from typing import Tuple, Any, Optional

from network_simulator import NetworkSimulator

# Congestion-control variants
from tcp_tahoe import TCPTahoe
from tcp_reno import TCPReno
from tcp_newreno import TCPNewReno

# Size of each data segment (bytes)
CHUNK_SIZE = 1024
# Timeout (seconds) to wait for an ACK before presuming loss
ACK_TIMEOUT = 1.0


def send_file(
    server_socket: Any,
    client_addr: Tuple[str, int],
    path: str,
    CongestionCls: Any,
    *,
    loss: float,
    delay: float,
    jitter: float,
    bandwidth_mbps: Optional[float],
) -> None:
    """Core transfer loop implementing TCP Tahoe."""
    file_size = os.path.getsize(path)
    total_packets = math.ceil(file_size / CHUNK_SIZE)

    # First, inform client about network parameters so it can self-configure.
    # We send four big-endian floats: loss, delay, jitter, bw(Mbps) with 0 = ∞.
    bw_value = 0.0 if bandwidth_mbps is None else float(bandwidth_mbps)
    param_payload = struct.pack('>ffff', float(loss), float(delay), float(jitter), bw_value)
    server_socket.sendto(param_payload, client_addr)

    # Inform client about incoming file size
    server_socket.sendto(struct.pack('>Q', file_size), client_addr)
    print(f"[+] Sent file size ({file_size} bytes) to {client_addr}")

    tahoe = CongestionCls()

    # Track progress
    base_seq = 0  # oldest unacknowledged sequence number
    next_seq = 0  # next sequence number to send

    # Cache file content in memory for simplicity (avoid repeated disk seeks)
    with open(path, 'rb') as f:
        data = f.read()

    start_time = time.time()

    # Map seq -> (send_time, retries)
    in_flight = {}

    while base_seq < total_packets:
        # Send data while window allows
        while next_seq - base_seq < tahoe.cwnd and next_seq < total_packets:
            offset = next_seq * CHUNK_SIZE
            chunk = data[offset : offset + CHUNK_SIZE]
            header = struct.pack('>I', next_seq)
            server_socket.sendto(header + chunk, client_addr)
            in_flight[next_seq] = time.time()
            next_seq += 1

        # Wait for ACKs (non-blocking)
        server_socket.settimeout(0.01)
        try:
            ack_bytes, _ = server_socket.recvfrom(4)
            ack_seq = struct.unpack('>I', ack_bytes)[0]
            if ack_seq >= base_seq:
                # New ACK
                base_seq = ack_seq + 1
                # Remove acknowledged packets from in-flight list
                for seq_num in list(in_flight):
                    if seq_num <= ack_seq:
                        in_flight.pop(seq_num, None)
                tahoe.on_ack()
        except socket.timeout:
            pass  # no ack this moment

        # Check for timeouts (loss)
        current_time = time.time()
        for seq_num, sent_at in list(in_flight.items()):
            if current_time - sent_at > ACK_TIMEOUT:
                print(f"[!] Timeout for seq {seq_num} - triggering Tahoe back-off")
                tahoe.on_timeout()
                # Retransmit from first unacknowledged packet
                next_seq = base_seq
                in_flight.clear()
                break  # break out to restart send loop with new cwnd

        # Progress display
        if base_seq % 100 == 0:
            progress = (base_seq / total_packets) * 100
            throughput = (base_seq * CHUNK_SIZE) / (time.time() - start_time + 1e-6) / 1024 / 1024
            print(
                f"\rProgress: {progress:6.2f}% | cwnd={tahoe.cwnd:3d} | ssthresh={tahoe.ssthresh:3d} | "
                f"throughput={throughput:6.2f} MB/s",
                end='',
            )

    print("\n[+] Transfer complete")


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP video sender using TCP Tahoe congestion control")
    parser.add_argument('--file', required=True, help='Path to video file')
    parser.add_argument('--port', type=int, default=5001, help='UDP port to bind')
    parser.add_argument('--variant', choices=['tahoe', 'reno', 'newreno'], default='tahoe', help='Congestion control algorithm')

    # Network-simulation parameters
    parser.add_argument('--loss', type=float, default=0.0, help='Packet loss rate (0-1)')
    parser.add_argument('--delay', type=float, default=0.0, help='Base one-way delay in seconds')
    parser.add_argument('--jitter', type=float, default=0.0, help='Jitter (std-dev) in seconds')
    parser.add_argument('--bandwidth', type=float, default=None, help='Bandwidth cap in Mbps (per direction; 0 or negative for unlimited)')

    args = parser.parse_args()

    if not os.path.isfile(args.file):
        raise FileNotFoundError(args.file)

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    raw_sock.bind(('0.0.0.0', args.port))

    # Wrap in simulator
    bw_bytes = args.bandwidth * 1_000_000 / 8 if args.bandwidth else None
    sock = NetworkSimulator(
        raw_sock,
        loss_rate=args.loss,
        delay=args.delay,
        jitter=args.jitter,
        bandwidth=bw_bytes,
    )

    bw_desc = 'unlimited' if bw_bytes is None else f"{args.bandwidth}"
    print(
        f"[+] Listening on 0.0.0.0:{args.port} | variant={args.variant} | "
        f"loss={args.loss} | delay={args.delay}s | jitter={args.jitter}s | "
        f"bw={bw_desc} Mbps"
    )

    print('[*] Waiting for client request ...')
    # Expect any initial datagram from client indicating readiness
    _, client_addr = sock.recvfrom(1)
    print(f"[+] Client {client_addr} connected. Starting transfer …")

    # Pick congestion-control class
    variant_map = {
        'tahoe': TCPTahoe,
        'reno': TCPReno,
        'newreno': TCPNewReno,
    }
    CongestionCls = variant_map[args.variant]

    send_file(sock, client_addr, args.file, CongestionCls, loss=args.loss, delay=args.delay, jitter=args.jitter, bandwidth_mbps=args.bandwidth)


if __name__ == '__main__':
    main() 