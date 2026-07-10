# Adaptive Video Streaming over UDP with TCP-Style Congestion Control

> A from-scratch, application-layer implementation of TCP congestion control (**Tahoe**, **Reno**, **NewReno**) running on top of raw UDP sockets, combined with **adaptive bitrate (ABR) video streaming** and a built-in **network impairment simulator** (packet loss, delay, jitter, bandwidth caps). The project includes a full experiment suite that measures and plots how each algorithm behaves under different network conditions.
>
> **What makes it stand out:** instead of reacting to congestion *after* packets are already being dropped like traditional TCP, it **probes the network up front** by sending a tiny test message, measuring the round-trip time, and predicting the available bandwidth, then **picks the right video quality before sending a single frame**. The result is faster, more reliable, lower-power delivery on weak or congested links, where getting the content through matters more than raw resolution.

---

## The Big Idea: It Predicts the Network *Before* Sending, Instead of Reacting After It Breaks

**This is what makes the project different from ordinary streaming.**

Traditional TCP is **reactive**: it just starts blasting data at full quality and only slows down *after* the network is already overloaded and packets have started getting lost. That means every download begins by causing the very congestion it then has to recover from, wasting time, power, and bandwidth on data that never makes it through.

This project flips that around and is **predictive**:

1. **Probe first.** Before sending any video, the client fires off a tiny **dummy "hello" message** and starts a stopwatch.
2. **Measure the reality of the link.** It records exactly how long that message takes to come back (the **round-trip time (RTT)**) and from that infers the network's real **available bandwidth and latency** *right now*, not in theory.
3. **Predict what will actually fit.** Using that live measurement, it calculates how much data the connection can realistically carry, keeping a safety margin so it never over-commits.
4. **Pick the matching video version up front.** It then chooses the exact quality (400 / 800 / 1500 kbps) the network can sustain, *before streaming a single frame*, so the transfer starts already tuned to the connection.

### Why this is so much better

- **No stall-and-recover cycles.** Because it never starts too aggressively, there are far fewer timeouts and retransmissions, so the transfer finishes noticeably faster.
- **Higher real throughput (goodput).** The link stays busy carrying data that *actually arrives* instead of packets that get dropped and resent, so useful throughput goes up.
- **Saves power and CPU.** Fewer retransmissions and no wasted effort pushing a bitrate the link can't handle means less work on both ends, ideal for mobile and battery-powered devices.
- **Built for guaranteed delivery.** It is designed for situations where **getting the content through reliably matters more than maximum resolution**, such as weak, congested, or unstable networks. Quality is treated as the deliberate trade-off, not the goal.
- **Adapts per connection.** Two clients on the same server automatically get different quality tuned to *their own* network conditions.

> **In one line:** *Traditional TCP reacts to congestion after it happens; this system predicts the network up front and adapts before sending anything, trading a sliver of quality for much faster, more reliable, lower-power delivery.*

---

## 1. What is this project? (Explain-Like-I'm-Five)

When you watch a video on YouTube or Netflix, two invisible things are happening behind the scenes:

1. **Congestion control:** Your device and the server constantly negotiate *how fast* to send data so the network doesn't get overwhelmed and drop packets. The classic algorithms that do this are called **TCP Tahoe**, **TCP Reno**, and **TCP NewReno**.
2. **Adaptive bitrate (ABR):** The video is stored in multiple qualities (e.g. 400 kbps, 800 kbps, 1500 kbps). If your internet is slow, the player automatically drops to a lower quality so the video keeps playing without buffering.

This project **rebuilds both of these mechanisms from scratch in pure Python** so you can see exactly how they work, experiment with them, and measure how they perform when the network is bad. Instead of trusting the operating system's built-in networking, everything (windows, retransmissions, quality switching) is implemented by hand in "user space."

**In one sentence:** *It's a mini streaming platform + a networking lab that demonstrates how the internet keeps video smooth over an unreliable connection.*

---

## 2. Why is this interesting? (For recruiters & hiring managers)

This project demonstrates hands-on, systems-level understanding that is often only taught theoretically:

- **Computer networking fundamentals:** Implements sliding-window flow control, congestion windows (`cwnd`), slow-start thresholds (`ssthresh`), timeout-based loss detection, and retransmission, the core of how the internet actually works.
- **Building reliability on top of an unreliable protocol:** UDP has no guarantees; this project layers sequencing, acknowledgements (ACKs), and retransmission on top of it to achieve reliable, in-order delivery, effectively re-implementing the key ideas of TCP.
- **Adaptive systems:** A DASH/HLS-style adaptive bitrate client that estimates available bandwidth and picks the best video quality automatically.
- **Experimental rigor:** A complete benchmarking harness that runs controlled experiments, collects metrics (throughput, transfer time, retransmissions, chosen bitrate), and auto-generates comparison plots.
- **Clean software engineering:** Modular design, dependency injection of the congestion-control strategy, a reusable socket wrapper, type hints, and clear separation between the protocol, the simulator, and the experiments.

