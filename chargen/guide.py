"""Side-view composition guides (img2img start images).

SDXL ignores "side view" for most characters (about 1 in 10 in the acceptance test). Starting the sampler from a
grey mannequin seen from the side and facing right fixes the camera and the facing; a high denoise lets the model
redraw everything else. A single mannequin also forces its body shape onto every character, so the guide is drawn
procedurally for a build chosen from the spec (body type, species).
Deterministic: the same build always gives the same image (its hash goes into the metadata).
"""

import hashlib
import io
import re

from PIL import Image, ImageDraw, ImageFilter

from .config import TOOL_ROOT

GUIDE_DIR = TOOL_ROOT / "references" / "guides"

# head: head height as a share of the figure; width: body DEPTH in profile (back to chest) as a share of the figure
# height; legs: leg length share; limb: arm/leg thickness share.
# Depth stays narrow for every build (0.15-0.21): a guide wider than ~0.25 of the height is read as a front view
# (acceptance test: 0.29-0.33 wide guides gave front views for 4 of 4 wide characters, 0.19 gave side views 8 of 8).
# Bulk comes from the prompt ("stocky", "chubby"); the guide only fixes the camera, facing and rough proportions.
BUILDS = {
    "slim":   dict(head=0.20, width=0.15, legs=0.42, limb=0.06),
    "normal": dict(head=0.21, width=0.18, legs=0.40, limb=0.07),
    "broad":  dict(head=0.19, width=0.20, legs=0.39, limb=0.08),
    "stocky": dict(head=0.24, width=0.21, legs=0.32, limb=0.085),  # dwarves: big head, short legs
    "small":  dict(head=0.26, width=0.18, legs=0.34, limb=0.065),  # goblins, children: big head, short limbs
    "robed":  dict(head=0.20, width=0.18, legs=0.40, limb=0.07, robe=True),
}

_RULES = [
    ("stocky", r"\b(dwarf|dwarven|stocky|squat|stout)\b"),
    ("small", r"\b(goblin|gnome|halfling|kobold|child|kid|tiny|small)\b"),
    ("broad", r"\b(muscular|huge|burly|broad|bulky|heavy|fat|chubby|big|giant|ogre|orc|troll|barbarian)\b"),
    ("slim", r"\b(slim|slender|thin|skinny|lanky|lean|bony|skeleton|elf|elven)\b"),
]
_ROBES = r"\b(robe|robes|gown|dress|cloak to the floor|long coat)\b"


def choose_build(spec):
    """Build name from the spec ('guide_build' wins; otherwise keywords in body type, species, role, clothing)."""
    if spec.get("guide_build"):
        if spec["guide_build"] not in BUILDS:
            raise ValueError(f"guide_build must be one of {', '.join(BUILDS)}")
        return spec["guide_build"]
    body = " ".join(str(spec.get(k, "")) for k in ("body_type", "species", "role")).lower()
    clothing = " ".join(spec.get("clothing", [])).lower()
    for build, pattern in _RULES:
        if build in ("broad", "slim") and re.search(_ROBES, clothing):
            return "robed"  # a floor-length robe defines the silhouette more than the build under it
        if re.search(pattern, body):
            return build
    return "robed" if re.search(_ROBES, clothing) else "normal"


