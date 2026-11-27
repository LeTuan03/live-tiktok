"""Push on-air broadcasts to live platforms (TikTok Live, Shopee Live, ...) over RTMP.

``platforms`` validates destinations and keeps stream keys out of what leaves
the API, ``encoder`` builds the ffmpeg command lines, ``plan`` decides what the
feed should be encoding as the broadcast moves, ``relay`` runs the processes
and ``runner`` ties them to the database. Everything but ``relay`` and
``runner`` is pure.
"""
