"""Character spec: the structured description of a character.

A spec is a plain dict (saved in character.json next to the sprite). Claude (the skill) normally fills it
from the user's words; `parse_description` is a deterministic keyword-based fallback for plain CLI use.
"""

import re

# Field order is also the order shown in character.json.
TEXT_FIELDS = [
    "name", "role", "species", "gender", "age", "body_type", "face", "hair", "expression",
    "pose", "view", "proportions", "setting", "art_style", "palette", "background",
]
LIST_FIELDS = ["clothing", "armor", "weapons", "accessories", "colors", "details", "avoid"]

DEFAULTS = {
    "species": "human",
    "pose": "standing idle pose",
    "view": "side",
    "proportions": "normal game proportions",
    "art_style": "16-bit",
    "palette": "limited color palette",
    "background": "plain white background",
}

VIEWS = {
    "side": "(side view:1.4), (from side:1.3), profile, facing right",
    "front": "front view, facing the viewer",
    "three-quarter": "three-quarter view, facing right",
    "back": "back view",
}

_GENDER = {
    "female": "female", "woman": "female", "girl": "female", "lady": "female",
    "male": "male", "man": "male", "boy": "male", "guy": "male",
}
_AGE = ["young adult", "young", "teenage", "teen", "child", "kid", "adult", "middle-aged", "old", "elderly", "aged", "ancient"]
_SPECIES = ["human", "elf", "dwarf", "orc", "goblin", "halfling", "gnome", "skeleton", "undead", "zombie", "demon",
            "vampire", "werewolf", "lizardfolk", "catfolk", "beastfolk", "troll", "ogre", "robot", "android", "slime"]
_ROLES = ["rogue", "thief", "assassin", "knight", "paladin", "warrior", "fighter", "barbarian", "mage", "wizard",
          "sorcerer", "witch", "warlock", "necromancer", "cleric", "priest", "monk", "ranger", "archer", "hunter",
          "druid", "bard", "samurai", "ninja", "pirate", "soldier", "guard", "merchant", "villager", "farmer",
          "blacksmith", "king", "queen", "prince", "princess", "bandit", "mercenary", "shaman", "alchemist"]
_WEAPON = ["dagger", "knife", "sword", "blade", "katana", "axe", "mace", "hammer", "spear", "lance", "halberd",
           "bow", "crossbow", "staff", "wand", "scythe", "whip", "shield", "rapier", "sabre", "saber", "club",
           "flail", "pistol", "musket", "gun", "sickle", "trident"]
_ARMOR = ["armor", "armour", "chainmail", "mail", "plate", "breastplate", "pauldron", "gauntlet", "greaves", "helmet", "helm", "cuirass", "brigandine"]
_CLOTHING = ["hood", "cloak", "cape", "robe", "tunic", "shirt", "vest", "coat", "jacket", "dress", "skirt",
             "pants", "trousers", "boots", "shoes", "sandals", "gloves", "hat", "scarf", "belt", "tabard", "mask", "bandana"]
_ACCESSORY = ["necklace", "amulet", "ring", "earring", "pouch", "bag", "backpack", "satchel", "quiver", "lantern", "book",
              "tome", "potion", "bracelet", "crown", "tiara", "feather", "goggles", "glasses"]
_HAIR = ["hair", "ponytail", "braid", "braids", "bun", "bald", "mohawk", "beard", "moustache", "mustache"]
_FACE = ["eyes", "scar", "freckles", "face", "nose", "ears", "tattoo", "skin", "complexion"]
_BODY = ["slim", "slender", "athletic", "muscular", "stocky", "chubby", "fat", "thin", "tall", "short body", "petite", "lanky", "burly", "body", "build"]
_EXPRESSION = ["expression", "smile", "smiling", "grin", "frown", "angry", "confident", "smug", "serious", "sad", "happy", "determined", "calm", "fierce"]
_SETTING = ["fantasy", "medieval", "sci-fi", "cyberpunk", "steampunk", "modern", "post-apocalyptic", "setting", "world"]
_POSE = ["pose", "standing", "crouching", "running", "walking", "attacking", "idle"]
_COLOR_WORDS = ["red", "green", "blue", "yellow", "orange", "purple", "violet", "pink", "brown", "black", "white",
                "grey", "gray", "gold", "golden", "silver", "teal", "cyan", "crimson", "navy", "beige", "tan"]
