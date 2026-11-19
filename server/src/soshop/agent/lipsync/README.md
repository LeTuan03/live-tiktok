# lipsync: local talking avatar

A keyless avatar that runs entirely on the agent machine. It replaces the
still-image fallback when no LemonSlice key is configured.

## How it works

1. **Offline, once per model photo** (`build.py`). LivePortrait animates the
   portrait into a 12 s, 25 fps motion clip with two **behavior tracks** on
   one timeline (`motion.py`):
   - *idle*, between sentences: calm sway, glances down at the comments,
     small listening nods, a smile now and then;
   - *talk*, speaking to camera: nods on a beat, head tilts, raised brows for
     emphasis, a look aside and back, a bigger smile, more blinks, and fast
     low-amplitude head motion that goes with articulation.
   Gestures are smooth bumps inside 1.2 to 2.1 s slots, so at every slot
   boundary both tracks show the same pose. Those frames are *portals*:
   playback may switch track there with no jump. The mouth stays neutral and
   both tracks loop with no seam. MODNet also extracts a person matte of the
   first frame. Takes about 14 minutes on a laptop with an Intel iGPU (about
   600 LivePortrait frames).
2. **Realtime** (`generator.py`). The clip loops forever, on the talk track
   while there is speech and on the idle track otherwise, switching at the
   next portal (0.8 s of silence counts as idle). While the agent speaks,
   Wav2Lip repaints the mouth and jaw from the TTS audio. With no speech
   nothing is inferred. Four details matter for quality:
   - the face is sampled through a per-frame affine transform (`align.py`)
     that turns it upright first, because the model smears the teeth of a
     tilted head, and the result is pasted back through the same transform;
   - the model works in RGB (fed BGR it paints purple lips);
   - its skin tone is nudged to match the frame so the blend is invisible;
   - it paints teeth the color of skin (dull orange), so pixels in the inner
     mouth that are much brighter than the closed-mouth reference get their
     color pulled toward white.
   Under CPU load the mouth is re-inferred only every 2nd or 3rd frame.
3. **Performance layer** (`performance.py`). Every finished frame goes through
   one smooth warp, about 5 ms: the shoulders and chest breathe, the upper body
   sways around a pivot below the frame, it leans toward the camera while the
   host talks, stressed syllables (a jump in loudness over the running level,
   read 40 to 120 ms ahead of playback) kick a short head nod, about one every
   two seconds, and a slow handheld drift moves the camera. Body terms are
   weighted by the person matte so the background stays put. If even the
   coarsest mouth stride cannot keep up, this layer switches itself off.
4. **Publishing** (`avatar.py`). livekit-agents' `AvatarRunner` pushes the
   generator's audio and video through one `AVSynchronizer`, so the two tracks
   stay in sync. The agent's own participant publishes them.

## Use

```bash
uv run --group agent python -m soshop.agent.lipsync.build \
    --image <path or url> --out data/avatars/<persona_id> --models data/models
```

`<out>/status.json` reports `{"status": "processing" | "ready" | "failed",
"progress": 0..1, "error": ...}` while it runs.

```python
from soshop.agent.lipsync import LipSyncAvatar, avatar_assets_ready

if avatar_assets_ready(assets_dir):
    avatar = LipSyncAvatar(ctx.room, assets_dir=assets_dir, models_dir=models_dir)
    await avatar.start(session)      # after ctx.connect(), before session.start()
    await session.start(agent, room=ctx.room,
                        room_output_options=RoomOutputOptions(audio_enabled=False))
    ctx.add_shutdown_callback(avatar.aclose)
```

`avatar_assets_ready` and `read_status` import only numpy, so the API process
can call them without the `agent` dependency group.

## What is and is not animated

Animated: head pose, eyes, eyelids, brows, cheeks, mouth, jaw, and the hair
and neck inside the face crop (about 2.3 face widths square), with different
behavior while talking and while idle. The shoulders and torso breathe, sway
and lean (a 2D warp of the photo, a few pixels), the head nods on stressed
syllables, and the camera drifts.

Not animated: arms and hands never move on their own, so there are no hand
gestures and the host cannot pick up or show a product. Talk gestures follow
the rhythm of speech (nods on loud syllables) but not its meaning: a joke
and a price get the same repertoire. The talk track repeats every 12 s,
which the audio-driven nods and the camera drift, both out of step with the
loop, mostly hide. The mouth interior is synthesized at 96x96 and is softer
than the rest of the face.

A portrait works best when it is a front-facing head-and-shoulders shot, the
face at least 90 px wide after fitting to 540x960. An open mouth (a laugh, a
wide smile with teeth) is closed automatically before animating, using
LivePortrait's lip retargeting network, so the model idles with a relaxed
closed-lip smile instead of a frozen laugh.

## Measured on the dev laptop (i5-1135G7, Iris Xe, 16 GB)

| Step | Number |
|---|---|
| Asset build, about 600 frames (two tracks) | about 14 min, 1.4 s per frame |
| Performance warp (breathing, sway, nods, camera) | about 5 ms per frame |
| Wav2Lip inference, Iris Xe via OpenVINO | 14 ms per frame |
| Same on the CPU via OpenVINO | 30 ms per frame |
| Full lip-synced frame (decode, infer, blend) | 24 ms, 42 fps of headroom at 25 fps |
| First audio in to first audio out | about 0.4 s (0.2 s is the model's lookahead) |
| Interruption to silence | about 0.15 s |

## Models and licenses

Weights download on first use into the models directory (`data/models`,
git-ignored). Nothing is vendored in this repository.

| Model | Source | License |
|---|---|---|
| LivePortrait (appearance, motion, warping, stitching, lip retargeting), ONNX export | `warmshao/FasterLivePortrait` on Hugging Face, from KwaiVGI/LivePortrait | MIT |
| SCRFD face detector and 2d106det landmarks (shipped in the same repo) | InsightFace `buffalo_l` | Non-commercial research use only |
| Wav2Lip GAN, ONNX export | `bluefoxcreation/Wav2lip-Onnx` on Hugging Face, from Rudrabha/Wav2Lip | Non-commercial research use only (trained on LRS2) |
| MODNet portrait matting, ONNX export | `Xenova/modnet` on Hugging Face, from ZHKKKe/MODNet | Apache-2.0 |

Because of the InsightFace and Wav2Lip terms this pipeline is fine for a demo
or internal evaluation, and it is **not licensed for commercial livestream
selling**. A commercial deployment needs a licensed lip-sync model or a hosted
avatar provider (LemonSlice is already wired in: set `LEMONSLICE_API_KEY`).

## Dependencies

In the `agent` group: `openvino` (inference on the Intel GPU or CPU),
`opencv-python-headless` (image ops), `onnx` (only to split the warping model
at build time). `onnxruntime` was already present.

The LivePortrait warping network contains two 5D `GridSample` nodes that
OpenVINO cannot load, and the whole model on ONNX Runtime's CPU provider takes
6 to 8 s per frame. `models.split_warping_model` cuts the graph around those
two nodes so the convolution stages run on the GPU and only the `GridSample`
stages stay on ONNX Runtime.
