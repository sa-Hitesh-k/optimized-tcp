from dataclasses import dataclass

@dataclass
class TCPTahoe:
    """Minimal implementation of TCP Tahoe congestion-window logic usable at the application layer."""

    # Congestion window in packets (segments)
    cwnd: int = 1
    # Slow-start threshold in packets
    ssthresh: int = 16

    def on_ack(self) -> None:
        """Call when an ACK acknowledging new data is received."""
        if self.cwnd < self.ssthresh:
            # Slow start (exponential growth)
            self.cwnd *= 2
        else:
            # Congestion-avoidance (linear growth)
            self.cwnd += 1

    def on_timeout(self) -> None:
        """Call when a timeout occurs (packet loss detected)."""
        # Multiplicative decrease
        self.ssthresh = max(self.cwnd // 2, 1)
        # Reset to slow-start
        self.cwnd = 1

    def __str__(self) -> str:
        return f"TCPTahoe(cwnd={self.cwnd}, ssthresh={self.ssthresh})" 