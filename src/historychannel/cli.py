"""`hc` command line.

    hc script "The German Revolutions of 1848"     # Claude API: research -> draft -> critique -> fact-check
    hc build content/<slug>/script.json [--preview]
    hc upload content/<slug>/script.json           # private upload of output/<slug>.mp4
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from historychannel import REPO_ROOT


def _build(args: argparse.Namespace) -> int:
    from historychannel.pipeline import build

    out = build(Path(args.script), tts_name=args.tts, preview=args.preview, music_file=args.music)
    print(out)
    return 0


def _upload(args: argparse.Namespace) -> int:
    from historychannel.models import VideoScript
    from historychannel.upload.youtube import upload_video

    script = VideoScript.load(args.script)
    out_dir = REPO_ROOT / "output"
    video = out_dir / f"{script.slug}.mp4"
    srt = video.with_suffix(".srt")
    credits = out_dir / f"{script.slug}.credits.txt"
    video_id = upload_video(
        video,
        script,
        thumbnail=Path(args.thumbnail) if args.thumbnail else None,
        privacy=args.privacy,
        attribution=credits.read_text().splitlines() if credits.exists() else [],
        captions=srt if srt.exists() else None,
        client_secret=Path(args.client_secret),
        token_path=REPO_ROOT / "youtube_token.json",
    )
    print(f"https://youtu.be/{video_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    parser = argparse.ArgumentParser(prog="hc", description="History channel video pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    from historychannel.script import add_cli_arguments, run_from_args

    p = sub.add_parser("script", help="generate a researched script with the Claude API")
    add_cli_arguments(p)
    p.set_defaults(func=lambda a: (print(run_from_args(a)), 0)[1])

    p = sub.add_parser("build", help="render script.json into an MP4")
    p.add_argument("script")
    p.add_argument("--tts", default="auto", choices=["auto", "kokoro", "say", "elevenlabs"])
    p.add_argument("--preview", action="store_true", help="fast 540p render")
    p.add_argument("--music", type=Path, help="music file to use instead of the generated bed")
    p.set_defaults(func=_build)

    p = sub.add_parser("upload", help="upload the built video to YouTube (private by default)")
    p.add_argument("script")
    p.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    p.add_argument("--thumbnail")
    p.add_argument("--client-secret", default=str(REPO_ROOT / "client_secret.json"))
    p.set_defaults(func=_upload)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
