import random
import threading
import time
from typing import Tuple, Any, Optional

import socket


class NetworkSimulator:
    """Wraps a standard socket to emulate real-world network conditions.

    Supported impairments (applied on *send* direction only):
      • Packet loss via *loss_rate* (0.0–1.0)
      • Propagation delay + jitter via *delay* and *jitter* (seconds)
      • Bandwidth cap via *bandwidth* (bytes/second)

    The class is deliberately lightweight – it proxies all undefined
    attributes to the underlying socket object, so it can be used as a
    drop-in replacement::

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sim  = NetworkSimulator(sock, loss_rate=0.01, delay=0.03, jitter=0.005)
        sim.sendto(b"payload", ("127.0.0.1", 5001))
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        loss_rate: float = 0.0,     # No packet loss
        delay: float = 0.0,         # No additional delay
        jitter: float = 0.0,        # No jitter
        bandwidth: Optional[float] = None,  # Unlimited unless specified
    ) -> None:
        if not 0.0 <= loss_rate <= 1.0:
            raise ValueError("loss_rate must be between 0 and 1")
        if delay < 0 or jitter < 0:
            raise ValueError("delay and jitter must be non-negative")

        self._sock = sock
        self.loss_rate = loss_rate
        self.delay = delay
        self.jitter = jitter
        # Convert bandwidth to bytes/s if provided in bits/s or bytes/s?
        self.bandwidth = bandwidth  # Already bytes/s expected.
        self.dropped_packets = 0

        # Book-keeping for simple token-bucket bandwidth limiting.
        self._lock = threading.Lock()
        self._next_send_time = time.time()

    # ---------------------------------------------------------------------
    # Public helpers
    # ---------------------------------------------------------------------
    def sendto(self, data: bytes, address: Tuple[str, int], *args: Any, **kwargs: Any) -> int:  # type: ignore[override]
        """Simulated *sendto* with loss, delay, jitter, and bandwidth cap."""
        # Simulate packet loss
        if random.random() < self.loss_rate:
            # Pretend the datagram was sent successfully but actually drop it.
            self.dropped_packets += 1
            return len(data)

        # Propagation delay + jitter
        total_delay = self._compute_delay()
        if total_delay > 0:
            time.sleep(total_delay)

        # Bandwidth throttling (simple leaky-bucket)
        if self.bandwidth is not None:
            self._rate_limit(len(data))

        return self._sock.sendto(data, address, *args, **kwargs)

    def send_control(self, data: bytes, address: Tuple[str, int]) -> int:
        """Send protocol setup data without simulated loss or impairment."""
        return self._sock.sendto(data, address)

    def recvfrom(self, bufsize: int, *args: Any, **kwargs: Any):  # type: ignore[override]
        """Direct passthrough for *recvfrom* – inbound impairments can be
        modelled by applying *sendto* constraints on the remote side."""
        return self._sock.recvfrom(bufsize, *args, **kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_delay(self) -> float:
        if self.delay == 0 and self.jitter == 0:
            return 0.0
        # Jitter as std-dev of normal distribution around *delay*
        if self.jitter == 0:
            return self.delay
        return max(0.0, random.gauss(self.delay, self.jitter))

    def _rate_limit(self, n_bytes: int) -> None:
        """Block until sending *n_bytes* respects the *bandwidth* budget."""
        assert self.bandwidth is not None  # for type checker
        seconds_per_byte = 1.0 / self.bandwidth
        now = time.time()
        with self._lock:
            earliest = self._next_send_time
            send_time = max(now, earliest)
            duration = n_bytes * seconds_per_byte
            self._next_send_time = send_time + duration
        # Sleep outside the lock so other senders are not blocked.
        to_sleep = send_time - now
        if to_sleep > 0:
            time.sleep(to_sleep)

    # ------------------------------------------------------------------
    # Duck-typing: delegate unknown attributes to underlying socket
    # ------------------------------------------------------------------
    def __getattr__(self, item: str):
        return getattr(self._sock, item)

    def __enter__(self):  # context manager passthrough
        self._sock.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._sock.__exit__(exc_type, exc_val, exc_tb) 