_FILLER = re.compile(r"\b(game character|character|sprite|pixel art|pixel-art)\b", re.I)


def _has(phrase, words):
    return any(re.search(r"\b" + re.escape(w) + r"s?\b", phrase) for w in words)


def _split(description):
    parts = re.split(r"[,;\n]+|\.\s+|\.$|\band\b(?=\s+(?:a|an|the|with)\b)", description)
    return [p.strip(" .") for p in parts if p and p.strip(" .")]


def new_spec(**fields):
    spec = {f: "" for f in TEXT_FIELDS}
    spec.update({f: [] for f in LIST_FIELDS})
    for key, value in DEFAULTS.items():
        spec[key] = value
    for key, value in fields.items():
        if value is not None:
            spec[key] = value
    return spec


def parse_description(description, name=None):
    """Keyword-based description -> spec. Deterministic; unknown phrases go to 'details'."""
    spec = new_spec(name=name or "")
    spec["description"] = description.strip()
    for raw in _split(description):
        phrase = raw.lower()
        words = re.findall(r"[a-z-]+", phrase)
        if "color" in phrase or "colour" in phrase or (words and all(w in _COLOR_WORDS + ["and", "dark", "light"] for w in words)):
            spec["colors"].append(re.sub(r"\s*colou?r(s| scheme)?$", "", raw, flags=re.I))
        elif _has(phrase, _HAIR):
            spec["hair"] = raw
        elif _has(phrase, _EXPRESSION):
            spec["expression"] = re.sub(r"\s*expression$", "", raw, flags=re.I)
        elif _has(phrase, _ARMOR):
            spec["armor"].append(raw)
        elif _has(phrase, _WEAPON):
            spec["weapons"].append(raw)
        elif _has(phrase, _CLOTHING):
            spec["clothing"].append(raw)
        elif _has(phrase, _ACCESSORY):
            spec["accessories"].append(raw)
        elif _has(phrase, _BODY) and not _has(phrase, _ROLES + list(_GENDER)):
            spec["body_type"] = re.sub(r"\s*(body|build)$", "", raw, flags=re.I)
        elif _has(phrase, _FACE):
            spec["face"] = raw
        elif _has(phrase, _SETTING) and not _has(phrase, _ROLES):
            spec["setting"] = _FILLER.sub("", raw).strip()
        elif _has(phrase, _POSE):
            spec["pose"] = raw
        elif _has(phrase, list(_GENDER) + _AGE + _SPECIES + _ROLES):
            _parse_identity(raw, spec)
        else:
            cleaned = _FILLER.sub("", raw).strip()
            if cleaned:
                spec["details"].append(cleaned)
    if not spec["name"]:
        spec["name"] = (spec["role"] or spec["species"] or "character").title().replace(" ", "")
    return spec


def _parse_identity(raw, spec):
    words = raw.split()
    rest = []
    i = 0
    while i < len(words):
        w = words[i].lower().strip(".")
        two = (w + " " + words[i + 1].lower()) if i + 1 < len(words) else ""
        if two in _AGE:
            spec["age"] = two
            i += 2
            continue
        if w in _GENDER:
            spec["gender"] = _GENDER[w]
        elif w in _AGE:
            spec["age"] = w
        elif w in _SPECIES:
            spec["species"] = w
        elif w in _ROLES:
            spec["role"] = (spec["role"] + " " + w).strip()
        else:
            rest.append(words[i])
        i += 1
    if rest:
        spec["details"].append(" ".join(rest))


def normalize(spec):
    """Fill defaults, coerce types, validate the view. Returns a new dict."""
    out = new_spec()
    for key, value in spec.items():
        if key in LIST_FIELDS:
            out[key] = [v for v in (value if isinstance(value, list) else [value]) if v]
        elif value is not None:
            out[key] = value
    if out["view"] not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}; got '{out['view']}'")
    if not out.get("name"):
        out["name"] = (out.get("role") or out.get("species") or "character").title().replace(" ", "")
    return out


def safe_name(name):
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    if not cleaned:
        raise ValueError(f"invalid character name: '{name}'")
    return cleaned
