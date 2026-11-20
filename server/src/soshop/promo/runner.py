"""Drive a promo job end to end: Flow renders each shot, ffmpeg stitches them.

Runs as an asyncio task inside the API process: Flow work is waiting on the
browser, and the bridge connection lives in this process. One job at a time,
because every job drives the same Flow tab.

The job state goes to Postgres after every step and each clip goes to the
media store as soon as Flow hands it over. Shots that already have a clip are
kept, so retrying a failed job or regenerating one shot only spends credits on
what is missing, whichever machine runs the retry. Files are worked on in the
job's scratch directory (:func:`soshop.promo.jobs.work_dir`), which is dropped
once the video is ready.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path
from typing import Any

import httpx

from soshop.promo import assemble, jobs, qa
from soshop.promo.bridge import BridgeError, bridge
from soshop.promo.flow import FlowError, FlowSession
from soshop.promo.prompts import shot_prompt
from soshop.promo.script import PromoScript
from soshop.services.studio_assets import resolve_media_path
from soshop_server import storage
from soshop_server.db.pool import DatabaseNotConfiguredError, get_pool

logger = logging.getLogger("soshop.promo.runner")

CTA = {"vi": "Mua ngay", "en": "Shop now"}
_DEBUG_FILE = "flow-error.png"

_tasks: dict[str, asyncio.Task[None]] = {}


class RunnerBusyError(RuntimeError):
    """Another promo video is rendering."""


def running_job_id() -> str | None:
    for job_id, task in list(_tasks.items()):
        if not task.done():
            return job_id
    return None


def is_running(job_id: str) -> bool:
    task = _tasks.get(job_id)
    return task is not None and not task.done()


async def recover_orphans() -> None:
    """Mark jobs left mid-render by a previous API process as failed."""
    try:
        pool = await get_pool()
    except DatabaseNotConfiguredError:
        return
    for job in await jobs.list_busy(pool):
        if is_running(job.id):
            continue
        job.status = "failed"
        job.error = "Server đã khởi động lại khi đang tạo video. Bấm tạo lại để tiếp tục."
        for shot in job.shots:
            if shot.status not in ("done", "pending"):
                shot.status = "failed"
        await jobs.save(pool, job)


async def start(db: Any, job_id: str) -> None:
    current = running_job_id()
    if current is not None:
        raise RunnerBusyError("Đang tạo một video khác bằng Flow. Hãy đợi video đó xong.")
    if not bridge.connected:
        raise BridgeError(
            "Chưa kết nối extension SoShop Flow Bridge. Mở Chrome có cài extension rồi thử lại."
        )
    job = await jobs.load(db, job_id)
    if job is None or job.script is None:
        raise ValueError("Video chưa có kịch bản.")
    job.status = "queued"
    job.stage = "Đang chuẩn bị"
    job.error = None
    await jobs.save(db, job)
    _tasks[job_id] = asyncio.create_task(_run(job))


async def cancel(job_id: str) -> bool:
    task = _tasks.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    return True


# ------------------------------------------------------------------ helpers


class _Writer:
    """Saves one job, also from the synchronous Flow stage callback.

    :meth:`touch` queues a write and returns at once; writes run one at a time
    and each takes the job as it is when it starts, so the newest state always
    lands last. :meth:`save` waits until everything queued is written.
    """

    def __init__(self, job: jobs.PromoJob) -> None:
        self.job = job
        self._dirty = False
        self._task: asyncio.Task[None] | None = None

    def touch(self, **changes: object) -> None:
        for name, value in changes.items():
            setattr(self.job, name, value)
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._flush())

    async def save(self, **changes: object) -> None:
        self.touch(**changes)
        await self.drain()

    async def drain(self) -> None:
        while self._task is not None and not self._task.done():
            # Shielded: a cancelled render must still record that it stopped.
            await asyncio.shield(self._task)

    async def _flush(self) -> None:
        while self._dirty:
            self._dirty = False
            try:
                await jobs.save(await get_pool(), self.job)
            except Exception:
                logger.exception("could not save promo job %s", self.job.id)


async def _local_copy(url: str | None, dst_stem: Path) -> Path | None:
    """A local file for an uploaded or http image."""
    local = await asyncio.to_thread(resolve_media_path, url)
    if local is not None and local.is_file():
        dst = dst_stem.with_suffix(local.suffix.lower() or ".png")
        shutil.copyfile(local, dst)
        return dst
    if url and url.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code == 200 and resp.content:
            kind = resp.headers.get("content-type", "")
            ext = ".png" if "png" in kind else ".webp" if "webp" in kind else ".jpg"
            dst = dst_stem.with_suffix(ext)
            dst.write_bytes(resp.content)
            return dst
    return None


async def _reference(work: Path, job: jobs.PromoJob, role: str, url: str | None) -> Path | None:
    """The product photo or host portrait in scratch, fetched once per job."""
    # Distinctive names: Flow lists uploads by file name and the picker is
    # searched by it.
    stem = f"soshop-{job.id[:8]}-{role}"
    existing = next(iter(sorted(work.glob(f"{stem}.*"))), None)
    return existing or await _local_copy(url, work / stem)


def _script(job: jobs.PromoJob) -> PromoScript:
    if job.script is None:
        raise FlowError("Video chưa có kịch bản.")
    return job.script


def _clip_file(work: Path, index: int) -> Path:
    return work / f"shot-{index + 1}.mp4"


async def _keep_debug(writer: _Writer, work: Path) -> None:
    """Store the screenshot Flow left behind on failure, so the studio can show it."""
    shot = work / _DEBUG_FILE
    if not shot.is_file():
        return
    key = jobs.new_key(writer.job.id, "flow-error", "png")
    try:
        await asyncio.to_thread(storage.get_store().put_file, key, shot, "image/png")
    except Exception:
        logger.warning("could not store the Flow error screenshot", exc_info=True)
        return
    old = writer.job.debug_key
    writer.touch(debug_key=key)
    await asyncio.to_thread(jobs.delete_keys, [old])


# --------------------------------------------------------------------- run


async def _run(job: jobs.PromoJob) -> None:
    writer = _Writer(job)
    work = jobs.work_dir(job.id)
    try:
        await _render(writer, work)
        await _finish(writer, work)
    except asyncio.CancelledError:
        await _keep_debug(writer, work)
        await writer.save(status="failed", error="Đã hủy.", stage="")
        raise
    except (FlowError, BridgeError, assemble.AssembleError, OSError) as exc:
        logger.warning("promo job %s failed: %s", job.id, exc)
        await _keep_debug(writer, work)
        await writer.save(status="failed", error=str(exc), stage="")
    except Exception as exc:
        logger.exception("promo job %s crashed", job.id)
        await _keep_debug(writer, work)
        await writer.save(status="failed", error=f"Lỗi không mong muốn: {exc}", stage="")


async def _has_clip(shot: jobs.ShotState) -> bool:
    if not shot.clip_key:
        return False
    return await asyncio.to_thread(storage.get_store().exists, shot.clip_key)


async def _render(writer: _Writer, work: Path) -> None:
    job = writer.job
    script = _script(job)
    if len(job.shots) != len(script.shots):
        job.shots = [jobs.ShotState(index=i) for i in range(len(script.shots))]
    for shot in job.shots:
        shot.has_clip = await _has_clip(shot)
        if not shot.has_clip:
            shot.clip_key = None
            shot.status, shot.error = "pending", None
    todo = [s for s in job.shots if not s.has_clip]
    (work / _DEBUG_FILE).unlink(missing_ok=True)
    (work / "flow-error.txt").unlink(missing_ok=True)
    await writer.save(status="rendering", stage="Chuẩn bị ảnh tham chiếu")
    if not todo:
        return

    product_ref = await _reference(work, job, "product", job.product.image_url)
    host_ref = await _reference(work, job, "host", job.host.image_url)
    if product_ref is None:
        raise FlowError("Sản phẩm chưa có ảnh. Hãy tải ảnh sản phẩm thật lên trước.")
    if host_ref is None:
        raise FlowError("Người mẫu chưa có ảnh chân dung.")

    await writer.save(stage="Mở Google Flow")
    session = await FlowSession.open(bridge, debug_dir=work)
    try:
        project_url = await session.ensure_project(job.flow_project_url)
        await writer.save(flow_project_url=project_url)
        refs = await session.upload_references([host_ref, product_ref])
        for shot in todo:
            await _render_shot(writer, work, session, shot, refs)
    finally:
        await session.close()


async def _render_shot(
    writer: _Writer,
    work: Path,
    session: FlowSession,
    shot: jobs.ShotState,
    refs: list[str],
) -> None:
    job = writer.job
    script = _script(job)
    total = len(job.shots)
    prompt = shot_prompt(script, script.shots[shot.index], job.product, job.host, job.language)
    shot.prompt, shot.started_at, shot.finished_at = prompt, jobs.now(), None

    def stage(status: jobs.ShotStatus, text: str) -> None:
        shot.status = status
        writer.touch(stage=f"Cảnh {shot.index + 1}/{total}: {text}")

    clip = _clip_file(work, shot.index)
    try:
        await session.generate_clip(
            prompt=prompt,
            references=refs,
            model_label=job.flow_model,
            out=clip,
            on_stage=stage,
        )
    except (FlowError, BridgeError) as exc:
        shot.status, shot.error = "failed", str(exc)
        await writer.save()
        raise
    key = jobs.new_key(job.id, f"shot-{shot.index + 1}", "mp4")
    await asyncio.to_thread(storage.get_store().put_file, key, clip, "video/mp4")
    shot.clip_key, shot.has_clip, shot.finished_at = key, True, jobs.now()
    stage("downloading", "kiểm tra lời thoại")
    heard = await qa.transcribe(clip, job.language)
    if heard is not None:
        shot.transcript = heard.text
        shot.speech_match = qa.match_score(script.shots[shot.index].dialogue, heard.text)
        shot.speech_start, shot.speech_end = heard.speech_start, heard.speech_end
    shot.status = "done"
    await writer.save()


async def _finish(writer: _Writer, work: Path) -> None:
    job = writer.job
    photo = await _reference(work, job, "product", job.product.image_url)
    await writer.save(status="assembling", stage="Ghép các cảnh thành video")
    made = await asyncio.to_thread(_assemble, work, job, photo)
    replaced = [job.video_key, job.thumb_key, job.captions_key]
    await writer.save(status="ready", stage="", error=None, **made)
    await asyncio.to_thread(jobs.delete_keys, replaced)
    shutil.rmtree(work, ignore_errors=True)


def _fetch_clip(work: Path, shot: jobs.ShotState) -> Path:
    """The shot's clip in scratch, downloaded from the store if this machine lacks it."""
    clip = _clip_file(work, shot.index)
    if clip.is_file():
        return clip
    if shot.clip_key and storage.get_store().download(shot.clip_key, clip):
        return clip
    raise assemble.AssembleError(f"Thiếu clip của cảnh {shot.index + 1}. Hãy tạo lại cảnh này.")


