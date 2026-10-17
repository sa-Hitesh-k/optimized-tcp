from __future__ import annotations

import csv
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
SOURCE_VIDEO = ROOT / "reps" / "400k.mp4"
DEMO_VIDEO = ROOT / "reps" / "1500k.mp4"
OUTPUT_DIR = ROOT / "variant_compare_outputs" / "streamlit"
PLOT_PATH = OUTPUT_DIR / "tcp_variants_metrics.png"
STRESS_OUTPUT_DIR = ROOT / "variant_compare_outputs" / "loss_stress" / "streamlit"
STRESS_VIDEO = ROOT / "reps" / "400k.mp4"
STRESS_VARIANTS = ("tahoe", "reno", "newreno")
DEMO_TEMP_DIR = Path(tempfile.gettempdir()) / "optimized-tcp-video-demo"

st.set_page_config(
    page_title="TCP Congestion Control Improvement | Network Lab",
    page_icon="📡",
    layout="wide",
)

st.title("TCP Congestion Control Improvement")
st.caption("Simulation of TCP-style congestion control over an impaired UDP network.")

overview, video_demo, strategy_comparison, experiment, stress_test = st.tabs(
    [
        "Overview",
        "NewReno video demo",
        "Fixed vs measure-first",
        "TCP variant comparison",
        "Loss Stress Test",
    ]
)

with overview:
    left, right = st.columns(2)
    with left:
        st.subheader("What this project demonstrates")
        st.markdown(
            """
            - Application-layer TCP-style Tahoe, Reno, and NewReno variants over UDP
            - Reliable, sequenced file transfer with cumulative ACKs, fast recovery, and timeout retransmission
            - Simulated packet loss, delay, jitter, and bandwidth limits
            - Adaptive video quality selection and measured benchmark comparisons
            """
        )
    with right:
        st.subheader("Benchmark input")
        if SOURCE_VIDEO.is_file():
            st.success(f"Ready: `{SOURCE_VIDEO.relative_to(ROOT)}`")
            st.caption(f"File size: {SOURCE_VIDEO.stat().st_size / 1_000_000:.2f} MB")
        else:
            st.warning("The benchmark video is not prepared yet.")
            st.markdown(
                "Create the video representations first with "
                "`python encode_reps.py 101191-video-720.mp4 reps`."
            )

    st.info(
        "This is an application-layer simulation over UDP, not a modification to "
        "the operating system's native TCP stack. NewReno uses duplicate ACKs "
        "for fast retransmit and partial-ACK recovery; Tahoe and Reno use "
        "simplified timeout-based loss handling."
    )

def _available_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind(("127.0.0.1", 0))
        return udp_socket.getsockname()[1]


