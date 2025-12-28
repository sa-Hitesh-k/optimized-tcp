from dataclasses import dataclass


@dataclass
class TCPReno:
    """Very small-scale application-layer approximation of TCP Reno.

    For this project we keep the logic minimal and compatible with the
    simple server loop: only *on_ack* (for successful delivery) and
    *on_timeout* (for loss) are required.

    Differences vs Tahoe implemented here:
    • After loss Reno resets the congestion window to *ssthresh* instead
      of 1, so it enters congestion-avoidance immediately instead of
      slow-start (unless ssthresh==1).
    • The *on_ack* growth rules are identical to Tahoe for simplicity.
    """

    cwnd: int = 1       # congestion window (packets)
    ssthresh: int = 16  # slow-start threshold (packets)

    # ------------------------------------------------------------------
    # Public API expected by the server
    # ------------------------------------------------------------------
    def on_ack(self) -> None:
        """Invoked when new data is acknowledged."""
        if self.cwnd < self.ssthresh:
            # Slow-start (exponential growth)
            self.cwnd *= 2
        else:
            # Congestion-avoidance (linear growth)
            self.cwnd += 1

    def on_timeout(self) -> None:
        """Invoked when the sender detects a loss (timeout)."""
        # Multiplicative decrease
        self.ssthresh = max(self.cwnd // 2, 1)
        # Reno sets cwnd to ssthresh (enters congestion-avoidance)
        self.cwnd = max(self.ssthresh, 1)

    # ------------------------------------------------------------------
    def __str__(self) -> str:  # pragma: no cover – for diagnostics only
        return f"TCPReno(cwnd={self.cwnd}, ssthresh={self.ssthresh})" 