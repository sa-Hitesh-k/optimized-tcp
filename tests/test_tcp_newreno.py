import contextlib
import io
import os
import struct
import tempfile
import unittest

from tahoe_server import send_file
from tcp_newreno import TCPNewReno


class TCPNewRenoTests(unittest.TestCase):
    def test_duplicate_acks_partial_ack_and_recovery_exit(self):
        congestion = TCPNewReno(cwnd=10, ssthresh=16)

        self.assertFalse(congestion.on_duplicate_ack(8, 19))
        self.assertFalse(congestion.on_duplicate_ack(8, 19))
        self.assertTrue(congestion.on_duplicate_ack(8, 19))
        self.assertEqual((congestion.ssthresh, congestion.cwnd), (4, 7))
        self.assertTrue(congestion.in_fast_recovery)

        self.assertFalse(congestion.on_duplicate_ack(8, 19))
        self.assertEqual(congestion.cwnd, 8)
        self.assertTrue(congestion.on_new_ack(15))
        self.assertEqual(congestion.cwnd, 7)
        self.assertTrue(congestion.in_fast_recovery)

        self.assertFalse(congestion.on_new_ack(20))
        self.assertEqual(congestion.cwnd, 4)
        self.assertFalse(congestion.in_fast_recovery)

    def test_timeout_leaves_fast_recovery(self):
        congestion = TCPNewReno(cwnd=10)
        for _ in range(3):
            retransmit = congestion.on_duplicate_ack(8, 19)

        self.assertTrue(retransmit)
        congestion.on_timeout(flight_size=6)

        self.assertEqual((congestion.ssthresh, congestion.cwnd), (3, 1))
        self.assertFalse(congestion.in_fast_recovery)
        self.assertEqual(congestion.duplicate_ack_count, 0)

    def test_sender_fast_retransmits_and_recovers_multiple_losses(self):
        class FakeSocket:
            def __init__(self):
                self.acks = iter((1, 1, 1, 1, 2, 5))
                self.sent_sequences = []

            def send_control(self, data, _address):
                return len(data)

            def sendto(self, data, _address):
                self.sent_sequences.append(struct.unpack(">I", data[:4])[0])
                return len(data)

            def settimeout(self, _timeout):
                pass

            def recvfrom(self, _size):
                return struct.pack(">I", next(self.acks)), ("127.0.0.1", 5000)

        with tempfile.NamedTemporaryFile(delete=False) as input_file:
            input_file.write(b"x" * (5 * 1024))
            input_path = input_file.name

        try:
            fake_socket = FakeSocket()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                send_file(
                    fake_socket,
                    ("127.0.0.1", 5000),
                    input_path,
                    TCPNewReno,
                    loss=0.0,
                    delay=0.0,
                    jitter=0.0,
                    bandwidth_mbps=None,
                )

            self.assertGreaterEqual(fake_socket.sent_sequences.count(1), 2)
            self.assertGreaterEqual(fake_socket.sent_sequences.count(2), 2)
            self.assertIn("event=FAST_RETRANSMIT seq=1", output.getvalue())
            self.assertIn("event=PARTIAL_ACK seq=2", output.getvalue())
        finally:
            os.unlink(input_path)


if __name__ == "__main__":
    unittest.main()
