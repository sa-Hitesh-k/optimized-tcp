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
import random
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
    send_completion: bool = False,
) -> None:
    """Core transfer loop implementing the selected TCP-style variant."""
    send_control = getattr(server_socket, "send_control", server_socket.sendto)
    file_size = os.path.getsize(path)
    total_packets = math.ceil(file_size / CHUNK_SIZE)

    # First, inform client about network parameters so it can self-configure.
    # We send four big-endian floats: loss, delay, jitter, bw(Mbps) with 0 = ∞.
    bw_value = 0.0 if bandwidth_mbps is None else float(bandwidth_mbps)
    param_payload = struct.pack('>ffff', float(loss), float(delay), float(jitter), bw_value)
    send_control(param_payload, client_addr)

    # Inform client about incoming file size
    send_control(struct.pack('>Q', file_size), client_addr)
    print(f"[+] Sent file size ({file_size} bytes) to {client_addr}")

    tahoe = CongestionCls()

    # Track progress
    base_seq = 0  # oldest unacknowledged sequence number
    next_seq = 0  # next sequence number to send

    # Cache file content in memory for simplicity (avoid repeated disk seeks)
    with open(path, 'rb') as f:
        data = f.read()

    start_time = time.monotonic()

    # Map sequence number to its most recent send time.
    in_flight = {}

    def retransmit(seq_num: int, event: str) -> None:
        packet = struct.pack('>I', seq_num) + data[
            seq_num * CHUNK_SIZE : (seq_num + 1) * CHUNK_SIZE
        ]
        server_socket.sendto(packet, client_addr)
        in_flight[seq_num] = time.monotonic()
        print(
            f"[TRACE] elapsed={time.monotonic() - start_time:.3f} "
            f"event={event} seq={seq_num} cwnd={tahoe.cwnd} ssthresh={tahoe.ssthresh}"
        )
        print(
            f"[TRACE] elapsed={time.monotonic() - start_time:.3f} "
            f"event=RETRANSMIT seq={seq_num} "
            f"cwnd={tahoe.cwnd} ssthresh={tahoe.ssthresh}"
        )

    while base_seq < total_packets:
        # Send data while window allows
        while next_seq - base_seq < tahoe.cwnd and next_seq < total_packets:
            offset = next_seq * CHUNK_SIZE
            chunk = data[offset : offset + CHUNK_SIZE]
            header = struct.pack('>I', next_seq)
            server_socket.sendto(header + chunk, client_addr)
            in_flight[next_seq] = time.monotonic()
            next_seq += 1

        # Wait for ACKs (non-blocking)
        server_socket.settimeout(0.01)
        try:
            ack_bytes, _ = server_socket.recvfrom(4)
            ack_number = struct.unpack('>I', ack_bytes)[0]
            if base_seq < ack_number <= next_seq:
                base_seq = ack_number
                # Remove acknowledged packets from in-flight list
                for seq_num in list(in_flight):
                    if seq_num < ack_number:
                        in_flight.pop(seq_num, None)
                old_cwnd = tahoe.cwnd
                if isinstance(tahoe, TCPNewReno):
                    partial_ack = tahoe.on_new_ack(ack_number)
                else:
                    tahoe.on_ack()
                    partial_ack = False
                if tahoe.cwnd != old_cwnd:
                    print(
                        f"[TRACE] elapsed={time.monotonic() - start_time:.3f} "
                        f"event=ACK cwnd={tahoe.cwnd} ssthresh={tahoe.ssthresh}"
                    )
                if partial_ack and base_seq < next_seq:
                    retransmit(base_seq, "PARTIAL_ACK")
            elif (
                ack_number == base_seq
                and base_seq < next_seq
                and isinstance(tahoe, TCPNewReno)
                and tahoe.on_duplicate_ack(next_seq - base_seq, next_seq - 1)
            ):
                retransmit(base_seq, "FAST_RETRANSMIT")
        except socket.timeout:
            pass  # no ack this moment

        # Check for timeouts (loss)
        current_time = time.monotonic()
        sent_at = in_flight.get(base_seq)
        if sent_at is not None and current_time - sent_at > ACK_TIMEOUT:
            old_cwnd = tahoe.cwnd
            old_ssthresh = tahoe.ssthresh
            if isinstance(tahoe, TCPNewReno):
                tahoe.on_timeout(next_seq - base_seq)
            else:
                tahoe.on_timeout()
            print(
                f"[TRACE] elapsed={time.monotonic() - start_time:.3f} "
                f"event=TIMEOUT seq={base_seq} cwnd={old_cwnd}->{tahoe.cwnd} "
                f"ssthresh={old_ssthresh}->{tahoe.ssthresh}"
            )
            for retry_seq in sorted(in_flight):
                print(
                    f"[TRACE] elapsed={time.monotonic() - start_time:.3f} "
                    f"event=RETRANSMIT seq={retry_seq} "
                    f"cwnd={tahoe.cwnd} ssthresh={tahoe.ssthresh}"
                )
            # Retransmit from first unacknowledged packet
            next_seq = base_seq
            in_flight.clear()

        # Progress display
        if base_seq % 100 == 0:
            progress = (base_seq / total_packets) * 100
            throughput = (base_seq * CHUNK_SIZE) / (time.monotonic() - start_time + 1e-6) / 1024 / 1024
            print(
                f"\rProgress: {progress:6.2f}% | cwnd={tahoe.cwnd:3d} | ssthresh={tahoe.ssthresh:3d} | "
                f"throughput={throughput:6.2f} MB/s",
                end='',
            )

    if send_completion:
        send_control(b"TRANSFER_COMPLETE", client_addr)
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
    parser.add_argument('--seed', type=int, default=None, help='Optional random seed for repeatable simulated loss')

    args = parser.parse_args()
    if args.seed is not None:
        random.seed(args.seed)

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

    send_file(
        sock,
        client_addr,
        args.file,
        CongestionCls,
        loss=args.loss,
        delay=args.delay,
        jitter=args.jitter,
        bandwidth_mbps=args.bandwidth,
        send_completion=True,
    )
    print(f"[METRIC] data_drops={sock.dropped_packets}")


if __name__ == '__main__':
    main() 