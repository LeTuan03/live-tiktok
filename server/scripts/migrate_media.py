"""Move media made before migration 0006 to where the app now looks for it.

    uv run python scripts/migrate_media.py            # import promo jobs into Postgres
    uv run python scripts/migrate_media.py --upload   # also copy local files to the bucket
    uv run python scripts/migrate_media.py --dry-run  # only report what would happen

1. **Promo jobs.** Every ``DATA_DIR/promo/<id>/job.json`` becomes a row in
   ``promo_videos`` (migration 0006 must be applied). The files next to it
   (``video.mp4``, ``thumb.jpg``, ``captions.vtt``, ``shot-N.mp4``,
   ``flow-error.png``) are recorded by their media-store keys, which on local
   storage are exactly where they already are. Rows that exist are left alone,
   so running it twice is safe. ``job.json`` is not deleted.
2. **``--upload``** (needs ``MEDIA_STORAGE=s3``): copies ``DATA_DIR/uploads/*``
   and every file a promo job points at into the bucket under the same keys.
   Objects already there are skipped. Database URLs need no rewrite: the API
   redirects ``/media/<key>`` to the bucket once it runs on S3.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# This script lives in `server/scripts/`; put the package sources on sys.path.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from soshop.promo import jobs  # noqa: E402
from soshop_server import storage  # noqa: E402
from soshop_server.db.pool import close_pool, get_pool  # noqa: E402

_LEGACY_FILES = {
    "video_key": "video.mp4",
    "thumb_key": "thumb.jpg",
    "captions_key": "captions.vtt",
    "debug_key": "flow-error.png",
}


def legacy_job(job_dir: Path) -> jobs.PromoJob | None:
    """A job.json from before 0006, with its files recorded by key."""
    try:
        job = jobs.PromoJob.model_validate(
            json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        print(f"  skip {job_dir.name}: unreadable job.json ({exc})")
        return None
    if job.id != job_dir.name or not jobs.valid_id(job.id):
        print(f"  skip {job_dir.name}: id does not match the directory")
        return None
    for field, name in _LEGACY_FILES.items():
        if (job_dir / name).is_file():
            setattr(job, field, jobs.key(job.id, name))
    for shot in job.shots:
        name = f"shot-{shot.index + 1}.mp4"
        has = (job_dir / name).is_file()
        shot.clip_key, shot.has_clip = (jobs.key(job.id, name) if has else None), has
    if job.status == "ready" and job.video_key is None:
        job.status, job.error = "failed", "Không tìm thấy tệp video khi chuyển dữ liệu."
    return job


async def import_jobs(root: Path, dry_run: bool) -> list[jobs.PromoJob]:
    promo = root / "promo"
    found = [
        job
        for path in sorted(promo.iterdir() if promo.is_dir() else [])
        if (path / "job.json").is_file() and (job := legacy_job(path)) is not None
    ]
    pool = await get_pool()
    for job in found:
        exists = await jobs.load(pool, job.id) is not None
        state = "exists" if exists else "import"
        files = len(job.file_keys())
        print(f"  {state} {job.id} [{job.status}] {job.product.name} ({files} files)")
        if not exists and not dry_run:
            await jobs.insert(pool, job)
    return found


def upload(root: Path, found: list[jobs.PromoJob], dry_run: bool) -> None:
    store = storage.get_store()
    if store.kind != "s3":
        raise SystemExit("--upload needs MEDIA_STORAGE=s3 (plus BUCKET_NAME and S3_PUBLIC_URL).")
    uploads = root / "uploads"
    keys = [
        f"uploads/{path.name}"
        for path in sorted(uploads.iterdir() if uploads.is_dir() else [])
        if path.is_file()
    ]
    keys += [key for job in found for key in job.file_keys()]
    copied = skipped = 0
    for key in keys:
        src = root.joinpath(*key.split("/"))
        if not storage.valid_key(key) or not src.is_file() or store.exists(key):
            skipped += 1
            continue
        print(f"  upload {key} ({src.stat().st_size // 1024} KB)")
        if not dry_run:
            store.put_file(key, src, storage.content_type_for(key))
        copied += 1
    print(f"uploaded {copied}, skipped {skipped}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--upload", action="store_true", help="copy local files to the bucket")
    parser.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Vietnamese names on a Windows console
    root = storage.data_root()
    print(f"DATA_DIR {root}")
    print("promo jobs:")
    try:
        found = await import_jobs(root, args.dry_run)
    finally:
        await close_pool()
    if args.upload:
        print("files:")
        upload(root, found, args.dry_run)


if __name__ == "__main__":
    asyncio.run(main())
