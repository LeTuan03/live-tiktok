"""Product promo videos generated with Google Flow (Veo).

Flow: catalog product -> AI script (``script``) -> per-shot Veo prompts
(``prompts``) -> Google Flow, driven in the user's own Chrome through the
SoShop Flow Bridge extension (``bridge``, ``cdp``, ``flow``) -> clips stitched
into one video (``assemble``). ``jobs`` keeps each video's state on disk and
``runner`` drives a job end to end inside the API process.
"""