def draw_guide(build, size=1024, head_scale=1.0):
    """A grey figure in strict profile facing right. Every part carries a side-view cue, because a wide or
    symmetric silhouette is read as a front or back view: face profile (nose, chin), one eye, ear, hair at the
    back of the head, flat back with chest/belly forward, stride with long feet pointing right, near arm in front,
    lighter front and darker back."""
    p = dict(BUILDS[build])
    if head_scale != 1.0:  # bigger head = chibi-like proportions (the player's head is ~30 % of its height)
        extra = p["head"] * (head_scale - 1.0)
        p["head"] = p["head"] * head_scale
        p["legs"] = max(0.2, p["legs"] - extra * 0.6)
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    front, back, far, dark = (140, 138, 136), (112, 110, 110), (88, 86, 88), (55, 52, 52)
    top, bottom = 100, 912
    h = bottom - top
    cx = size // 2
    head_h = p["head"] * h
    head_w = head_h * 0.92
    depth = p["width"] * h            # body depth (back to front) in profile
    limb = p["limb"] * h
    leg_top = bottom - p["legs"] * h
    neck = top + head_h
    shoulder = neck + 0.02 * h
    foot_h = 0.045 * h
    stride = 0.55 * limb + 0.03 * h
    back_x = cx - depth * 0.45        # flat back
    front_x = cx + depth * 0.55       # chest / belly forward

    def leg(x_hip, x_foot, color):
        d.polygon([(x_hip - limb * 0.55, leg_top), (x_hip + limb * 0.55, leg_top),
                   (x_foot + limb * 0.5, bottom - foot_h), (x_foot - limb * 0.5, bottom - foot_h)], fill=color)
        d.polygon([(x_foot - limb * 0.55, bottom - foot_h), (x_foot + limb * 1.6, bottom - foot_h * 0.6),
                   (x_foot + limb * 1.7, bottom), (x_foot - limb * 0.55, bottom)], fill=color)  # foot points right

    if p.get("robe"):
        leg(cx - stride * 0.3, cx - stride * 0.6, far)
        d.polygon([(back_x, leg_top - 0.08 * h), (front_x, leg_top - 0.08 * h),
                   (front_x + depth * 0.25, bottom - foot_h * 1.2), (back_x - depth * 0.35, bottom - foot_h * 1.2)],
                  fill=back)
        d.polygon([(cx, leg_top - 0.08 * h), (front_x, leg_top - 0.08 * h),
                   (front_x + depth * 0.25, bottom - foot_h * 1.2), (cx + depth * 0.05, bottom - foot_h * 1.2)],
                  fill=front)
        d.polygon([(cx + depth * 0.2, bottom - foot_h * 1.3), (cx + depth * 0.2 + limb * 2.0, bottom - foot_h * 0.5),
                   (cx + depth * 0.2 + limb * 2.0, bottom), (cx + depth * 0.2, bottom)], fill=far)  # toe out of the hem
    else:
        leg(cx - depth * 0.05, cx - stride, far)           # back leg behind
        leg(cx + depth * 0.05, cx + stride, back)          # front leg forward
    # far arm, torso (flat back, forward chest/belly), near arm in front
    arm_end = leg_top + 0.03 * h
    d.polygon([(cx - limb * 0.4, shoulder + 0.02 * h), (cx + limb * 0.5, shoulder + 0.02 * h),
               (cx - limb * 0.3, arm_end), (cx - limb * 1.2, arm_end - 0.02 * h)], fill=far)
    belly = depth * (0.12 if build in ("broad", "stocky") else 0.04)
    torso_bottom = leg_top + 0.04 * h
    d.polygon([(back_x, shoulder), (cx + depth * 0.3, shoulder - 0.005 * h), (front_x, shoulder + 0.1 * h),
               (front_x + belly, (shoulder + torso_bottom) / 2 + 0.05 * h), (front_x - depth * 0.05, torso_bottom),
               (back_x + depth * 0.05, torso_bottom), (back_x - depth * 0.04, (shoulder + torso_bottom) / 2)], fill=back)
    d.polygon([(cx + depth * 0.05, shoulder), (cx + depth * 0.3, shoulder - 0.005 * h), (front_x, shoulder + 0.1 * h),
               (front_x + belly, (shoulder + torso_bottom) / 2 + 0.05 * h), (front_x - depth * 0.05, torso_bottom),
               (cx + depth * 0.05, torso_bottom)], fill=front)
    d.polygon([(cx - limb * 0.3, shoulder + 0.03 * h), (cx + limb * 0.7, shoulder + 0.03 * h),
               (cx + limb * 1.4, arm_end - 0.01 * h), (cx + limb * 0.5, arm_end + 0.01 * h)], fill=front)
    d.ellipse([cx + limb * 0.4, arm_end - limb * 0.6, cx + limb * 1.6, arm_end + limb * 0.6], fill=front)  # hand
    # neck and head in profile: face to the right, hair at the back
    hx = cx + depth * 0.12
    d.polygon([(hx - head_w * 0.2, neck - 0.03 * h), (hx + head_w * 0.18, neck - 0.03 * h),
               (hx + head_w * 0.22, shoulder + 0.01 * h), (hx - head_w * 0.25, shoulder + 0.01 * h)], fill=back)
    d.ellipse([hx - head_w / 2, top, hx + head_w / 2, neck], fill=front)
    d.pieslice([hx - head_w / 2, top, hx + head_w / 2, neck], 120, 300, fill=dark)          # hair, back of head
    fy = top + head_h * 0.5
    d.polygon([(hx + head_w * 0.42, fy - head_h * 0.18), (hx + head_w * 0.62, fy + head_h * 0.02),
               (hx + head_w * 0.47, fy + head_h * 0.08), (hx + head_w * 0.5, fy + head_h * 0.22),
               (hx + head_w * 0.3, fy + head_h * 0.42), (hx + head_w * 0.2, fy + head_h * 0.1)], fill=front)  # nose, chin
    d.ellipse([hx + head_w * 0.22, fy - head_h * 0.1, hx + head_w * 0.32, fy], fill=dark)     # one eye
    d.ellipse([hx - head_w * 0.12, fy - head_h * 0.06, hx + head_w * 0.02, fy + head_h * 0.14], fill=back)  # ear
    return img.filter(ImageFilter.GaussianBlur(2))


def guide_file(build, head_scale=1.0):
    """Writes (once) and returns the path of the guide PNG for a build, plus its sha256."""
    GUIDE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if head_scale == 1.0 else f"_head{int(round(head_scale * 100))}"
    path = GUIDE_DIR / f"side_right_{build}{suffix}.png"
    buf = io.BytesIO()
    draw_guide(build, head_scale=head_scale).save(buf, format="PNG")
    data = buf.getvalue()
    if not path.exists() or path.read_bytes() != data:
        path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()
