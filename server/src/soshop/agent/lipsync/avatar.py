"""LiveKit wiring for the lip-sync avatar.

``LipSyncAvatar`` takes over the agent session's audio output. TTS audio no
longer goes to the room directly: it flows into the video generator, and the
generator's audio and video come back out through one ``AVSynchronizer`` onto
two tracks published by the agent's own participant (``avatar_audio`` and
``avatar_video``).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livekit import rtc
from livekit.agents.voice.avatar import AvatarOptions, AvatarRunner, QueueAudioOutput

if TYPE_CHECKING:
    from livekit.agents.voice import AgentSession

logger = logging.getLogger("soshop.agent.lipsync")

_VIDEO_MAX_BITRATE = 2_500_000
"""Without an explicit ceiling the encoder starts from a low default and
halves the resolution to fit it, which smears the mouth."""

AUDIO_SAMPLE_RATE = 24000
"""Rate requested from the session's TTS. 24 kHz is a whole number of samples
per frame at 20, 25 and 30 fps."""


class _EagerAvatarRunner(AvatarRunner):
    """``AvatarRunner`` that publishes without waiting for a subscriber.

    The stock runner blocks its first frame on ``wait_for_subscription`` of
    the audio track. A livestream host has to keep running with an empty
    room: otherwise speech queues up unplayed, the session never sees
    "playback finished", and the whole pipeline stalls until someone joins.
    """

    async def _publish_track(self) -> None:
        async with self._lock:
            await self._room_connected_fut
            audio_track = rtc.LocalAudioTrack.create_audio_track("avatar_audio", self._audio_source)
            self._audio_publication = await self._room.local_participant.publish_track(
                audio_track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            video_track = rtc.LocalVideoTrack.create_video_track("avatar_video", self._video_source)
            self._video_publication = await self._room.local_participant.publish_track(
                video_track,
                rtc.TrackPublishOptions(
                    source=rtc.TrackSource.SOURCE_CAMERA,
                    simulcast=False,
                    video_encoding=rtc.VideoEncoding(
                        max_bitrate=_VIDEO_MAX_BITRATE,
                        max_framerate=self._options.video_fps,
                    ),
                ),
            )


class LipSyncAvatar:
    """Realistic talking avatar rendered locally from prebuilt assets."""

    def __init__(self, room: rtc.Room, *, assets_dir: Path, models_dir: Path) -> None:
        self._room = room
        self._assets_dir = assets_dir
        self._models_dir = models_dir
        self._runner: AvatarRunner | None = None
        self._audio_out: QueueAudioOutput | None = None

    async def start(self, session: AgentSession) -> None:
        """Call after ``ctx.connect()`` and BEFORE ``session.start()``.

        Takes over the session's audio output (sets ``session.output.audio``)
        and publishes synced audio and video tracks itself. The caller must
        start the session with room audio output disabled, for example
        ``RoomOutputOptions(audio_enabled=False)``, or the room output would
        replace this one.
        """
        clip, engine, matte = await asyncio.to_thread(self._load)
        from soshop.agent.lipsync.generator import LipSyncVideoGenerator

        generator = LipSyncVideoGenerator(clip, engine, sample_rate=AUDIO_SAMPLE_RATE, matte=matte)
        self._audio_out = QueueAudioOutput(sample_rate=AUDIO_SAMPLE_RATE)
        session.output.audio = self._audio_out
        self._runner = _EagerAvatarRunner(
            self._room,
            audio_recv=self._audio_out,
            video_gen=generator,
            options=AvatarOptions(
                video_width=clip.width,
                video_height=clip.height,
                video_fps=float(clip.fps),
                audio_sample_rate=AUDIO_SAMPLE_RATE,
                audio_channels=1,
            ),
            _lazy_publish=False,
        )
        await self._runner.start()
        logger.info(
            "lip-sync avatar started",
            extra={
                "frames": len(clip),
                "tracks": clip.n_tracks,
                "matte": matte is not None,
                "fps": clip.fps,
                "device": engine.device,
                "infer_ms": round(engine.frame_seconds * 1000, 1),
            },
        )

    def _load(self) -> tuple[Any, Any, Any]:
        from soshop.agent.lipsync.assets import MotionClip
        from soshop.agent.lipsync.models import ensure_wav2lip
        from soshop.agent.lipsync.wav2lip import Wav2LipEngine

        clip = MotionClip.load(self._assets_dir)
        engine = Wav2LipEngine(ensure_wav2lip(self._models_dir))
        return clip, engine, self._load_matte(clip)

    def _load_matte(self, clip: Any) -> Any:
        """The clip's person matte, computed now for clips built without one.

        Never downloads at session start: without the matting model already
        on disk the body motion uses its geometric fallback.
        """
        from soshop.agent.lipsync.assets import MATTE_FILE
        from soshop.agent.lipsync.matte import load_matte, person_matte, save_matte

        if clip.matte_path is not None:
            return load_matte(clip.matte_path)
        try:
            matte = person_matte(self._models_dir, clip.frame(0))
        except FileNotFoundError:
            return None
        except Exception:
            logger.warning("person matte failed; body motion uses a geometric mask", exc_info=True)
            return None
        try:
            save_matte(self._assets_dir / MATTE_FILE, matte)
        except OSError:
            logger.debug("could not cache the person matte", exc_info=True)
        return matte

    async def aclose(self) -> None:
        if self._runner is not None:
            runner, self._runner = self._runner, None
            await runner.aclose()
