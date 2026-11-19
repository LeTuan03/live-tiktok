"""Pre-rendered TikTok-style product review videos with the AI model as host.

Unlike the live studio, nothing here runs in real time, so every frame can
afford the full LivePortrait render (about 1.2 s on a laptop iGPU):

1. ``script``   the LLM writes short spoken beats, each tagged with a gesture
   and whether the product should be on screen;
2. ``voice``    each beat is spoken separately, so sentence and word timings
   are known for gestures and captions;
3. ``director`` turns script and audio into per-frame head pose, expression,
   blinks, beat nods and mouth opening (``mouth``);
4. ``render``   LivePortrait renders every frame at 720x1280 and the body
   breathes and sways (``soshop.agent.lipsync.performance``);
5. ``edit``     composes the 1080x1920 cut: punch-in shots per beat, burned
   captions with the spoken word highlighted, hook title, product card and
   call-to-action, then muxes the voice.

``pipeline.make_video`` runs all five and reports progress through a
``status.json`` like the avatar build. ``python -m soshop.advideo`` is the CLI.
"""
