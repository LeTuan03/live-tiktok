"""Drive a promo job end to end: Flow renders each shot, ffmpeg stitches them.

Runs as an asyncio task inside the API process: Flow work is waiting on the
browser, and the bridge connection lives in this process. One job at a time,
because every job drives the same Flow tab. Shots that already have a clip
are kept, so retrying a failed job or regenerating one shot only spends
credits on what is missing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
from pathlib import Path

import httpx

from soshop.promo import assemble, jobs, qa
from soshop.promo.bridge import BridgeError, bridge
from soshop.promo.flow import FlowError, FlowSession
from soshop.promo.prompts import shot_prompt
from soshop.promo.script import PromoScript
from soshop.services.studio_assets import data_dir, resolve_media_path

logger = logging.getLogger("soshop.promo.runner")

CTA = {"vi": "Mua ngay", "en": "Shop now"}

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


def recover_orphans() -> None:
    """Mark jobs left mid-render by a previous API process as failed."""
    for job in jobs.list_all(data_dir()):
        path = jobs.job_dir(data_dir(), job.id)
        if job.busy and not is_running(job.id) and path is not None:
            job.status = "failed"
            job.error = "Server đã khởi động lại khi đang tạo video. Bấm tạo lại để tiếp tục."
            for shot in job.shots:
                if shot.status not in ("done", "pending"):
                    shot.status = "failed"
            jobs.save(path, job)


def start(job_id: str) -> None:
    current = running_job_id()
    if current is not None:
        raise RunnerBusyError("Đang tạo một video khác bằng Flow. Hãy đợi video đó xong.")
    if not bridge.connected:
        raise BridgeError(
            "Chưa kết nối extension SoShop Flow Bridge. Mở Chrome có cài extension rồi thử lại."
        )
    path = jobs.job_dir(data_dir(), job_id)
    job = jobs.load(path) if path else None
    if path is None or job is None or job.script is None:
        raise ValueError("Video chưa có kịch bản.")
    job.status = "queued"
    job.stage = "Đang chuẩn bị"
    job.error = None
    jobs.save(path, job)
    _tasks[job_id] = asyncio.create_task(_run(path))


async def cancel(job_id: str) -> bool:
    task = _tasks.get(job_id)
    if task is None or task.done():
        return False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    return True


# ------------------------------------------------------------------ helpers


async def _local_copy(url: str | None, dst_stem: Path) -> Path | None:
    """A local file for an uploaded (``/media/uploads``) or http image."""
    local = resolve_media_path(url)
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


def _script(job: jobs.PromoJob) -> PromoScript:
    if job.script is None:
        raise FlowError("Video chưa có kịch bản.")
    return job.script


def _save(path: Path, job: jobs.PromoJob, **changes: object) -> None:
    for key, value in changes.items():
        setattr(job, key, value)
    jobs.save(path, job)


# --------------------------------------------------------------------- run


async def _run(path: Path) -> None:
    job = jobs.load(path)
    if job is None or job.script is None:
        return
    try:
        await _render(path, job)
        await asyncio.to_thread(_assemble, path, job)
    except asyncio.CancelledError:
        _save(path, job, status="failed", error="Đã hủy.", stage="")
        raise
    except (FlowError, BridgeError, assemble.AssembleError, OSError) as exc:
        logger.warning("promo job %s failed: %s", job.id, exc)
        _save(path, job, status="failed", error=str(exc), stage="")
    except Exception as exc:
        logger.exception("promo job %s crashed", job.id)
        _save(path, job, status="failed", error=f"Lỗi không mong muốn: {exc}", stage="")


async def _render(path: Path, job: jobs.PromoJob) -> None:
    script = _script(job)
    if len(job.shots) != len(script.shots):
        job.shots = [jobs.ShotState(index=i) for i in range(len(script.shots))]
    for shot in job.shots:
        shot.has_clip = jobs.clip_path(path, shot.index).is_file()
        if not shot.has_clip:
            shot.status, shot.error = "pending", None
    todo = [s for s in job.shots if not s.has_clip]
    for stale in ("flow-error.png", "flow-error.txt"):
        (path / stale).unlink(missing_ok=True)
    _save(path, job, status="rendering", stage="Chuẩn bị ảnh tham chiếu")
    if not todo:
        return

    # Distinctive names: Flow lists uploads by file name and the picker is
    # searched by it.
    product_ref = await _local_copy(job.product.image_url, path / f"soshop-{job.id[:8]}-product")
    host_ref = await _local_copy(job.host.image_url, path / f"soshop-{job.id[:8]}-host")
    if product_ref is None:
        raise FlowError("Sản phẩm chưa có ảnh. Hãy tải ảnh sản phẩm thật lên trước.")
    if host_ref is None:
        raise FlowError("Người mẫu chưa có ảnh chân dung.")

    _save(path, job, stage="Mở Google Flow")
    session = await FlowSession.open(bridge, debug_dir=path)
    try:
        project_url = await session.ensure_project(job.flow_project_url)
        _save(path, job, flow_project_url=project_url)
        refs = await session.upload_references([host_ref, product_ref])
        for shot in todo:
            await _render_shot(path, job, session, shot, refs)
    finally:
        await session.close()


async def _render_shot(
    path: Path,
    job: jobs.PromoJob,
    session: FlowSession,
    shot: jobs.ShotState,
    refs: list[str],
) -> None:
    script = _script(job)
    total = len(job.shots)
    prompt = shot_prompt(script, script.shots[shot.index], job.product, job.host, job.language)
    shot.prompt, shot.started_at, shot.finished_at = prompt, jobs.now(), None

    def stage(status: jobs.ShotStatus, text: str) -> None:
        shot.status = status
        _save(path, job, stage=f"Cảnh {shot.index + 1}/{total}: {text}")

    try:
        await session.generate_clip(
            prompt=prompt,
            references=refs,
            model_label=job.flow_model,
            out=jobs.clip_path(path, shot.index),
            on_stage=stage,
        )
    except (FlowError, BridgeError) as exc:
        shot.status, shot.error = "failed", str(exc)
        jobs.save(path, job)
        raise
    shot.has_clip, shot.finished_at = True, jobs.now()
    stage("downloading", "kiểm tra lời thoại")
    heard = await qa.transcribe(jobs.clip_path(path, shot.index), job.language)
    if heard is not None:
        shot.transcript = heard.text
        shot.speech_match = qa.match_score(script.shots[shot.index].dialogue, heard.text)
        shot.speech_start, shot.speech_end = heard.speech_start, heard.speech_end
    shot.status = "done"
    jobs.save(path, job)


def _assemble(path: Path, job: jobs.PromoJob) -> None:
    script = _script(job)
    _save(path, job, status="assembling", stage="Ghép các cảnh thành video")
    work = path / "work"
    work.mkdir(exist_ok=True)
    parts: list[Path] = []
    durations: list[float] = []
    for shot in job.shots:
        dst = work / f"part-{shot.index + 1}.mp4"
        durations.append(assemble.normalise(jobs.clip_path(path, shot.index), dst))
        parts.append(dst)
    photo = next(iter(sorted(path.glob("soshop-*-product.*"))), None)
    end = work / "endcard.mp4"
    assemble.end_card_clip(job.product, photo, CTA.get(job.language, CTA["en"]), end)
    parts.append(end)
    video = path / jobs.VIDEO_FILE
    assemble.concat(parts, video)
    assemble.thumbnail(video, path / jobs.THUMB_FILE)
    lines = [s.dialogue for s in script.shots]
    spans = [
        (s.speech_start, s.speech_end)
        if s.speech_start is not None and s.speech_end is not None
        else None
        for s in job.shots
    ]
    captions = assemble.webvtt(lines, durations, spans)
    (path / jobs.CAPTIONS_FILE).write_text(captions, encoding="utf-8")
    total = sum(durations) + assemble.END_CARD_SECONDS
    _save(path, job, status="ready", stage="", error=None, duration_s=round(total, 2))
