import subprocess
import sys
from pathlib import Path

try:
    import imageio_ffmpeg  # type: ignore
except ImportError:
    print("imageio-ffmpeg not installed. Run: pip install imageio-ffmpeg", file=sys.stderr)
    sys.exit(1)


def main():
    if len(sys.argv) < 3:
        print("Usage: python encode_reps.py <input_video> <output_dir>")
        sys.exit(1)

    input_video = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    bitrates = [400, 800, 1500]  # kbps
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    for br in bitrates:
        out_file = out_dir / f"{br}k.mp4"
        cmd = [
            ffmpeg,
            "-i",
            str(input_video),
            "-c:v",
            "libx264",
            "-b:v",
            f"{br}k",
            "-an",
            str(out_file),
            "-y",
            "-loglevel",
            "error",
        ]
        print(f"Encoding {out_file} ...")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"Encoding failed for {br} kbps", file=sys.stderr)
            sys.exit(result.returncode)

    print("All encodes complete.")


if __name__ == "__main__":
    main() 