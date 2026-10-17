# TCP Congestion Control Improvement (Application-Layer PBL)

This college PBL project studies **TCP-style congestion control implemented at the application layer over UDP**. It includes simplified Tahoe, Reno, and NewReno variants, reliable ordered file transfer, configurable network impairment, adaptive video representation selection, and benchmark tools.

**Scope:** this project does not modify or replace the operating system's native TCP stack and does not introduce a new TCP congestion-control algorithm. Its improvement focus is evaluating and adapting application-layer transmission behavior under changing simulated network conditions.

## What the project measures

The sender uses a congestion window (`cwnd`) and slow-start threshold (`ssthresh`), accepts ACKs, and backs off on timeout. The network simulator can drop datagrams, add delay and jitter, and cap bandwidth. Experiments measure completed-transfer time, goodput, simulated data/ACK drops, and actual retransmitted data segments.

The legacy adaptive mode estimates throughput from the small manifest exchange. The dashboard's measure-first experiment instead transfers a 256 KiB sample from the lowest-quality representation as a probe, measures its delivered goodput over the simulated link, and selects a media quality below 80% of that measurement. This active probe is more representative of the data path, though one probe still does not predict future Internet capacity.

---

## 1. What is this project? (Explain-Like-I'm-Five)

When you watch a video on YouTube or Netflix, two invisible things are happening behind the scenes:

1. **Congestion control:** Your device and the server constantly negotiate *how fast* to send data so the network doesn't get overwhelmed and drop packets. The classic algorithms that do this are called **TCP Tahoe**, **TCP Reno**, and **TCP NewReno**.
2. **Adaptive bitrate (ABR):** The video is stored in multiple qualities (e.g. 400 kbps, 800 kbps, 1500 kbps). If your internet is slow, the player automatically drops to a lower quality so the video keeps playing without buffering.

This project implements simplified versions of both mechanisms in Python at the application layer. Its UDP transfer is separate from, and does not alter, operating-system TCP.

**In one sentence:** *It's a mini streaming platform + a networking lab that demonstrates how the internet keeps video smooth over an unreliable connection.*

---

## 2. Why is this interesting? (For recruiters & hiring managers)

This project demonstrates hands-on, systems-level understanding that is often only taught theoretically:

- **Computer networking fundamentals:** Implements a simplified sliding window, congestion window (`cwnd`), slow-start threshold (`ssthresh`), timeout-based loss detection, and retransmission at the application layer.
- **Building reliability on top of an unreliable protocol:** UDP has no guarantees; this project layers sequencing, acknowledgements (ACKs), and retransmission on top of it to achieve reliable, in-order delivery, effectively re-implementing the key ideas of TCP.
- **Adaptive systems:** A small manifest-RTT heuristic selects a video representation; transfer performance is evaluated separately under simulated conditions.
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
- ACKs carry the next expected sequence number. The client repeats this cumulative ACK when it receives an out-of-order packet.
- If an ACK doesn't arrive within `ACK_TIMEOUT` (1 second), the server treats it as **packet loss**, shrinks its window according to the chosen algorithm, and **retransmits**. NewReno can also retransmit on three duplicate ACKs.
- A small handshake sends network parameters and the total file size so the client can self-configure and verify integrity at the end.

### The congestion-control algorithms
| Variant | On ACK (window grows) | On loss/timeout (window shrinks) |
|---|---|---|
| **Tahoe** | Exponential in slow-start, then linear | `ssthresh = cwnd/2`, reset `cwnd` back to **1** (full slow-start restart) |
| **Reno** | Same growth as Tahoe | `ssthresh = cwnd/2`, set `cwnd = ssthresh` (skips slow-start, enters congestion-avoidance) |
| **NewReno** | Same slow-start / congestion-avoidance growth as Reno; inflates `cwnd` on duplicate ACKs | On three duplicate ACKs, fast retransmit and enter fast recovery; a partial ACK retransmits the next missing segment, while a full ACK exits recovery. A timeout resets `cwnd` to 1. |

> NewReno is an application-layer teaching approximation, not a standards-complete TCP implementation. The simulation uses cumulative ACKs and timeout-based recovery for Tahoe and Reno; NewReno additionally handles duplicate and partial ACKs.

### Adaptive bitrate (ABR)
The video is pre-encoded into multiple qualities (`400k.mp4`, `800k.mp4`, `1500k.mp4`). The server advertises a manifest of available representations.

### Adaptive video quality selection
The legacy client can time the manifest round trip and select the highest bitrate below 80% of that estimate. The dashboard's **Fixed vs measure-first** experiment uses a more direct probe: it downloads a 256 KiB sample from the lowest representation, measures delivered goodput, and selects the highest available bitrate below 80% of the probe rate (or the lowest available quality when none fit).

1. The server advertises its available representations in a manifest.
2. Measure-first downloads a 256 KiB low-quality video sample over the same impaired link and measures its delivered goodput.
3. It selects a representation using an 80% safety margin. The comparison includes probe bytes and time in the adaptive end-to-end result; the fixed-quality baseline starts directly with the 1500 kbps representation.

