"""Make a product review video from the command line.

    uv run --group agent python -m soshop.advideo --portrait ../model/mau.png \\
        --name "Son kem lì" --price 199000 --description "..." --highlights "..." \\
        [--product-image photo.jpg] [--voice google-vi] [--script script.json] \\
        [--music bgm.mp3] [--reuse-host] --out data/videos/demo

or with everything in one JSON file (what the studio API writes):

    python -m soshop.advideo --request request.json --out data/videos/<job_id>

``request.json``: ``{"portrait", "voice", "language", "product": {"name",
"price", "currency", "description", "highlights", "image"}}``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from soshop.advideo.pipeline import VideoRequest, make_video
from soshop.advideo.script import AdScript, ProductBrief
from soshop.agent.lipsync.assets import write_status

logger = logging.getLogger("soshop.advideo")


def _args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Make a TikTok-style product review video.")
    parser.add_argument("--request", type=Path, default=None, help="request.json")
    parser.add_argument("--portrait", help="model photo: path or http(s) url")
    parser.add_argument("--name", help="product name")
    parser.add_argument("--price", type=float, default=None)
    parser.add_argument("--currency", default="VND")
    parser.add_argument("--description", default="")
    parser.add_argument("--highlights", default="")
    parser.add_argument("--product-image", default=None)
    parser.add_argument("--voice", default="google-vi")
    parser.add_argument("--language", default="vi", choices=("vi", "en"))
    parser.add_argument("--script", type=Path, default=None, help="script.json to speak as is")
    parser.add_argument("--music", type=Path, default=None, help="background music, quiet")
    parser.add_argument("--reuse-host", action="store_true", help="reuse host.mp4 if unchanged")
    parser.add_argument("--models", type=Path, default=Path("data/models"))
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def _request(args: argparse.Namespace) -> VideoRequest:
    if args.request is not None:
        data = json.loads(args.request.read_text(encoding="utf-8"))
        product = ProductBrief(**data["product"])
        portrait, voice, language = data["portrait"], data["voice"], data["language"]
    else:
        if not args.portrait or not args.name:
            raise SystemExit("--portrait and --name are required without --request")
        product = ProductBrief(
            name=args.name,
            price=args.price,
            currency=args.currency,
            description=args.description,
            highlights=args.highlights,
            image=args.product_image,
        )
        portrait, voice, language = args.portrait, args.voice, args.language
    script = None
    if args.script is not None:
        script = AdScript.from_json(json.loads(args.script.read_text(encoding="utf-8")), language)
    return VideoRequest(
        portrait=portrait,
        product=product,
        voice=voice,
        language=language,
        script=script,
        music=args.music,
        reuse_host=args.reuse_host,
    )


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        request = _request(args)
        video = asyncio.run(make_video(request, args.out, args.models))
    except Exception as exc:
        logger.exception("video failed")
        write_status(args.out, "failed", 0.0, str(exc) or type(exc).__name__)
        return 1
    print(video)
    return 0


if __name__ == "__main__":
    sys.exit(main())
