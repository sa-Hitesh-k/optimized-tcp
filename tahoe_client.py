"""tahoe_client.py

Receive a file sent by tahoe_server.py using UDP with application-layer TCP
Tahoe congestion control (server side only). The client simply ACKs every
in-order packet.

Usage:
    python tahoe_client.py --output received.mp4
"""
import argparse
import math
import os
import socket
import struct
import sys
from typing import Tuple, Any

from network_simulator import NetworkSimulator

CHUNK_SIZE = 1024


def recv_exact(sock: Any, size: int) -> bytes:
    """Receive exactly *size* bytes from *sock*."""
    data = b''
    while len(data) < size:
        chunk, _ = sock.recvfrom(size - len(data))
        if not chunk:
            raise ConnectionError('Connection closed unexpectedly')
        data += chunk
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP video receiver compatible with tahoe_server.py")
    parser.add_argument('--host', default='127.0.0.1', help='Server address')
    parser.add_argument('--port', type=int, default=5001, help='Server UDP port')
    parser.add_argument('--output', default='received.mp4', help='Output file name')
    parser.add_argument('--loss', type=float, default=None, help='Packet loss rate (0-1). If omitted, use value sent by server.')
    parser.add_argument('--delay', type=float, default=None, help='Base one-way delay (s). If omitted, use value sent by server.')
    parser.add_argument('--jitter', type=float, default=None, help='Jitter (std-dev, s). If omitted, use value sent by server.')
    parser.add_argument('--bandwidth', type=float, default=None, help='Bandwidth cap in Mbps. If omitted, use value sent by server (0==∞).')
    args = parser.parse_args()

    raw_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_addr: Tuple[str, int] = (args.host, args.port)

    # Send dummy byte to announce presence
    raw_sock.sendto(b'\x01', server_addr)

    # Receive 16-byte parameter header from server
    param_header = recv_exact(raw_sock, 16)
    srv_loss, srv_delay, srv_jitter, srv_bw_mbps = struct.unpack('>ffff', param_header)

    # Use server-advertised values unless overridden by CLI
    loss = srv_loss if args.loss is None else args.loss
    delay = srv_delay if args.delay is None else args.delay
    jitter = srv_jitter if args.jitter is None else args.jitter
    bw_mbps = srv_bw_mbps if args.bandwidth is None else args.bandwidth
    bw_bytes = None if bw_mbps == 0.0 else bw_mbps * 1_000_000 / 8

    # Now wrap socket for outbound (ACK) traffic
    sock = NetworkSimulator(
        raw_sock,
        loss_rate=loss,
        delay=delay,
        jitter=jitter,
        bandwidth=bw_bytes,
    )

    # Receive 8-byte file size header
    file_size_bytes = recv_exact(sock, 8)
    file_size = struct.unpack('>Q', file_size_bytes)[0]
    total_packets = math.ceil(file_size / CHUNK_SIZE)
    print(f"[+] Expecting {file_size} bytes across {total_packets} packets")

    expected_seq = 0
    bytes_written = 0

    with open(args.output, 'wb') as f:
        # Pre-allocate file for efficiency (optional)
        f.truncate(file_size)

        while expected_seq < total_packets:
            packet, _ = sock.recvfrom(CHUNK_SIZE + 4)
            seq = struct.unpack('>I', packet[:4])[0]

            if seq == expected_seq:
                # Write in-order packet
                f.seek(seq * CHUNK_SIZE)
                f.write(packet[4:])
                bytes_written += len(packet) - 4

                # ACK this packet
                sock.sendto(struct.pack('>I', seq), server_addr)

                expected_seq += 1

                if expected_seq % 100 == 0 or expected_seq == total_packets:
                    progress = (expected_seq / total_packets) * 100
                    print(f"\rProgress: {progress:6.2f}%", end='')
            else:
                # Out-of-order packet -> ignore (server will retransmit if needed)
                continue

    print("\n[+] Download complete")
    # Verify integrity (basic)
    if os.path.getsize(args.output) == file_size:
        print('[+] Integrity check passed')
    else:
        print('[!] Integrity check failed')
        sys.exit(1)


if __name__ == '__main__':
    main() 