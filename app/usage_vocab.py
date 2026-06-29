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

# Canned deliverable-spec templates for commercial intake. Cloning a template creates normal
# audited project_deliverables rows; it does not deliver files, charge, send, or publish anything.
DELIVERABLE_TEMPLATES = {
    "menu_stills": {
        "label": "Menu stills",
        "deliverables": [
            {
                "label": "Edited hero/detail images",
                "spec_qty": 25,
                "unit": "images",
                "spec_format": "JPEG sRGB",
                "sort_order": 10,
                "note": "Final retouched selects from the menu shoot.",
            },
            {
                "label": "Social crop pack",
                "spec_qty": 1,
                "unit": "files",
                "spec_format": "ZIP, 4:5 and 9:16 crops",
                "sort_order": 20,
                "note": "Cropped exports for feed and stories.",
            },
            {
                "label": "Web gallery",
                "spec_qty": 1,
                "unit": "files",
                "spec_format": "PIN-gated gallery",
                "sort_order": 30,
                "note": "Client-facing gallery/download hub.",
            },
        ],
    },
    "hero_reels": {
        "label": "Hero + reels",
        "deliverables": [
            {
                "label": "Hero images",
                "spec_qty": 15,
                "unit": "images",
                "spec_format": "JPEG sRGB",
                "sort_order": 10,
                "note": "Campaign-ready stills.",
            },
            {
                "label": "Short-form reels",
                "spec_qty": 3,
                "unit": "reels",
                "spec_format": "Vertical MP4, 9:16",
                "sort_order": 20,
                "note": "Edited vertical motion deliverables.",
            },
            {
                "label": "Cover stills",
                "spec_qty": 3,
                "unit": "images",
                "spec_format": "JPEG sRGB, 9:16",
                "sort_order": 30,
                "note": "Poster/thumbnail stills for each reel.",
            },
        ],
    },
    "print_web": {
        "label": "Print + web",
        "deliverables": [
            {
                "label": "Web-ready images",
                "spec_qty": 20,
                "unit": "images",
                "spec_format": "JPEG sRGB",
                "sort_order": 10,
                "note": "Optimized for web, social, and email use.",
            },
            {
                "label": "Print files",
                "spec_qty": 10,
                "unit": "files",
                "spec_format": "CMYK TIFF, 300dpi",
                "sort_order": 20,
                "note": "Press/menu-ready print exports.",
            },
            {
                "label": "Usage-rights summary",
                "spec_qty": 1,
                "unit": "files",
                "spec_format": "Licence record",
                "sort_order": 30,
                "note": "Structured licence terms recorded in Mise.",
            },
        ],
    },
}

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

# Canned commercial shot-list templates for intake. Cloning a template creates normal
# audited shot_list rows; it does not sync, publish, or overwrite project-specific edits.
SHOT_TEMPLATES = {
    "hero_detail": {
        "label": "Hero + detail",
        "shots": [
            {
                "title": "Plated hero, three-quarter",
                "category": "Hero Dish",
                "priority": "must",
                "sort_order": 10,
                "note": "Primary campaign/menu image.",
            },
            {
                "title": "Overhead hero",
                "category": "Hero Dish",
                "priority": "want",
                "sort_order": 20,
                "note": "Layout-friendly alternate crop.",
            },
            {
                "title": "Texture/detail close-up",
                "category": "Detail",
                "priority": "want",
                "sort_order": 30,
                "note": "Sauce, garnish, crumb, steam, or pour.",
            },
            {
                "title": "Chef/action process",
                "category": "Process",
                "priority": "if-time",
                "sort_order": 40,
                "note": "Hands, plating, flame, pour, or finish.",
            },
            {
                "title": "Room/table context",
                "category": "Ambiance",
                "priority": "if-time",
                "sort_order": 50,
                "note": "Hospitality context for social or web.",
            },
        ],
    },
    "menu_three_part": {
        "label": "Menu 3-part",
        "shots": [
            {
                "title": "Full menu lineup",
                "category": "Hero Dish",
                "priority": "must",
                "sort_order": 10,
                "note": "Set the seasonal/menu story in one frame.",
            },
            {
                "title": "Hero entree",
                "category": "Hero Dish",
                "priority": "must",
                "sort_order": 20,
                "note": "Strong single-dish anchor.",
            },
            {
                "title": "Drink pairing",
                "category": "Drinks",
                "priority": "want",
                "sort_order": 30,
                "note": "Cocktail, wine, coffee, or N/A pairing.",
            },
            {
                "title": "Ingredient/process detail",
                "category": "Detail",
                "priority": "want",
                "sort_order": 40,
                "note": "Craft cue that makes the menu feel specific.",
            },
            {
                "title": "Interior or table context",
                "category": "Ambiance",
                "priority": "if-time",
                "sort_order": 50,
                "note": "Use when the venue/environment is part of the sell.",
            },
        ],
    },
}

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