**Skills shown:** Python, socket programming, network protocol design, concurrency (threading), binary serialization (`struct`), video encoding (FFmpeg), data visualization (Matplotlib), and the scientific method applied to systems.

---

## 3. How it works (The technical picture)

```
                    ┌─────────────────────────────────────────────┐
                    │                  SERVER                       │
                    │  reads video file → splits into 1 KB chunks   │
                    │  sends chunks within a congestion window      │
                    │  (cwnd controlled by Tahoe / Reno / NewReno)  │
                    └───────────────────────┬──────────────────────┘
                                            │  UDP datagrams
                            ┌───────────────▼───────────────┐
                            │      NetworkSimulator          │
                            │  drops packets (loss)          │
                            │  adds delay + jitter           │
                            │  throttles bandwidth           │
                            └───────────────┬───────────────┘
                                            │
                    ┌───────────────────────▼──────────────────────┐
                    │                  CLIENT                        │
                    │  receives chunks, writes them in order        │
                    │  sends an ACK for every in-order packet       │
                    │  (ABR client also picks the best quality)     │
                    └───────────────────────────────────────────────┘
```

### The transfer protocol (built by hand on UDP)
- The file is split into **1024-byte chunks**, each prefixed with a **4-byte sequence number**.
- The server only keeps `cwnd` (congestion window) packets "in flight" at once.
- The client **ACKs** every in-order packet it receives.
- If an ACK doesn't arrive within `ACK_TIMEOUT` (1 second), the server treats it as **packet loss**, shrinks its window according to the chosen algorithm, and **retransmits**.
- A small handshake sends network parameters and the total file size so the client can self-configure and verify integrity at the end.

### The congestion-control algorithms
| Variant | On ACK (window grows) | On loss/timeout (window shrinks) |
|---|---|---|
| **Tahoe** | Exponential in slow-start, then linear | `ssthresh = cwnd/2`, reset `cwnd` back to **1** (full slow-start restart) |
| **Reno** | Same growth as Tahoe | `ssthresh = cwnd/2`, set `cwnd = ssthresh` (skips slow-start, enters congestion-avoidance) |
| **NewReno** | Same as Reno (placeholder for full fast-recovery) | Same as Reno |

> The variant is injected into the transfer loop as a pluggable class, so adding a new algorithm (e.g. CUBIC or BBR) is as simple as writing a class with `on_ack()` and `on_timeout()` methods.

### Adaptive bitrate (ABR)
The video is pre-encoded into multiple qualities (`400k.mp4`, `800k.mp4`, `1500k.mp4`). The server advertises a **manifest** of available bitrates; the client estimates throughput from the handshake round-trip time and picks the highest quality that fits within **80 % of the estimated bandwidth** (a safety margin to avoid buffering).

### The key idea: probe first, then decide (why this beats plain TCP for delivery)
Unlike a traditional TCP download that just blindly starts blasting data at full quality and only slows down *after* the network is already congested and packets are being lost, this project **measures the network before committing to anything**:

1. The client first sends a tiny **dummy "hello" probe** and records exactly how long it takes to get a response back (the round-trip time).
2. From that round-trip time it **estimates the available bandwidth and latency** of the link.
3. It then **selects the matching version of the video** (400 / 800 / 1500 kbps) that the network can actually handle, instead of over-committing and choking.

Because the sender never wastes effort pushing a bitrate the connection can't sustain, this approach:

- **Saves time:** no long stall-and-recover cycles caused by starting too aggressively.
- **Saves power/CPU:** fewer retransmissions and less wasted encoding/transfer work.
- **Raises effective throughput:** the link stays busy with data that actually gets through, so goodput goes up.
- **Prioritizes guaranteed delivery over raw quality:** it is designed for situations where *getting the message through reliably matters more than maximum resolution* (e.g. weak or congested networks). Quality is deliberately treated as the trade-off, not the goal.

> In short: **traditional TCP reacts to congestion; this system predicts it up front and adapts before sending a single frame of video.**

---

## 4. Project structure

