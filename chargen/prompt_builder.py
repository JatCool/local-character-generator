"""Spec + profile -> positive/negative prompt.

The wording lives in prompts/*.txt templates (one segment per line). A segment is dropped when every
placeholder in it is empty, so missing fields never leave dangling words. Segment order is fixed, which
keeps prompts for different characters structurally identical (good for a consistent look).
"""

import re

from .config import TOOL_ROOT
from .spec import VIEWS

PROMPTS_DIR = TOOL_ROOT / "prompts"
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# Views the model must avoid for the requested view (it drifts to back/three-quarter views otherwise).
VIEW_NEGATIVE = {
    "side": "(front view:1.3), (facing viewer:1.3), (looking at viewer:1.2), back view, from behind",
    "front": "back view, from behind, side view",
    "three-quarter": "back view, from behind",
    "back": "",
}


def _join(items):
    return ", ".join(i for i in items if i)


# LLM text encoders follow an explicit description of the camera better than tags.
VIEWS_NATURAL = {
    "side": "a strict side view (profile, facing right) like a side-scrolling platformer sprite, "
            "the body turned 90 degrees to the right, only one eye visible",
    "front": "a front view, facing the viewer",
    "three-quarter": "a three-quarter view, facing right",
    "back": "a back view, seen from behind",
}


def _values(spec, natural=False):
    gender = (spec.get("gender") or "").lower()
    pronoun = {"female": "she", "male": "he"}.get(gender, "they")
    subject = " ".join(p for p in [spec.get("age"), gender, spec.get("species"), spec.get("role")] if p)
    return {
        "style": spec.get("art_style", ""),
        "subject": re.sub(r"\s+", " ", subject).strip() or "character",
        "view": (VIEWS_NATURAL if natural else VIEWS)[spec.get("view", "side")],
        "body_type": spec.get("body_type", ""),
        "hair": spec.get("hair", ""),
        "face": spec.get("face", ""),
        "expression": spec.get("expression", ""),
        "clothing": _join(spec.get("clothing", [])),
        "armor": _join(spec.get("armor", [])),
        "weapons": _join(spec.get("weapons", [])),
        "accessories": _join(spec.get("accessories", [])),
        "colors": _join(spec.get("colors", [])),
        "pose": spec.get("pose", ""),
        "proportions": spec.get("proportions", ""),
        "setting": spec.get("setting", ""),
        "palette": spec.get("palette", ""),
        "background": spec.get("background", ""),
        "details": _join(spec.get("details", [])),
        "avoid": _join(spec.get("avoid", [])),
        "view_negative": VIEW_NEGATIVE.get(spec.get("view", "side"), ""),
        "Pronoun": pronoun.capitalize(),
        "has": "have" if pronoun == "they" else "has",
        "is": "are" if pronoun == "they" else "is",
    }


def render_template(text, values, joiner):
    segments = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys = _PLACEHOLDER.findall(line)
        content_keys = [k for k in keys if k not in ("Pronoun", "has", "is")]
        if content_keys and not any(values.get(k) for k in content_keys):
            continue
        segments.append(_PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), line))
    text = joiner.join(segments)
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+([,.])", r"\1", text).strip(" ,")


def load_prompt_file(name):
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def build(spec, profile, variation=None):
    """Returns (positive, negative)."""
    style = profile.get("prompt_style", "tags")
    joiner = ", " if style == "tags" else " "
    values = _values(spec, natural=(style == "natural"))
    body = render_template(load_prompt_file(f"{style}.txt"), values, joiner)
    parts = [profile.get("prompt_prefix", ""), body]
    if variation:
        parts.append(variation)
    parts.append(profile.get("prompt_suffix", ""))
    positive = joiner.join(p.strip() for p in parts if p and p.strip())
    negative = ""
    if profile.get("negative_file"):
        negative = render_template(load_prompt_file(profile["negative_file"]), values, ", ")
    return positive, negative
