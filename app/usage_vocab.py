"""Shared usage-channel vocabulary — the single source of truth for the F&B
channels Kevin negotiates against. Imported by both Domain E (licenses) and
Domain H (press) so the channel-overlap seam between them can never silently
rot: a license's granted channels and a press hit's channel are drawn from the
SAME list, so "ran in a channel the license didn't grant" stays a real overlap
check rather than two vocabularies that drift apart.

CHANNELS was originally defined in app/admin/licenses.py; it moved here verbatim
(same values, same order) when Domain H needed it too. licenses.py imports it
from here now — its behavior is unchanged.
"""

# F&B-relevant usage channels — the menu Kevin negotiates against.
CHANNELS = [
    "website",
    "social_organic",
    "social_paid",
    "ooh_billboard",
    "print",
    "pr_editorial",
    "delivery_apps",
    "menu",
    "email",
    "broadcast",
]

# Domain F shoot-production vocab. SHOT_CATEGORIES groups a project's shot list
# the way Kevin frames an F&B shoot; SHOT_PRIORITIES is the must/want/if-time
# triage he works down on the day. Both are validated app-side in
# app/admin/shotlist.py (no SQL CHECK), so the lists can evolve in one place.
SHOT_CATEGORIES = [
    "Hero Dish",
    "Detail",
    "Process",
    "Drinks",
    "Ingredients",
    "Interior",
    "Team",
    "Ambiance",
]
SHOT_PRIORITIES = ["must", "want", "if-time"]

# Deliverable units for a project's contracted deliverable spec (Domain F). Same shape as the
# retainer quota units, but a project deliverable is the ONE-OFF spec for a shoot ("25 hero images,
# 5 reels, 1 social-crop ZIP") rather than a recurring monthly commitment. App-validated in
# app/admin/deliverables.py (no SQL CHECK), so the list evolves in one place.
DELIVERABLE_UNITS = [
    "images",
    "reels",
    "videos",
    "stories",
    "carousels",
    "posts",
    "hours",
    "files",
    "other",
]
