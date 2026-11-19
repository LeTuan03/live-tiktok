"""Livestream sessions played from pre-rendered product videos.

``timeline`` derives the on-air position from an anchor, ``control`` maps
control-room actions onto it, ``media`` resolves and stores the videos and
``hub`` wakes event streams when a broadcast changes. None of them touch the
database; ``soshop.services.broadcasts`` does.
"""
