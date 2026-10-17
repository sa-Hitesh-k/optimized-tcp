from dataclasses import dataclass
from typing import Optional

from tcp_reno import TCPReno


@dataclass
class TCPNewReno(TCPReno):
    """Application-layer NewReno fast recovery, driven by cumulative ACKs."""

    duplicate_ack_count: int = 0
    in_fast_recovery: bool = False
    recover_point: int = 0

    def on_duplicate_ack(self, flight_size: int, highest_sent: int) -> bool:
        """Return True when the third duplicate ACK triggers fast retransmit."""
        if self.in_fast_recovery:
            self.cwnd += 1
            return False

        self.duplicate_ack_count += 1
        if self.duplicate_ack_count != 3:
            return False

        self.ssthresh = max(flight_size // 2, 2)
        self.cwnd = self.ssthresh + 3
        self.recover_point = highest_sent + 1
        self.in_fast_recovery = True
        return True

    def on_new_ack(self, ack_number: int) -> bool:
        """Process an advancing cumulative ACK; return True for a partial ACK."""
        self.duplicate_ack_count = 0
        if not self.in_fast_recovery:
            self.on_ack()
            return False

        if ack_number >= self.recover_point:
            self.cwnd = self.ssthresh
            self.in_fast_recovery = False
            self.recover_point = 0
            return False

        self.cwnd = self.ssthresh + 3
        return True

    def on_timeout(self, flight_size: Optional[int] = None) -> None:
        """Fall back to slow start and leave fast recovery after an RTO."""
        if flight_size is None:
            super().on_timeout()
        else:
            self.ssthresh = max(flight_size // 2, 2)
            self.cwnd = 1
        self.duplicate_ack_count = 0
        self.in_fast_recovery = False
        self.recover_point = 0

    def __str__(self) -> str:  # pragma: no cover – for diagnostics only
        return f"TCPNewReno(cwnd={self.cwnd}, ssthresh={self.ssthresh})"