| File | Role |
|---|---|
| **Core protocol** | |
| `network_simulator.py` | Wraps a UDP socket to emulate real-world **packet loss, delay, jitter, and bandwidth caps**. Drop-in replacement for a normal socket. |
| `tcp_tahoe.py` | TCP **Tahoe** congestion-window logic (`on_ack` / `on_timeout`). |
| `tcp_reno.py` | TCP **Reno** congestion-window logic. |
| `tcp_newreno.py` | TCP **NewReno** (extends Reno; placeholder for fast-recovery). |
| `tahoe_server.py` | Sends a single video file over UDP using a chosen congestion-control variant. |
| `tahoe_client.py` | Receives the file, ACKs each packet, verifies integrity. |
| **Adaptive streaming** | |
| `variant_file_server.py` | Multi-quality server: advertises a manifest, streams the quality the client selects. |
| `variant_file_client.py` | Adaptive client: estimates bandwidth and auto-selects the best bitrate. |
| **Tooling & experiments** | |
| `encode_reps.py` | Uses FFmpeg to encode a source video into 400/800/1500 kbps representations. |
| `compare_http_udp.py` | Benchmarks this UDP+Tahoe transfer against a standard HTTP (TCP) download and plots the result. |
| `compare_tcp_variants.py` | Compares Tahoe vs Reno vs NewReno (throughput, time, retransmissions) at a given loss rate and plots it. |
| `loss_adaptation.py` | Shows which bitrate each variant selects at a single loss rate. |
| `unique_delay_adaptation.py` | Tunes per-variant delay to demonstrate distinct quality selections. |
| `multi_loss_collect.py` | Runs transfers across several loss rates and saves the received videos + a summary table. |
| `quality_drop_graph.py` | Plots bitrate selection per variant under no-loss conditions. |
| **Data folders** | |
| `reps/` | Pre-encoded video representations (`400k.mp4`, `800k.mp4`, `1500k.mp4`). |
| `received_videos/` | Videos received during experiments (named by variant + loss rate). |

---

## 5. Getting started

### Prerequisites
- Python 3.8+
- The following Python packages:

```bash
pip install matplotlib requests imageio-ffmpeg
```

> `imageio-ffmpeg` bundles an FFmpeg binary used for encoding, `matplotlib` for the plots, and `requests` for the HTTP-vs-UDP comparison.

### Step 1: Prepare the video qualities
Encode any source video into the three bitrates the project uses:

```bash
python encode_reps.py 101191-video-720.mp4 reps
```

This produces `reps/400k.mp4`, `reps/800k.mp4`, and `reps/1500k.mp4`.

### Step 2: Run a basic transfer (single quality)
Open **two terminals**.

Terminal 1 (server):

```bash
python tahoe_server.py --file reps/1500k.mp4 --variant tahoe --loss 0.002 --delay 0.02 --jitter 0.005 --bandwidth 10
```

Terminal 2 (client):

```bash
python tahoe_client.py --output received.mp4
```

You'll see live progress, the congestion window (`cwnd`) growing and shrinking, throughput, and a final integrity check.

### Step 3: Run adaptive streaming (auto-selects quality)
Terminal 1 (server):

```bash
python variant_file_server.py --dir reps --port 6000 --variant reno --loss 0.003
```

Terminal 2 (client):

```bash
python variant_file_client.py --port 6000 --output adaptive_output.mp4
```

The client will print the manifest, its bandwidth estimate, and the quality it chose.

---

## 6. Reproducing the experiments

Each script starts the server and client for you and saves a plot:

```bash
# Compare this UDP+Tahoe transfer against plain HTTP (TCP)
python compare_http_udp.py --dir reps

# Compare Tahoe vs Reno vs NewReno at a chosen loss rate
python compare_tcp_variants.py --loss 0.002

# Which quality each variant picks at one loss rate
python loss_adaptation.py --dir reps --loss 0.001

# Collect received videos across many loss rates + summary table
python multi_loss_collect.py --dir reps --losses 0.0 0.002 0.004 0.007
```

Generated `.png` plots and received videos are written to the output directories named by each script.

---

## 7. Key parameters you can tune

| Parameter | Meaning | Example |
|---|---|---|
| `--loss` | Packet loss probability (0 to 1) | `0.002` = 0.2 % |
| `--delay` | Base one-way propagation delay (seconds) | `0.02` = 20 ms |
| `--jitter` | Random variation in delay (std-dev, seconds) | `0.005` = 5 ms |
| `--bandwidth` | Link capacity cap in Mbps (`0`/omit = unlimited) | `10` |
| `--variant` | Congestion-control algorithm | `tahoe` / `reno` / `newreno` |

---

## 8. What I learned / highlights

- Reliable, ordered delivery **can be rebuilt on top of unreliable UDP** using sequence numbers, ACKs, and timeouts.
- Congestion-control algorithms have **measurably different behavior** under loss. Tahoe's full window reset is more conservative than Reno's, which shows up clearly in throughput and transfer-time plots.
- Adaptive bitrate selection is fundamentally a **bandwidth-estimation-plus-safety-margin** problem, and small changes to the estimate change the user's perceived quality.
- Designing the congestion controller as a **pluggable strategy object** keeps the transfer loop clean and makes the system easy to extend.

---

## 9. Possible future work

- Implement true **NewReno fast-recovery** (partial-ACK handling) and modern algorithms like **CUBIC** or **BBR**.
- Use **duplicate-ACK-based fast retransmit** instead of relying only on timeouts.
- Smarter ABR (moving-average / buffer-based throughput estimation, like real DASH players).
- Real-time playback + buffering visualization instead of a plain file download.

---

*This is an academic/major project built to explore how modern video streaming stays smooth over imperfect networks by re-implementing the core algorithms from the ground up.*