def _start_video_transfer(loss_rate: float) -> dict:
    if not DEMO_VIDEO.is_file():
        raise FileNotFoundError(f"Demo video does not exist: {DEMO_VIDEO}")

    run_dir = DEMO_TEMP_DIR / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    port = _available_udp_port()
    seed = int(time.time_ns() % 2_000_000_000)
    output_path = run_dir / "received_video.mp4"
    server_log = run_dir / "server.log"
    client_log = run_dir / "client.log"
    server_command = [
        sys.executable,
        "-u",
        str(ROOT / "tahoe_server.py"),
        "--file",
        str(DEMO_VIDEO),
        "--port",
        str(port),
        "--variant",
        "newreno",
        "--loss",
        str(loss_rate),
        "--delay",
        "0.0005",
        "--jitter",
        "0.0001",
        "--bandwidth",
        "10",
        "--seed",
        str(seed),
    ]

    with server_log.open("w", encoding="utf-8") as log_file:
        server_process = subprocess.Popen(
            server_command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

    try:
        startup_deadline = time.monotonic() + 10
        while time.monotonic() < startup_deadline:
            if server_process.poll() is not None:
                raise RuntimeError(
                    "Video sender exited before it was ready:\n"
                    + server_log.read_text(encoding="utf-8", errors="replace")
                )
            if "[+] Listening on" in server_log.read_text(
                encoding="utf-8", errors="replace"
            ):
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("Timed out while starting the video sender.")

        client_command = [
            sys.executable,
            "-u",
            str(ROOT / "tahoe_client.py"),
            "--port",
            str(port),
            "--output",
            str(output_path),
            "--seed",
            str(seed + 1),
        ]
        with client_log.open("w", encoding="utf-8") as log_file:
            client_process = subprocess.Popen(
                client_command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
    except (OSError, RuntimeError, TimeoutError):
        if server_process.poll() is None:
            server_process.terminate()
            server_process.wait(timeout=5)
        raise

    return {
        "server": server_process,
        "client": client_process,
        "server_log": str(server_log),
        "client_log": str(client_log),
        "output": str(output_path),
        "source": str(DEMO_VIDEO),
        "loss": loss_rate,
        "started_at": time.monotonic(),
        "status": "running",
    }


def _metric_value(lines: list[str], name: str) -> float:
    prefix = f"[METRIC] {name}="
    for line in lines:
        if line.startswith(prefix):
            return float(line[len(prefix) :])
    return 0.0


def _finish_video_transfer(run: dict) -> None:
    client_process = run["client"]
    server_process = run["server"]
    if client_process.returncode == 0:
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.terminate()
            server_process.wait(timeout=5)
    elif server_process.poll() is None:
        server_process.terminate()
        server_process.wait(timeout=5)

    client_log = Path(run["client_log"]).read_text(
        encoding="utf-8", errors="replace"
    )
    server_log = Path(run["server_log"]).read_text(
        encoding="utf-8", errors="replace"
    )
    if client_process.returncode != 0:
        run["status"] = "failed"
        run["error"] = (
            f"Video receiver exited with code {client_process.returncode}."
        )
        return

    output_path = Path(run["output"])
    source_size = Path(run["source"]).stat().st_size
    if not output_path.is_file() or output_path.stat().st_size != source_size:
        run["status"] = "failed"
        run["error"] = "Received video size does not match the source video."
        return
    if "[+] Integrity check passed" not in client_log:
        run["status"] = "failed"
        run["error"] = "The receiver did not confirm the file integrity check."
        return

    elapsed = time.monotonic() - run["started_at"]
    trace_events = re.findall(r"event=([A-Z_]+)", server_log)
    run["metrics"] = {
        "transfer_time": elapsed,
        "throughput": source_size * 8 / elapsed / 1_000_000,
        "retransmissions": trace_events.count("RETRANSMIT"),
        "fast_retransmits": trace_events.count("FAST_RETRANSMIT"),
        "partial_acks": trace_events.count("PARTIAL_ACK"),
        "timeouts": trace_events.count("TIMEOUT"),
        "data_drops": _metric_value(server_log.splitlines(), "data_drops"),
        "ack_drops": _metric_value(client_log.splitlines(), "ack_drops"),
    }
    run["status"] = "complete"


@st.fragment(run_every=1)
def _render_video_transfer():
    run = st.session_state.get("video_transfer")
    if not run:
        st.info("Start a transfer to receive and play the video here.")
        return

    client_process = run["client"]
    if run["status"] == "running" and client_process.poll() is None:
        if time.monotonic() - run["started_at"] > 120:
            client_process.terminate()
            client_process.wait(timeout=5)
            if run["server"].poll() is None:
                run["server"].terminate()
                run["server"].wait(timeout=5)
            run["status"] = "failed"
            run["error"] = "Video transfer exceeded the 120-second time limit."
            st.error(run["error"])
            return

        client_log = Path(run["client_log"]).read_text(
            encoding="utf-8", errors="replace"
        )
        progress_matches = re.findall(r"Progress:\s*([\d.]+)%", client_log)
        progress = min(float(progress_matches[-1]), 100.0) if progress_matches else 0.0
        st.progress(progress / 100, text=f"Receiving video: {progress:.1f}%")
        st.caption(
            "Algorithm: NewReno · "
            f"Simulated loss: {run['loss'] * 100:.2f}% · "
            "One-way delay: 0.5 ms · Link cap: 10 Mbps"
        )
        return

    if run["status"] == "running":
        _finish_video_transfer(run)

    if run["status"] == "failed":
        st.error(run["error"])
        for log_name in ("client_log", "server_log"):
            log_text = Path(run[log_name]).read_text(
                encoding="utf-8", errors="replace"
            )
            if log_text:
                with st.expander(f"{log_name.replace('_', ' ').title()}"):
                    st.code(log_text[-6000:], language="text")
        return

    if run["status"] == "complete":
        st.success("Video transfer completed and the received file passed its size check.")
        metrics = run["metrics"]
        columns = st.columns(4)
        columns[0].metric("Transfer time", f"{metrics['transfer_time']:.2f} s")
        columns[1].metric("Goodput", f"{metrics['throughput']:.2f} Mbps")
        columns[2].metric("Data retransmissions", metrics["retransmissions"])
        columns[3].metric("Simulated packet drops", int(metrics["data_drops"] + metrics["ack_drops"]))
        recovery_columns = st.columns(3)
        recovery_columns[0].metric("Fast retransmits", metrics["fast_retransmits"])
        recovery_columns[1].metric("Partial-ACK recoveries", metrics["partial_acks"])
        recovery_columns[2].metric("Timeouts", metrics["timeouts"])

        video_bytes = Path(run["output"]).read_bytes()
        st.video(video_bytes, format="video/mp4")
        st.download_button(
            "Download received video",
            data=video_bytes,
            file_name="newreno_received_video.mp4",
            mime="video/mp4",
        )
        with st.expander("What NewReno did during this transfer"):
            st.write(
                "The sender limited in-flight packets using its congestion window. "
                "When it received three duplicate cumulative ACKs, it retransmitted "
                "the missing segment without waiting for the timeout. A partial ACK "
                "during fast recovery prompted retransmission of the next missing "
                "segment. This run reports the events that actually occurred; "
                "zero fast retransmits means no loss pattern triggered that behavior."
            )


with video_demo:
    st.subheader("Transfer a video with NewReno")
    st.write(
        "Send the encoded 1500 kbps video through the project’s UDP sender and "
        "receiver, then play the received copy. NewReno is selected in the backend."
    )
    if DEMO_VIDEO.is_file():
        st.caption(
            f"Source: `{DEMO_VIDEO.relative_to(ROOT)}` · "
            f"{DEMO_VIDEO.stat().st_size / 1_000_000:.2f} MB"
        )
    else:
        st.error(
            "The demo video is missing. Create representations with "
            "`python encode_reps.py 101191-video-720.mp4 reps`."
        )

    demo_loss = st.slider(
        "Simulated packet loss (%)",
        min_value=0.0,
        max_value=2.0,
        value=0.2,
        step=0.1,
        key="video_demo_loss",
        help="Loss is simulated in both directions. A nonzero rate may trigger "
        "NewReno's fast-recovery behavior.",
    )
    active_demo = st.session_state.get("video_transfer")
    demo_is_running = bool(
        active_demo
        and active_demo["status"] == "running"
        and active_demo["client"].poll() is None
    )
    start_demo = st.button(
        "Transfer video",
        type="primary",
        disabled=not DEMO_VIDEO.is_file() or demo_is_running,
    )
    if start_demo:
        try:
            st.session_state["video_transfer"] = _start_video_transfer(
                demo_loss / 100
            )
        except (OSError, RuntimeError, TimeoutError) as error:
            st.error(f"Could not start the video transfer: {error}")
    _render_video_transfer()


@st.fragment(run_every=1)
def _render_strategy_comparison():
    run = st.session_state.get("video_strategy_comparison")
    if not run:
        st.info("Run the paired test to compare fixed-quality and measure-first delivery.")
        return

    process = run["process"]
    log_path = Path(run["log_path"])
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    if process.poll() is None:
        progress = re.findall(r"Completed (\d+)/(\d+):", log_text)
        if progress:
            completed, total = map(int, progress[-1])
            st.progress(completed / total, text=f"Completed {completed} of {total} transfers")
        else:
            st.info("Starting the paired video-transfer experiment...")
        if log_text:
            st.code(log_text[-2000:], language="text")
        return

    if process.returncode != 0:
        st.error(f"Video comparison failed (exit code {process.returncode}).")
        st.code(log_text[-8000:] or "(No process output)", language="text")
        return

    summary_lines = re.findall(r"^SUMMARY_JSON (.+)$", log_text, re.MULTILINE)
    if not summary_lines:
        st.error("The comparison finished without producing its summary.")
        st.code(log_text[-8000:] or "(No process output)", language="text")
        return

    summary = json.loads(summary_lines[-1])
    traditional = summary["traditional"]
    adaptive = summary["adaptive"]
    st.success(
        f"Completed {int(traditional['trials'])} paired trial(s) per strategy. "
        "Both used the same NewReno sender and network profile."
    )
    quality_change = (
        adaptive["selected_bitrate_kbps"] / traditional["selected_bitrate_kbps"] - 1
    ) * 100
    time_change = (
        (adaptive["total_time_s"] / traditional["total_time_s"] - 1) * 100
        if traditional["total_time_s"]
        else None
    )
    payload_change = (
        adaptive["total_bytes_sent"] / traditional["total_bytes_sent"] - 1
    ) * 100
    retransmission_change = (
        (adaptive["retransmissions"] / traditional["retransmissions"] - 1) * 100
        if traditional["retransmissions"]
        else None
    )
    metrics = st.columns(4)
    metrics[0].metric(
        "End-to-end time",
        f"{adaptive['total_time_s']:.2f} s",
        delta=f"{time_change:+.1f}% vs fixed" if time_change is not None else None,
        delta_color="inverse",
    )
    metrics[1].metric(
        "Selected video quality",
        f"{adaptive['selected_bitrate_kbps']:.0f} kbps",
        delta=f"{quality_change:+.1f}% vs fixed",
        delta_color="off",
    )
    metrics[2].metric(
        "Total payload incl. probe",
        f"{adaptive['total_bytes_sent'] / 1_000_000:.2f} MB",
        delta=f"{payload_change:+.1f}% vs fixed",
        delta_color="inverse",
    )
    metrics[3].metric(
        "Data retransmissions",
        f"{adaptive['retransmissions']:.0f}",
        delta=(
            f"{retransmission_change:+.1f}% vs fixed"
            if retransmission_change is not None
            else "fixed baseline had none"
        ),
        delta_color="inverse",
    )

    comparison_rows = [
        {
            "Delivery strategy": "Traditional fixed quality",
            "Video quality (kbps)": f"{traditional['selected_bitrate_kbps']:.0f}",
            "Probe goodput (kbps)": "—",
            "Probe overhead (MB)": f"{traditional['probe_bytes'] / 1_000_000:.2f}",
            "Video data delivered (MB)": f"{traditional['media_bytes'] / 1_000_000:.2f}",
            "Total time incl. measurement (s)": f"{traditional['total_time_s']:.2f}",
            "Media transfer goodput (Mbps)": f"{traditional['media_transfer_goodput_mbps']:.2f}",
            "End-to-end media rate (Mbps)": f"{traditional['end_to_end_media_rate_mbps']:.2f}",
            "Retransmissions": f"{traditional['retransmissions']:.0f}",
            "Packet drops": f"{traditional['data_drops'] + traditional['ack_drops']:.0f}",
        },
        {
            "Delivery strategy": "Measure-first adaptive",
            "Video quality (kbps)": (
                f"{adaptive['selected_bitrate_kbps']:.0f} "
                f"(trials: {', '.join(str(rate) for rate in adaptive['selected_bitrates_kbps'])})"
            ),
            "Probe goodput (kbps)": (
                f"{adaptive['measured_probe_goodput_kbps']:.0f}"
                if adaptive["measured_probe_goodput_kbps"] is not None
                else "—"
            ),
            "Probe overhead (MB)": f"{adaptive['probe_bytes'] / 1_000_000:.2f}",
            "Video data delivered (MB)": f"{adaptive['media_bytes'] / 1_000_000:.2f}",
            "Total time incl. measurement (s)": f"{adaptive['total_time_s']:.2f}",
            "Media transfer goodput (Mbps)": f"{adaptive['media_transfer_goodput_mbps']:.2f}",
            "End-to-end media rate (Mbps)": f"{adaptive['end_to_end_media_rate_mbps']:.2f}",
            "Retransmissions": f"{adaptive['retransmissions']:.0f}",
            "Packet drops": f"{adaptive['data_drops'] + adaptive['ack_drops']:.0f}",
        },
    ]
    st.subheader("Measured results")
    st.dataframe(comparison_rows, use_container_width=True, hide_index=True)

    time_change = summary["transfer_time_change_percent"]
    adaptive_time = adaptive["total_time_s"]
    fixed_time = traditional["total_time_s"]
    if time_change is not None:
        time_direction = "less" if time_change < 0 else "more" if time_change > 0 else "the same"
        st.markdown(
            f"**End-to-end result:** measure-first took **{abs(time_change):.1f}% "
            f"{time_direction} time** than fixed quality "
            f"({adaptive_time:.2f} s vs {fixed_time:.2f} s). "
            f"It spent {adaptive['probe_time_s']:.2f} s measuring before selecting "
            f"an average {adaptive['selected_bitrate_kbps']:.0f} kbps representation."
        )
    st.caption(
        "End-to-end time includes the probe for adaptive delivery; fixed-quality "
        "starts with the 1500 kbps representation and has no probe. Media transfer "
        "goodput excludes the probe; end-to-end media rate includes its time cost. "
        "A lower quality can finish sooner, but is not automatically a quality win."
    )

    left, right = st.columns(2)
    fixed_path = Path(traditional["last_output"])
    adaptive_path = Path(adaptive["last_output"])
    for column, label, video_path in (
        (left, "Traditional: fixed 1500 kbps video", fixed_path),
        (
            right,
            f"Measure-first: {int(adaptive['selected_bitrates_kbps'][-1])} kbps video",
            adaptive_path,
        ),
    ):
        column.subheader(label)
        if video_path.is_file():
            column.video(video_path.read_bytes(), format="video/mp4")
            column.download_button(
                f"Download {label.split(':')[0].lower()} copy",
                data=video_path.read_bytes(),
                file_name=video_path.name,
                mime="video/mp4",
                key=f"download_{video_path.stem}",
            )
        else:
            column.warning("Received video file is no longer available.")

    with st.expander("NewReno recovery events"):
        st.write(
            f"Measure-first runs averaged {adaptive['fast_retransmits']:.0f} fast "
            f"retransmits, {adaptive['partial_acks']:.0f} partial-ACK recoveries, "
            f"and {adaptive['timeouts']:.0f} timeouts per trial. The fixed-quality "
            "runs use the same NewReno congestion controller; the comparison is "
            "about quality selection and probe overhead, not different algorithms."
        )


with strategy_comparison:
    st.subheader("Fixed quality vs measure first")
    st.write(
        "Traditional delivery immediately requests the highest-quality video. "
        "Measure-first sends a 256 KiB low-quality video sample over the impaired link, "
        "measures delivered goodput, then chooses a representation with a safety margin."
    )
    control_left, control_right = st.columns(2)
    with control_left:
        comparison_loss = st.slider(
            "Simulated packet loss (%)",
            min_value=0.0,
            max_value=2.0,
            value=0.2,
            step=0.1,
            key="strategy_loss",
        )
        comparison_bandwidth = st.select_slider(
            "Simulated link capacity",
            options=[0.5, 1.0, 2.0, 5.0, 10.0],
            value=1.0,
            format_func=lambda rate: f"{rate:g} Mbps",
            key="strategy_bandwidth",
        )
    with control_right:
        comparison_trials = st.number_input(
            "Paired trials per strategy",
            min_value=1,
            max_value=5,
            value=1,
            step=1,
            key="strategy_trials",
            help="Use 3 or more trials for a more reliable average.",
        )
        st.caption(
            "Both strategies use NewReno, the same link capacity, loss, delay, "
            "jitter, and paired random seeds."
        )

    active_comparison = st.session_state.get("video_strategy_comparison")
    comparison_running = bool(
        active_comparison and active_comparison["process"].poll() is None
    )
    start_comparison = st.button(
        "Run fixed vs adaptive comparison",
        type="primary",
        disabled=comparison_running or not DEMO_VIDEO.is_file(),
    )
    if start_comparison:
        comparison_dir = DEMO_TEMP_DIR / f"comparison-{uuid.uuid4().hex}"
        comparison_dir.mkdir(parents=True)
        log_path = comparison_dir / "comparison.log"
        command = [
            sys.executable,
            "-u",
            str(ROOT / "video_strategy_comparison.py"),
            "--loss",
            str(comparison_loss / 100),
            "--trials",
            str(comparison_trials),
            "--delay",
            "0.0005",
            "--jitter",
            "0.0001",
            "--bandwidth",
            str(comparison_bandwidth),
            "--out-dir",
            str(comparison_dir),
        ]
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            st.session_state["video_strategy_comparison"] = {
                "process": process,
                "output_dir": str(comparison_dir),
                "log_path": str(log_path),
            }
        except OSError as error:
            st.error(f"Could not start the paired comparison: {error}")
    _render_strategy_comparison()

with experiment:
    st.subheader("Compare Tahoe, Reno, and NewReno")
    st.write(
        "Run the existing transfer benchmark and compare its throughput, "
        "completion time, and retransmission counts."
    )

    loss_rate = st.slider(
        "Packet loss probability",
        min_value=0.0,
        max_value=0.01,
        value=0.0002,
        step=0.0001,
        format="%.4f",
        help="For example, 0.0020 means a 0.2% packet loss probability.",
    )
    st.caption(f"Equivalent packet loss: {loss_rate * 100:.2f}%")

    if not SOURCE_VIDEO.is_file():
        st.error(
            "Cannot run the benchmark because `reps/400k.mp4` is missing. "
            "Encode the representations using the command shown on the Overview tab."
        )

    run_clicked = st.button(
        "Run comparison",
        type="primary",
        disabled=not SOURCE_VIDEO.is_file(),
    )

    if run_clicked:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            str(ROOT / "compare_tcp_variants.py"),
            "--loss",
            str(loss_rate),
            "--out-dir",
            str(OUTPUT_DIR),
            "--file",
            str(SOURCE_VIDEO),
            "--delay",
            "0.002",
            "--jitter",
            "0.001",
        ]
        with st.spinner("Running quick comparison..."):
            result = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        st.session_state["tcp_comparison"] = {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    result = st.session_state.get("tcp_comparison")
    if result:
        if result["returncode"] == 0:
            st.success("Comparison completed.")
            rows = []
            for match in re.finditer(
                r"Time:\s*([\d.]+)\s*s\s+Throughput:\s*([\d.]+)\s*Mbps\s+Retrans:\s*(\d+)",
                result["stdout"],
            ):
                rows.append(
                    {
                        "Variant": ("Tahoe", "Reno", "NewReno")[len(rows)],
                        "Transfer time (s)": float(match.group(1)),
                        "Throughput (Mbps)": float(match.group(2)),
                        "Retransmissions": int(match.group(3)),
                    }
                )
            if rows:
                st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            st.error(f"Benchmark failed with exit code {result['returncode']}.")

        with st.expander("Benchmark output", expanded=result["returncode"] != 0):
            st.code(result["stdout"] or "(No standard output)", language="text")
            if result["stderr"]:
                st.code(result["stderr"], language="text")

        if PLOT_PATH.is_file():
            st.subheader("Benchmark chart")
            st.image(str(PLOT_PATH), use_container_width=True)


def _read_csv(path: Path):
    with path.open(newline="", encoding="utf-8") as csv_file:
        return list(csv.DictReader(csv_file))


def _render_stress_summary(rows):
    if not rows:
        return

    best_throughput = max(rows, key=lambda row: float(row["Throughput (Mbps)"]))
    fastest = min(rows, key=lambda row: float(row["Transfer time (s)"]))
    most_retransmissions = max(rows, key=lambda row: float(row["Retransmissions"]))
    st.subheader("Experiment summary")
    st.markdown(
        f"""
        - **Best measured throughput:** {best_throughput['Algorithm']} at
          {float(best_throughput['Loss %']):.2f}% loss
          ({float(best_throughput['Throughput (Mbps)']):.2f} Mbps).
        - **Lowest measured transfer time:** {fastest['Algorithm']} at
          {float(fastest['Loss %']):.2f}% loss
          ({float(fastest['Transfer time (s)']):.2f} s).
        - **Highest mean retransmissions:** {most_retransmissions['Algorithm']} at
          {float(most_retransmissions['Loss %']):.2f}% loss
          ({float(most_retransmissions['Retransmissions']):.2f}).
        """
    )

    by_loss = {}
    for row in rows:
        by_loss.setdefault(float(row["Loss %"]), []).append(row)
    first_loss, last_loss = min(by_loss), max(by_loss)
    first_throughput = sum(
        float(row["Throughput (Mbps)"]) for row in by_loss[first_loss]
    ) / len(by_loss[first_loss])
    last_throughput = sum(
        float(row["Throughput (Mbps)"]) for row in by_loss[last_loss]
    ) / len(by_loss[last_loss])
    if last_loss != first_loss:
        change = last_throughput - first_throughput
        direction = "increased" if change > 0 else "decreased" if change < 0 else "did not change"
        st.write(
            f"Across the selected algorithms, mean measured throughput {direction} "
            f"from {first_throughput:.2f} Mbps at {first_loss:.2f}% loss to "
            f"{last_throughput:.2f} Mbps at {last_loss:.2f}% loss."
        )
    for loss in sorted(by_loss):
        winner = max(by_loss[loss], key=lambda row: float(row["Throughput (Mbps)"]))
        st.write(
            f"At {loss:.2f}% loss, {winner['Algorithm']} had the highest measured "
            f"mean throughput ({float(winner['Throughput (Mbps)']):.2f} Mbps)."
        )

    newreno_vs_reno = []
    for loss in sorted(by_loss):
        by_algorithm = {row["Algorithm"].upper(): row for row in by_loss[loss]}
        if "NEWRENO" not in by_algorithm or "RENO" not in by_algorithm:
            continue

        newreno = by_algorithm["NEWRENO"]
        reno = by_algorithm["RENO"]

        def relative_change(metric):
            baseline = float(reno[metric])
            if baseline == 0:
                return None
            return (float(newreno[metric]) - baseline) / baseline * 100

        newreno_vs_reno.append(
            {
                "Loss %": loss,
                "Trials": min(int(newreno["Trials"]), int(reno["Trials"])),
                "Transfer time vs Reno (%)": relative_change("Transfer time (s)"),
                "Throughput vs Reno (%)": relative_change("Throughput (Mbps)"),
                "Retransmissions vs Reno (%)": relative_change("Retransmissions"),
            }
        )

    if newreno_vs_reno:
        st.subheader("NewReno vs Reno")
        st.caption(
            "Relative change = (NewReno - Reno) / Reno. Lower transfer time and "
            "retransmissions, or higher throughput, favor NewReno. A blank value "
            "means Reno's baseline was zero."
        )
        st.dataframe(newreno_vs_reno, use_container_width=True, hide_index=True)


@st.fragment(run_every=1)
def _render_stress_run():
    run = st.session_state.get("loss_stress_run")
    if not run:
        return

    process = run["process"]
    if process.poll() is None:
        log_text = Path(run["log_path"]).read_text(encoding="utf-8", errors="replace")
        progress_matches = re.findall(r"Completed (\d+)/(\d+):", log_text)
        if progress_matches:
            completed, total = map(int, progress_matches[-1])
            st.progress(completed / total, text=f"Completed {completed} of {total} transfers")
        else:
            st.info("Starting loss-stress experiment...")
        if log_text:
            st.code(log_text[-2500:], language="text")
        return

    if process.returncode != 0:
        st.error(f"Loss-stress experiment failed (exit code {process.returncode}).")
        log_text = Path(run["log_path"]).read_text(encoding="utf-8", errors="replace")
        st.code(log_text[-8000:] or "(No process output)", language="text")
        return

    summary_path = Path(run["output_dir"]) / "loss_stress_summary.csv"
    trace_path = Path(run["output_dir"]) / "congestion_trace.csv"
    if not summary_path.is_file():
        st.error("The experiment completed but did not produce its summary CSV.")
        return

    rows = _read_csv(summary_path)
    numeric_columns = (
        "Loss %",
        "Transfer time (s)",
        "Throughput (Mbps)",
        "Retransmissions",
        "Data drops",
        "ACK drops",
    )
    for row in rows:
        for column in numeric_columns:
            row[column] = float(row[column])
        row["Trials"] = int(row["Trials"])
    st.success("Loss-stress experiment completed.")
    st.subheader("Mean results across trials")
    st.dataframe(rows, use_container_width=True, hide_index=True)
    _render_stress_summary(rows)

    chart_columns = st.columns(2)
    chart_names = (
        ("Throughput (Mbps)", "throughput_vs_loss.png"),
        ("Transfer time (s)", "transfer_time_vs_loss.png"),
        ("Retransmissions", "retransmissions_vs_loss.png"),
    )
    for index, (caption, filename) in enumerate(chart_names):
        chart_path = Path(run["output_dir"]) / filename
        if chart_path.is_file():
            column = chart_columns[index % len(chart_columns)]
            column.image(str(chart_path), caption=caption, use_container_width=True)

    if trace_path.is_file():
        traces = _read_csv(trace_path)
        for trace in traces:
            trace["loss_percent"] = float(trace["loss_percent"])
            trace["trial"] = int(trace["trial"])
            trace["time_s"] = float(trace["time_s"])
            if trace["seq"]:
                trace["seq"] = int(trace["seq"])
        with st.expander("Congestion-control events (actual simulation trace)"):
            if traces:
                st.caption(
                    "Timeouts, fast retransmits, partial-ACK recovery, and retransmitted "
                    "segments with recorded cwnd/ssthresh."
                )
                st.dataframe(traces, use_container_width=True, hide_index=True)
            else:
                st.info("No timeout or retransmission events were observed in these trials.")
    st.caption(
        "Retransmissions count data segments resent after fast recovery or timeout. "
        "Drop counters separately report simulated data and ACK datagrams dropped."
    )


with stress_test:
    st.subheader("Loss Stress Test")
    st.write(
        "Run repeated Tahoe, Reno, and/or NewReno transfers at selected loss rates. "
        "The table and charts use measured trial results; no values are prefilled."
    )
    loss_choices = st.multiselect(
        "Packet-loss conditions",
        options=[0.0, 0.002, 0.004, 0.007],
        default=[0.0, 0.002, 0.004, 0.007],
        format_func=lambda loss: f"{loss * 100:g}%",
    )
    variant_choices = st.multiselect(
        "Algorithms",
        options=list(STRESS_VARIANTS),
        default=list(STRESS_VARIANTS),
        format_func=str.upper,
    )
    trials = st.number_input(
        "Trials per algorithm and loss condition",
        min_value=1,
        max_value=5,
        value=3,
        step=1,
    )
    st.caption(
        "Uses the existing 400 kbps video, 2 ms delay, and 1 ms jitter. "
        "Three trials are run by default for each selected condition. Fixed RNG "
        "seeds support comparison, but OS scheduling can still affect exact timings."
    )
    active_run = st.session_state.get("loss_stress_run")
    is_running = bool(active_run and active_run["process"].poll() is None)
    run_stress_clicked = st.button(
        "Run loss stress test",
        type="primary",
        disabled=is_running or not loss_choices or not variant_choices or not STRESS_VIDEO.is_file(),
    )
    if not STRESS_VIDEO.is_file():
        st.error(f"Missing stress-test input video: `{STRESS_VIDEO.relative_to(ROOT)}`")
    if run_stress_clicked:
        STRESS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log_path = STRESS_OUTPUT_DIR / "experiment.log"
        command = [
            sys.executable,
            "-u",
            str(ROOT / "loss_stress_test.py"),
            "--losses",
            *[str(loss) for loss in loss_choices],
            "--variants",
            *variant_choices,
            "--trials",
            str(trials),
            "--file",
            str(STRESS_VIDEO),
            "--out-dir",
            str(STRESS_OUTPUT_DIR),
        ]
        try:
            with log_path.open("w", encoding="utf-8") as log_file:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            st.session_state["loss_stress_run"] = {
                "process": process,
                "output_dir": str(STRESS_OUTPUT_DIR),
                "log_path": str(log_path),
            }
        except OSError as error:
            st.error(f"Could not start the experiment: {error}")
    _render_stress_run()
