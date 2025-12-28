from dataclasses import dataclass

from tcp_reno import TCPReno


@dataclass
class TCPNewReno(TCPReno):
    """Tiny placeholder for TCP NewReno.

    True NewReno improves fast-recovery behaviour, but that requires
    per-ACK duplicate tracking and partial ACK handling which our simple
    server does not implement.  For comparative experiments we keep the
    window adjustment identical to Reno; the class exists to demonstrate
    how a new variant can be plugged in without altering the transfer
    logic.  You can extend it later with a more faithful algorithm.
    """

    def __str__(self) -> str:  # pragma: no cover – diagnostics only
        return f"TCPNewReno(cwnd={self.cwnd}, ssthresh={self.ssthresh})" 