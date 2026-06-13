"""Record a video of a policy playing a board (harness/replay.lua).

Resolves the board's cached eval disk + save state, then runs MAME with
-aviwrite while replay.lua self-drives N balls using a trained policy
(or random if no policy file). Converts the AVI to MP4 + GIF if ffmpeg
is available.

Usage:
  python3 tools/record_replay.py work/evo_smoke/g004_o1.pb \
      --policy work/policies/g004_o1.txt --balls 3 --out work/videos/g004_o1
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from pcs_env import ensure_state  # noqa: E402


def record(pb_path: str, out: str, policy: str | None, balls: int,
           plunger: int, seconds: int) -> str:
    disk, state = ensure_state(pb_path)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    # absolute path: MAME prepends its snapshot dir to relative -aviwrite
    avi = os.path.abspath(out + ".avi")
    env = dict(os.environ, PCS_STATE=state, PCS_BALLS=str(balls),
               PCS_PLUNGER=str(plunger))
    if policy:
        env["PCS_POLICY"] = policy
    cmd = [
        os.path.join(ROOT, "scripts", "run_mame.sh"),
        "-flop1", disk, "-gameio", "joy",
        "-sound", "none", "-nothrottle",
        "-seconds_to_run", str(seconds),
        "-autoboot_delay", "1",
        "-autoboot_script", os.path.join(ROOT, "harness", "replay.lua"),
        "-aviwrite", avi,
    ]
    subprocess.run(cmd, env=env, timeout=seconds * 30 + 120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return avi


def to_mp4_gif(avi: str, out: str, start: float, length: float) -> None:
    if not shutil_which("ffmpeg"):
        return
    mp4 = out + ".mp4"
    subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(length),
                    "-i", avi, "-vf", "scale=560:384:flags=neighbor",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-an", mp4],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gif = out + ".gif"
    subprocess.run(["ffmpeg", "-y", "-ss", str(start), "-t", str(length),
                    "-i", avi,
                    "-vf", "fps=15,scale=480:329:flags=neighbor,"
                           "split[s0][s1];[s0]palettegen=max_colors=32[p];"
                           "[s1][p]paletteuse=dither=none", gif],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def shutil_which(name: str):
    from shutil import which
    return which(name)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board")
    ap.add_argument("--policy", default=None)
    ap.add_argument("--balls", type=int, default=3)
    ap.add_argument("--plunger", type=int, default=220)
    ap.add_argument("--seconds", type=int, default=40)
    ap.add_argument("--clip-start", type=float, default=12.0,
                    help="seconds into the AVI where play begins")
    ap.add_argument("--clip-len", type=float, default=22.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    avi = record(args.board, args.out, args.policy, args.balls,
                 args.plunger, args.seconds)
    to_mp4_gif(avi, args.out, args.clip_start, args.clip_len)
    print(f"recorded {avi}")
    for ext in (".mp4", ".gif"):
        if os.path.exists(args.out + ext):
            print(f"  -> {args.out}{ext} ({os.path.getsize(args.out+ext)} bytes)")


if __name__ == "__main__":
    main()