The project does not assume this heuristic improves performance; compare actual experiment measurements:

- Transfer time can rise when loss triggers timeout-based recovery.
- Power use and CPU savings are not measured by this project.
- Throughput is computed from completed transfers; its direction and magnitude depend on simulated conditions.
- The transfer retries lost segments, but this does not guarantee delivery under every failure condition.

> In short: **this project evaluates application-layer TCP-style transmission over UDP; it does not modify native TCP or claim to introduce a new TCP algorithm.**

---

## 4. Project structure

| File | Role |
|---|---|
| **Core protocol** | |
| `network_simulator.py` | Wraps a UDP socket to emulate real-world **packet loss, delay, jitter, and bandwidth caps**. Drop-in replacement for a normal socket. |
| `tcp_tahoe.py` | TCP **Tahoe** congestion-window logic (`on_ack` / `on_timeout`). |
| `tcp_reno.py` | TCP **Reno** congestion-window logic. |
| `tcp_newreno.py` | TCP **NewReno** fast retransmit and fast recovery, including partial ACK handling. |
| `tahoe_server.py` | Sends a single video file over UDP using a chosen congestion-control variant. |
| `tahoe_client.py` | Receives the file, ACKs each packet, verifies integrity. |
| **Adaptive streaming** | |
| `variant_file_server.py` | Multi-quality server: advertises a manifest, streams the quality the client selects. |
| `variant_file_client.py` | Adaptive client: estimates bandwidth and auto-selects the best bitrate. |
| **Tooling & experiments** | |
| `encode_reps.py` | Uses FFmpeg to encode a source video into 400/800/1500 kbps representations. |
| `compare_http_udp.py` | Benchmarks this UDP+Tahoe transfer against a standard HTTP (TCP) download and plots the result. |
| `compare_tcp_variants.py` | Compares Tahoe vs Reno vs NewReno (throughput, time, retransmissions) at a given loss rate and plots it. |
| `streamlit_app.py` | Streamlit dashboard with a playable NewReno video-transfer demo and the benchmark comparisons. |
| `loss_stress_test.py` | Runs repeated comparisons across loss rates and saves measured CSV results, congestion traces, and three charts. |
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
- Python 3.8+ for the command-line scripts; use a Python version supported by Streamlit for the GUI.
- The following Python packages:

```bash
pip install matplotlib requests imageio-ffmpeg streamlit
```

> `imageio-ffmpeg` bundles an FFmpeg binary used for encoding, `matplotlib` for the plots, `requests` for the HTTP-vs-UDP comparison, and `streamlit` for the GUI.

### Open the GUI
After preparing the video representations, start the dashboard from the project folder:

```bash
streamlit run streamlit_app.py
```

The dashboard opens in your browser with a **NewReno video demo**, a **Fixed vs measure-first** comparison, a TCP variant comparison, and a **Loss Stress Test** tab. The paired video comparison measures a probe before choosing quality, and reports selected quality, probe goodput and overhead, media transfer goodput, end-to-end delivery time, retransmissions, and packet drops. Both strategies use NewReno and the same simulated link settings. The fixed baseline immediately requests 1500 kbps; adaptive includes the probe in total delivery time. The loss stress test uses the 400 kbps representation; choose loss rates, variants, and trial count in the UI.

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

# Compare all variants at 0%, 0.2%, 0.4%, and 0.7% loss with three trials per condition
python loss_stress_test.py --losses 0 0.002 0.004 0.007 --trials 3

# Which quality each variant picks at one loss rate
python loss_adaptation.py --dir reps --loss 0.001

# Collect received videos across many loss rates + summary table
python multi_loss_collect.py --dir reps --losses 0.0 0.002 0.004 0.007
```

The stress test writes trial-level and mean CSV files, congestion event traces, and throughput, transfer-time, and retransmission charts under `variant_compare_outputs/loss_stress/`.

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

- Reliable, ordered delivery can be built on UDP using sequence numbers, ACKs, duplicate-ACK handling, and timeouts.
- Tahoe resets `cwnd` to one after timeout; Reno sets it to `ssthresh`. NewReno adds duplicate-ACK fast retransmit and partial-ACK recovery.
- The multi-loss experiment uses repeated trials with initialized random seeds and reports measured outcomes. OS scheduling can still affect observed timing and retry counts, so interpret the trial means rather than a single run.
- Adaptive bitrate selection is a simple bandwidth-estimate-plus-safety-margin heuristic.
- Designing the congestion controller as a **pluggable strategy object** keeps the transfer loop clean and makes the system easy to extend.

---

## 9. Possible future work

- Compare NewReno with modern algorithms like **CUBIC** or **BBR** under network conditions suited to each algorithm.
- Model a wider range of TCP details, such as RTT-based retransmission timers and duplicate-ACK behavior in the Reno variant.
- Smarter ABR (moving-average / buffer-based throughput estimation, like real DASH players).
- Real-time playback + buffering visualization instead of a plain file download.

---

*This is an academic project for measuring simplified application-layer TCP-style transmission over impaired UDP; it is not a native TCP implementation.*