def _assemble(work: Path, job: jobs.PromoJob, photo: Path | None) -> dict[str, object]:
    """Stitch the clips and store the results. Blocking: runs in a thread."""
    script = _script(job)
    parts_dir = work / "work"
    parts_dir.mkdir(exist_ok=True)
    parts: list[Path] = []
    durations: list[float] = []
    for shot in job.shots:
        dst = parts_dir / f"part-{shot.index + 1}.mp4"
        durations.append(assemble.normalise(_fetch_clip(work, shot), dst))
        parts.append(dst)
    end = parts_dir / "endcard.mp4"
    assemble.end_card_clip(job.product, photo, CTA.get(job.language, CTA["en"]), end)
    parts.append(end)
    video = work / "video.mp4"
    assemble.concat(parts, video)
    thumb = work / "thumb.jpg"
    assemble.thumbnail(video, thumb)
    lines = [s.dialogue for s in script.shots]
    spans = [
        (s.speech_start, s.speech_end)
        if s.speech_start is not None and s.speech_end is not None
        else None
        for s in job.shots
    ]
    captions = assemble.webvtt(lines, durations, spans)
    total = sum(durations) + assemble.END_CARD_SECONDS

    store = storage.get_store()
    video_key = jobs.new_key(job.id, "video", "mp4")
    thumb_key = jobs.new_key(job.id, "thumb", "jpg")
    captions_key = jobs.new_key(job.id, "captions", "vtt")
    store.put_file(video_key, video, "video/mp4")
    store.put_file(thumb_key, thumb, "image/jpeg")
    store.put_bytes(captions_key, captions.encode("utf-8"), "text/vtt")
    return {
        "video_key": video_key,
        "thumb_key": thumb_key,
        "captions_key": captions_key,
        "duration_s": round(total, 2),
    }
