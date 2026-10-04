"""Rig data for an approved side-view character: joints, per-pixel body-part map and hidden-pixel underlay.

Everything is computed on the 1024 px source image (`references/source_raw.png`) and then sampled onto exactly the
same pixel grid as the sprite (pixelate layers), so the data lines up with the sprite (`<Name>.png`) pixel for pixel. The
sprite itself is never changed. Output (next to the recipe), format `chargen-rig/1` (docs/rig-data.md):

    rig/rig.json          joints, decisions, checks, file list, hashes, and the recipe of this step
    rig/joints.txt        the same joints in the plain "Name x y" format (one joint per line, 'ground y')
    rig/parts.png         part bitmask per pixel: R = bits 0-7, G = bits 8-15, A = 255 where the sprite is opaque
    rig/parts_preview.png colour picture of the part map (x8), for people
    rig/underlay.png      pixels hidden behind the near arm / weapon, reconstructed (transparent elsewhere)
    rig/source/           keypoints.json (SDPose output), arm_mask.png and inpaint_raw.png (1024 px)

Steps: SDPose keypoints -> near/far limbs -> part labels (capsules around the measured limbs, robe rule, weapon by
connectivity to the near hand) -> inpaint the near arm + weapon with the character's model, prompt and seed ->
sample labels and the inpainted image onto the sprite grid with the sprite's own palette -> keep held items attached to
their hand on that grid (attach_held_items) -> joints in sprite pixels.
"""

import datetime as dt
import hashlib
import io
import json
import re
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import __version__, pixelate, workflow as wf
from .comfy import ComfySession
from .config import TOOL_ROOT, load_models
from .generator import GenerationError, _log, get_profile, postprocess, sprite_path
from .guide import BUILDS

FORMAT = "chargen-rig/1"
PARTS = ["Body", "Head", "Hair", "ArmNearUpper", "ArmNearLower", "Weapon", "ArmFarUpper", "ArmFarLower",
         "LegNearUpper", "LegNearLower", "FootNear", "LegFarUpper", "LegFarLower", "FootFar"]
BIT = {name: 1 << i for i, name in enumerate(PARTS)}
JOINTS = ["Neck", "HairPivot", "Hip", "ShoulderNear", "ElbowNear", "HandNear", "ShoulderFar", "ElbowFar", "HandFar",
          "KneeNear", "FootNear", "KneeFar", "FootFar", "WeaponPivot", "WeaponTip"]
PART_COLORS = [(200, 60, 60), (60, 160, 220), (240, 200, 40), (90, 200, 90), (40, 120, 40), (250, 120, 0),
               (180, 90, 200), (110, 40, 140), (0, 200, 200), (0, 120, 140), (0, 70, 90), (230, 100, 170),
               (150, 60, 100), (100, 40, 70)]
# OpenPose body-18
NOSE, NECK, RSH, REL, RWR, LSH, LEL, LWR, RHIP, RKNEE, RANK, LHIP, LKNEE, LANK, REYE, LEYE, REAR, LEAR = range(18)
_ROBE = re.compile(r"\b(robe|robes|gown|dress|cassock|habit|long coat|cloak to the floor)\b", re.I)


# ----------------------------------------------------------------------------------------------- geometry helpers

def _seg_dist(xx, yy, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = ((xx - ax) * dx + (yy - ay) * dy) / max(1e-6, dx * dx + dy * dy)
    t = np.clip(t, 0.0, 1.0)
    return np.hypot(xx - (ax + t * dx), yy - (ay + t * dy))


def _components_touching(mask, seed, work=256):
    """Pixels of `mask` in 8-connected components (measured at low resolution) that touch `seed`."""
    h, w = mask.shape
    small = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).resize((work, work), Image.Resampling.NEAREST)) > 127
    sseed = np.asarray(Image.fromarray((seed * 255).astype(np.uint8)).resize((work, work), Image.Resampling.BOX)) > 0
    keep = np.zeros_like(small)
    frontier = small & sseed
    keep |= frontier
    while frontier.any():
        grown = np.zeros_like(frontier)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                grown |= np.roll(np.roll(frontier, dy, 0), dx, 1)
        frontier = grown & small & ~keep
        keep |= frontier
    big = np.asarray(Image.fromarray((keep * 255).astype(np.uint8)).resize((w, h), Image.Resampling.NEAREST)) > 127
    return big & mask


# ----------------------------------------------------------------------------------------------- keypoints

def parse_keypoints(text):
    data = json.loads(text) if isinstance(text, str) else text
    frame = data[0] if isinstance(data, list) else data
    people = frame.get("people") or []
    if not people:
        raise GenerationError("SDPose found no person in the source image")
    flat = people[0]["pose_keypoints_2d"]
    return [(float(flat[i * 3]), float(flat[i * 3 + 1]), float(flat[i * 3 + 2])) for i in range(len(flat) // 3)]


def run_sdpose(cfg, client, raw_path, run_dir):
    rc = cfg.data["rig"]
    template, wf_hash = wf.load(rc["sdpose_workflow"])
    name = client.upload_image(raw_path, subfolder="chargen_rig")
    api = wf.fill(template, {"sdpose_model": rc["sdpose_model"], "image": name})
    (run_dir / "sdpose_workflow_api.json").write_text(json.dumps(api, indent=2), encoding="utf-8")
    entry = client.wait(client.queue(api), cfg.generation_timeout)
    texts = client.output_texts(entry)
    if not texts:
        raise GenerationError("SDPose returned no keypoints (is the SDPose model installed? models install --group rig)")
    return texts[0], wf_hash


# ----------------------------------------------------------------------------------------------- analysis

class Skeleton:
    """Keypoints in source pixels with near/far sides.

    The near side (facing the viewer) is the body side SDPose sees best: in profile its shoulder, elbow, wrist and ear
    score clearly higher than the hidden side, whose positions are guesses (often placed in front of the body).
    Near arm and near leg are on the same body side."""

    def __init__(self, kps, forward, min_conf):
        self.kps = kps
        self.min_conf = min_conf
        p = lambda i: np.array(kps[i][:2])  # noqa: E731
        self.conf = {i: kps[i][2] for i in range(len(kps))}
        right_arm, left_arm = (p(RSH), p(REL), p(RWR)), (p(LSH), p(LEL), p(LWR))
        right_leg, left_leg = (p(RHIP), p(RKNEE), p(RANK)), (p(LHIP), p(LKNEE), p(LANK))
        score = lambda ids: float(np.mean([kps[i][2] for i in ids]))  # noqa: E731
        self.side_scores = {"right": round(score((RSH, REL, RWR, REAR)), 3), "left": round(score((LSH, LEL, LWR, LEAR)), 3)}
        r_first = self.side_scores["right"] >= self.side_scores["left"]
        self.near_arm, self.far_arm = (right_arm, left_arm) if r_first else (left_arm, right_arm)
        self.near_arm_ids = (RSH, REL, RWR) if r_first else (LSH, LEL, LWR)
        self.far_arm_ids = (LSH, LEL, LWR) if r_first else (RSH, REL, RWR)
        self.near_leg, self.far_leg = (right_leg, left_leg) if r_first else (left_leg, right_leg)
        self.near_leg_ids = (RHIP, RKNEE, RANK) if r_first else (LHIP, LKNEE, LANK)
        self.far_leg_ids = (LHIP, LKNEE, LANK) if r_first else (RHIP, RKNEE, RANK)
        self.neck = p(NECK)
        self.hip = (p(RHIP) + p(LHIP)) / 2
        self.ear = (p(REAR) + p(LEAR)) / 2
        self.forward = forward

    def weak(self, ids):
        return [i for i in ids if self.conf.get(i, 0) < self.min_conf]


def analyse(cfg, raw_img, kps, spec, gen):
    """Part labels (uint16 bitmask per source pixel), arm/weapon mask for inpainting, decisions."""
    rc = cfg.data["rig"]
    rgb = raw_img.convert("RGB")
    bg, _ = pixelate.background_mask(rgb)
    fg = pixelate._main_figure(~bg)
    ys, xs = np.nonzero(fg)
    top, bottom = ys.min(), ys.max()
    H = float(bottom - top + 1)
    forward = -1 if gen.get("mirror") else 1          # the raw image faces left when the sprite was mirrored
    sk = Skeleton(kps, forward, float(rc["min_keypoint_confidence"]))
    hh, ww = fg.shape
    yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)

    r_arm, r_hand, r_leg = rc["arm_radius"] * H, rc["hand_radius"] * H, rc["leg_radius"] * H
    torso_half = rc["torso_half_width"] * H
    d_torso = _seg_dist(xx, yy, sk.neck, sk.hip)
    torso = d_torso < torso_half

    # head and hair: above the neck; hair = the part of the head behind the ear
    head = fg & (yy < sk.neck[1])
    behind = (xx - sk.ear[0]) * forward < -0.02 * H
    hair = head & behind

    # near arm (+ hand) first: it is drawn in front of everything
    s, e, w = sk.near_arm
    d_nu, d_nl, d_nh = _seg_dist(xx, yy, s, e), _seg_dist(xx, yy, e, w), np.hypot(xx - w[0], yy - w[1])
    near_arm = fg & ~head & ((d_nu < r_arm) | (d_nl < r_arm) | (d_nh < r_hand))
    near_upper = near_arm & (d_nu <= np.minimum(d_nl, d_nh))

    # legs: strict capsules (used to keep the weapon search off the legs)
    def leg_dist(leg):
        hp, kn, an = leg
        toe = an + np.array([forward * 0.06 * H, 0.0])
        return np.minimum.reduce([_seg_dist(xx, yy, hp, kn), _seg_dist(xx, yy, kn, an), _seg_dist(xx, yy, an, toe)])
    d_near_leg, d_far_leg = leg_dist(sk.near_leg), leg_dist(sk.far_leg)
    leg_capsules = (d_near_leg < r_leg) | (d_far_leg < r_leg)

    # weapon: figure pixels outside head, near arm, torso core and leg capsules, connected to the near hand
    free = fg & ~head & ~near_arm & ~torso & ~leg_capsules
    weapon = _components_touching(free, d_nh < r_hand * 1.6)
    if weapon.sum() < 0.002 * fg.sum():
        weapon[:] = False
    # an item held by the far hand (e.g. a sword carried behind) moves with the far arm
    fs, fe, fw = sk.far_arm
    far_item = _components_touching(free & ~weapon, np.hypot(xx - fw[0], yy - fw[1]) < r_hand * 1.6)
    if far_item.sum() < 0.002 * fg.sum():
        far_item[:] = False

    # robes: the robe stays body; only the feet below the hem become legs
    robed = (gen.get("guide") == "robed") or bool(_ROBE.search(" ".join(spec.get("clothing", []))))
    ankle_y = max(sk.near_leg[2][1], sk.far_leg[2][1])
    knee_near_y, knee_far_y = sk.near_leg[1][1], sk.far_leg[1][1]
    ankle_near_y, ankle_far_y = sk.near_leg[2][1] - 0.01 * H, sk.far_leg[2][1] - 0.01 * H
    near_legs = np.minimum(d_near_leg, d_far_leg) < 2.5 * r_leg      # only pixels around the legs move with them
    if robed:
        leg_zone = fg & (yy > ankle_y - 0.06 * H) & ~weapon & ~near_arm & ~far_item
    else:
        leg_zone = fg & (yy > sk.hip[1]) & near_legs & ~weapon & ~near_arm & ~far_item
    both = np.abs(d_near_leg - d_far_leg) < 0.6 * r_leg
    is_near = d_near_leg <= d_far_leg

    def leg_bits(near):
        k_y, a_y = (knee_near_y, ankle_near_y) if near else (knee_far_y, ankle_far_y)
        names = ("LegNearUpper", "LegNearLower", "FootNear") if near else ("LegFarUpper", "LegFarLower", "FootFar")
        out = np.where(yy < k_y, BIT[names[0]], np.where(yy < a_y, BIT[names[1]], BIT[names[2]]))
        return out.astype(np.uint16)
    near_bits, far_bits = leg_bits(True), leg_bits(False)
    legs = np.where(both, near_bits | far_bits, np.where(is_near, near_bits, far_bits)).astype(np.uint16)

    # far arm: only where it sticks out of the torso (behind the torso it is invisible)
    s, e, w = sk.far_arm
    d_fu, d_fl, d_fh = _seg_dist(xx, yy, s, e), _seg_dist(xx, yy, e, w), np.hypot(xx - w[0], yy - w[1])
    far_arm = fg & ~head & ~near_arm & ~weapon & ~torso & ~leg_zone & ((d_fu < r_arm) | (d_fl < r_arm) | (d_fh < r_hand))
    far_upper = far_arm & (d_fu <= np.minimum(d_fl, d_fh))
    far_arm |= far_item

    labels = np.zeros(fg.shape, dtype=np.uint16)
    labels[fg] = BIT["Body"]
    labels[far_arm] = BIT["ArmFarLower"]
    labels[far_upper] = BIT["ArmFarUpper"]
    labels[leg_zone] = legs[leg_zone]
    labels[head] = BIT["Head"]
    labels[hair] = BIT["Hair"]
    labels[weapon] = BIT["Weapon"]
    labels[near_arm] = BIT["ArmNearLower"]
    labels[near_upper] = BIT["ArmNearUpper"]

    # Torso silhouette behind the arm: per row between the neck and a little below the hip, the span between the
    # outermost body pixels that are not arm/weapon/head. Arm pixels inside it hide torso; outside it they hide background.
    body_px = fg & ~near_arm & ~weapon & ~head & ~far_arm
    hull = np.zeros_like(fg)
    y_lo, y_hi = int(sk.neck[1]), int(min(bottom, sk.hip[1] + 0.12 * H))
    for y in range(max(0, y_lo), max(0, y_hi)):
        xs_row = np.nonzero(body_px[y])[0]
        if len(xs_row) > 1:
            hull[y, xs_row.min():xs_row.max() + 1] = True
    torso = torso | hull

    # what the near arm and weapon cover over the torso is reconstructed (inpainted), grown a little for a clean seam
    cover = (near_arm | weapon) & torso
    grow = int(rc["mask_grow_px"])
    mask_img = Image.fromarray((cover * 255).astype(np.uint8))
    if grow:
        mask_img = mask_img.filter(ImageFilter.MaxFilter(2 * grow + 1))

    decisions = {
        "facing": "right",
        "near_side": "right" if sk.near_arm_ids[0] == RSH else "left",
        "side_confidence": sk.side_scores,
        "robed": robed,
        "weapon_found": bool(weapon.any()),
        "weak_keypoints": [i for i in sk.near_arm_ids + sk.near_leg_ids if sk.conf.get(i, 0) < sk.min_conf],
        "figure_height_source_px": int(H),
    }
    decisions["far_hand_item"] = bool(far_item.any())
    return labels, cover, mask_img, sk, decisions, fg, torso


# ----------------------------------------------------------------------------------------------- inpainting

def prefill(raw_img, mask_img, fg, torso):
    """What the sampler starts from: the masked area inside the torso is filled from the surrounding torso colours
    (diffusion from the mask border), outside the torso with the background colour. Without this the model simply
    redraws the arm that is still visible in the latent."""
    rgb = np.asarray(raw_img.convert("RGB")).astype(np.float32)
    mask = np.asarray(mask_img.convert("L")) > 127
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    inside = mask & torso
    known = fg & ~mask
    out = rgb.copy()
    out[mask & ~torso] = bg
    ys, xs = np.nonzero(inside)
    if len(ys):
        pad = 40
        y0, y1 = max(0, ys.min() - pad), min(rgb.shape[0], ys.max() + pad + 1)
        x0, x1 = max(0, xs.min() - pad), min(rgb.shape[1], xs.max() + pad + 1)
        col = rgb[y0:y1, x0:x1].copy()
        have = known[y0:y1, x0:x1].copy()
        todo = inside[y0:y1, x0:x1]
        for _ in range(400):
            missing = todo & ~have
            if not missing.any():
                break
            acc = np.zeros_like(col)
            cnt = np.zeros(have.shape, dtype=np.float32)
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                sh = np.roll(np.roll(have, dy, 0), dx, 1)
                acc += np.roll(np.roll(col, dy, 0), dx, 1) * sh[..., None]
                cnt += sh
            grow = missing & (cnt > 0)
            col[grow] = acc[grow] / cnt[grow][:, None]
            have |= grow
        out[y0:y1, x0:x1][todo] = col[todo]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def run_inpaint(cfg, client, raw_path, mask_img, recipe, prompt, run_dir, start_img):
    rc = cfg.data["rig"]
    profile = get_profile(rc["inpaint_profile"])
    template, wf_hash = wf.load(rc["inpaint_workflow"])
    mask_path = run_dir / "arm_mask.png"
    mask_img.convert("RGB").save(mask_path)
    start_path = run_dir / "inpaint_start.png"
    start_img.save(start_path)
    image_name = client.upload_image(start_path, subfolder="chargen_rig")
    mask_name = client.upload_image(mask_path, subfolder="chargen_rig_mask")
    positive = f"{prompt['positive']}, {rc['inpaint_positive_hint']}"
    negative = ", ".join(p for p in (prompt["negative"], rc["inpaint_negative_hint"]) if p)
    values = dict(profile["params"])
    values.update(positive=positive, negative=negative, seed=recipe["generation"]["seed"], image=image_name,
                  mask=mask_name, denoise=float(rc["inpaint_denoise"]),
                  prefix=f"chargen_rig/{recipe['name']}_s{recipe['generation']['seed']}")
    api = wf.fill(template, values)
    (run_dir / "inpaint_workflow_api.json").write_text(json.dumps(api, indent=2), encoding="utf-8")
    entry = client.wait(client.queue(api), cfg.generation_timeout)
    images = client.output_images(entry, wf.save_image_nodes(api))
    if not images:
        raise GenerationError("inpainting returned no image")
    return client.fetch_image(images[0]), {"workflow": rc["inpaint_workflow"], "workflow_sha256_16": wf_hash,
                                           "profile": rc["inpaint_profile"], "params": profile["params"],
                                           "seed": recipe["generation"]["seed"], "denoise": float(rc["inpaint_denoise"]),
                                           "positive": positive, "negative": negative,
                                           "mask_grow_px": int(rc["mask_grow_px"]),
                                           "start": "masked area pre-filled: torso colours (diffusion) inside the torso, background outside"}


# ----------------------------------------------------------------------------------------------- sprite space

_N8 = [(dy, dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
# Held items count as attached only through edge contact: a corner-only (diagonal) link is one pixel wide and is lost
# when the part rotates and is resampled, so it is treated as a gap and bridged.
_N4 = [(-1, 0), (0, -1), (0, 1), (1, 0)]


def _pieces(mask, nbrs=_N8):
    """Connected components of a boolean mask (8-connected by default), as lists of (y, x), deterministic order."""
    seen = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    out = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        seen[y, x] = True
        piece, queue = [], deque([(y, x)])
        while queue:
            cy, cx = queue.popleft()
            piece.append((cy, cx))
            for dy, dx in nbrs:
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        out.append(piece)
    return out


def _touching(piece, mask, nbrs=_N4):
    h, w = mask.shape
    return any(0 <= y + dy < h and 0 <= x + dx < w and mask[y + dy, x + dx] for y, x in piece for dy, dx in nbrs)


def _bridge(piece, target, passable, max_gap):
    """Shortest edge-connected path of at most `max_gap` passable pixels from `piece` to a pixel touching `target`
    (breadth-first, fixed neighbour order, so the result is deterministic). Returns (path, reached) or (None, None)."""
    h, w = target.shape
    prev = {p: None for p in piece}
    frontier = list(piece)
    for _ in range(max_gap):
        nxt = []
        for y, x in frontier:
            for dy, dx in _N4:
                q = (y + dy, x + dx)
                if q in prev or not (0 <= q[0] < h and 0 <= q[1] < w) or not passable[q]:
                    continue
                prev[q] = (y, x)
                nxt.append(q)
        for q in nxt:
            for dy, dx in _N4:
                ry, rx = q[0] + dy, q[1] + dx
                if 0 <= ry < h and 0 <= rx < w and target[ry, rx]:
                    path, p = [], q
                    while p is not None and p not in piece:
                        path.append(p)
                        p = prev[p]
                    return path, (ry, rx)
        frontier = nxt
    return None, None


def attach_held_items(parts, max_gap=3):
    """Keep held items and limb pieces attached to the part that moves them, on the sprite's own pixel grid.

    The parts are decided on the 1024 px source and then sampled onto the sprite grid, where a thin link between a hand
    and the item it holds (a strap, a chain, a handle) can end up as a few Body pixels: the item then flies off on its
    own when the arm swings. Rules, applied to the sampled labels in this order:
    1. far hand: the far arm is drawn behind the body and only its visible pixels exist. A far-arm piece without any
       visible upper arm (an item or hand whose arm is hidden behind the torso or legs) has nothing visible to hang
       from and would float when the far arm swings, so it moves with the Body.
    2. arm parts: each arm part (upper/lower, near/far) is one edge-connected piece. A stray fragment away from the
       part's main (largest) piece, e.g. two forearm pixels up at the shoulder, would swing around a joint it is not
       near, so it takes the label most common on its edges (the part it actually sits on), else Body.
    3. near hand: every Weapon piece must share an edge with the near forearm/hand (ArmNearLower) or with a Weapon
       piece that does (a corner-only contact is a one-pixel link that breaks when the item rotates). A gap of at most
       `max_gap` Body or leg pixels (a handle crossing the thigh) is bridged: the bridge pixels move with the hand
       (ArmNearLower) when they lead to the hand, with the item (Weapon) when they join two item pieces. A piece that
       still cannot be reached joins the near arm part it touches (ArmNearUpper), else the Body: it never floats.
    Returns (new parts, report)."""
    out = parts.copy()
    far_to_body = 0
    far = (out & (BIT["ArmFarUpper"] | BIT["ArmFarLower"])) > 0
    for piece in _pieces(far):
        if not any(out[p] & BIT["ArmFarUpper"] for p in piece):
            for p in piece:
                out[p] = BIT["Body"]
            far_to_body += 1
    report_fragments = 0
    for name in ("ArmNearUpper", "ArmNearLower", "ArmFarUpper", "ArmFarLower"):
        pieces = sorted(_pieces(out == BIT[name], _N4), key=len, reverse=True)
        for piece in pieces[1:]:
            h, w = out.shape
            around = [int(out[y + dy, x + dx]) for y, x in piece for dy, dx in _N4
                      if 0 <= y + dy < h and 0 <= x + dx < w and out[y + dy, x + dx] not in (0, BIT[name])]
            new = max(sorted(set(around)), key=around.count) if around else BIT["Body"]
            for p in piece:
                out[p] = new
            report_fragments += 1
    legs = BIT["LegNearUpper"] | BIT["LegNearLower"] | BIT["FootNear"] | BIT["LegFarUpper"] | BIT["LegFarLower"] | BIT["FootFar"]
    passable = (out == BIT["Body"]) | ((out > 0) & ((out & legs) == out))     # Body or leg-only pixels
    report = {"bridged_pixels": 0, "weapon_pieces": 0, "weapon_pieces_reattached": 0, "weapon_pieces_to_body": 0,
              "weapon_pieces_to_upper_arm": 0, "far_pieces_to_body": far_to_body, "arm_fragments_relabelled": report_fragments}

    hand = (out & BIT["ArmNearLower"]) > 0
    weapon = (out & BIT["Weapon"]) > 0
    pieces = _pieces(weapon, _N4)
    report["weapon_pieces"] = len(pieces)
    attached = hand.copy()
    pending = []
    for piece in pieces:
        if _touching(piece, hand):
            for p in piece:
                attached[p] = True
        else:
            pending.append(piece)
    progress = True
    while pending and progress:                      # pieces may attach through other pieces: repeat until stable
        progress = False
        for piece in list(pending):
            if _touching(piece, attached):
                path, reached = [], None
            else:
                path, reached = _bridge(piece, attached, passable & ~attached, max_gap)
                if path is None:
                    continue
            bit = BIT["ArmNearLower"] if reached is not None and hand[reached] else BIT["Weapon"]
            for p in path:
                out[p] = bit
                attached[p] = True
                passable[p] = False
            for p in piece:
                attached[p] = True
            report["bridged_pixels"] += len(path)
            report["weapon_pieces_reattached"] += 1
            pending.remove(piece)
            progress = True
    upper = (out & BIT["ArmNearUpper"]) > 0
    for piece in pending:
        to_upper = _touching(piece, upper)
        for p in piece:
            out[p] = BIT["ArmNearUpper"] if to_upper else BIT["Body"]
        report["weapon_pieces_to_upper_arm" if to_upper else "weapon_pieces_to_body"] += 1

    return out, report


def held_items_attached(parts):
    """True when every Weapon pixel is connected to the near hand through Weapon/ArmNearLower pixels and every far-arm
    piece contains visible upper-arm pixels (the invariant `attach_held_items` establishes)."""
    weapon = (parts & BIT["Weapon"]) > 0
    if weapon.any():
        chain = weapon | ((parts & BIT["ArmNearLower"]) > 0)
        for piece in _pieces(chain, _N4):
            if any(weapon[p] for p in piece) and not any(parts[p] & BIT["ArmNearLower"] for p in piece):
                return False
    far = (parts & (BIT["ArmFarUpper"] | BIT["ArmFarLower"])) > 0
    return all(any(parts[p] & BIT["ArmFarUpper"] for p in piece) for piece in _pieces(far))


def drop_detached_underlay(under, parts):
    """Hidden pixels continue the visible torso. An underlay piece that touches no visible Body pixel (one pixel behind
    a weapon, surrounded by legs) would be left standing alone in front of the legs once the arm moves: drop it.
    Returns (underlay, number of pixels dropped)."""
    out = under.copy()
    body = parts == BIT["Body"]
    dropped = 0
    for piece in _pieces(out[..., 3] > 0, _N4):
        if not _touching(piece, body):
            for p in piece:
                out[p] = 0
            dropped += len(piece)
    return out, dropped


def fill_bridge_underlay(under, sprite, parts, pixels):
    """Hidden pixels for relabelled pixels the inpainting did not cover: the most common Body colour around them
    (the same colours `torso_colours_only` allows). Pixels with no Body neighbour stay transparent (background behind)."""
    out = under.copy()
    h, w = parts.shape
    for y, x in pixels:
        if out[y, x, 3]:
            continue
        neigh = [tuple(int(v) for v in sprite[y + dy, x + dx, :3]) for dy, dx in _N8
                 if 0 <= y + dy < h and 0 <= x + dx < w and parts[y + dy, x + dx] == BIT["Body"] and sprite[y + dy, x + dx, 3]]
        if neigh:
            vals, counts = np.unique(np.array(neigh), axis=0, return_counts=True)
            out[y, x, :3] = vals[np.argmax(counts)]
            out[y, x, 3] = 255
    return out


def torso_colours_only(under, sprite, parts):
    """Hidden pixels may only use colours that occur on the visible torso (Body pixels). Anything else (a hand or
    sleeve the inpainting kept) becomes the most common torso colour among its neighbours, or the nearest torso colour."""
    body = (parts == BIT["Body"]) & (sprite[..., 3] > 0)
    body_cols = {tuple(c) for c in sprite[body][:, :3]}
    if not body_cols:
        return under, 0
    pal = np.array(sorted(body_cols), dtype=np.int32)
    out = under.copy()
    h, w = parts.shape
    replaced = 0
    for y, x in zip(*np.nonzero(under[..., 3] > 0)):
        c = tuple(int(v) for v in under[y, x, :3])
        if c in body_cols:
            continue
        neigh = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w:
                    if under[ny, nx, 3] and tuple(int(v) for v in under[ny, nx, :3]) in body_cols:
                        neigh.append(tuple(int(v) for v in under[ny, nx, :3]))
                    elif body[ny, nx]:
                        neigh.append(tuple(int(v) for v in sprite[ny, nx, :3]))
        if neigh:
            vals, counts = np.unique(np.array(neigh), axis=0, return_counts=True)
            new = vals[np.argmax(counts)]
        else:
            new = pal[np.argmin(((pal - np.array(c)) ** 2).sum(axis=1))]
        out[y, x, :3] = new
        replaced += 1
    return out, replaced


def to_sprite_transform(info, gen):
    """Source pixel -> sprite pixel (continuous, origin top-left, y down), from the post-processing record."""
    x0, y0 = info["character_box_source"][:2]
    cell = info["cell_size_source_px"]
    cw, ch = info["canvas"]
    w, h = info["character_size_px"]
    off = 1 if info["outline"] else 0
    top = ch - gen.get("feet_margin", 0) - h + off
    left = (cw - w) // 2 + off
    mirror = gen.get("mirror", False)

    def f(X, Y):
        x = left + (X - x0) / cell
        y = top + (Y - y0) / cell
        return (cw - x if mirror else x), y
    return f


def weapon_grip(parts, hand):
    """(WeaponPivot, WeaponTip) in sprite pixels from the part map, or None without Weapon pixels. The pivot is the grip:
    the centre of the item pixels that share an edge with the near hand (ArmNearLower), so the item turns where the hand
    holds it and stays in contact (the SDPose wrist is only an estimate and can sit a few pixels off). Without such
    contact, the item pixel nearest to the hand joint. The tip is the item pixel farthest from the pivot."""
    wy, wx = np.nonzero(parts & BIT["Weapon"])
    if not len(wx):
        return None
    pts = np.stack([wx + 0.5, wy + 0.5], axis=1)
    hand_px = (parts & BIT["ArmNearLower"]) > 0
    near = np.zeros_like(hand_px)
    near[1:] |= hand_px[:-1]; near[:-1] |= hand_px[1:]; near[:, 1:] |= hand_px[:, :-1]; near[:, :-1] |= hand_px[:, 1:]
    gy, gx = np.nonzero(near & ((parts & BIT["Weapon"]) > 0))
    if len(gx):
        pivot = np.array([gx.mean() + 0.5, gy.mean() + 0.5])
    else:
        pivot = pts[np.argmin(np.linalg.norm(pts - np.array(hand, dtype=float), axis=1))]
    tip = pts[np.argmax(np.linalg.norm(pts - pivot, axis=1))]
    return tuple(pivot), tuple(tip)


def sprite_joints(sk, kps, transform, parts, feet_row, decisions):
    """Joints in sprite pixels; weapon pivot/tip from the weapon pixels of the part map."""
    T = lambda pt: transform(float(pt[0]), float(pt[1]))  # noqa: E731
    neck, hip, ear = T(sk.neck), T(sk.hip), T(sk.ear)
    sh_n, el_n, wr_n = (T(p) for p in sk.near_arm)
    sh_f, el_f, wr_f = (T(p) for p in sk.far_arm)
    _, kn_n, an_n = (T(p) for p in sk.near_leg)
    _, kn_f, an_f = (T(p) for p in sk.far_leg)
    head_rows = np.nonzero((parts & (BIT["Head"] | BIT["Hair"])).any(axis=1))[0]
    head_top = float(head_rows.min()) if len(head_rows) else neck[1] - 8
    ground_y = feet_row + 1
    joints = {
        "Neck": neck, "HairPivot": (ear[0] - 1.0, head_top + 0.35 * (neck[1] - head_top)), "Hip": hip,
        "ShoulderNear": sh_n, "ElbowNear": el_n, "HandNear": wr_n,
        "ShoulderFar": sh_f, "ElbowFar": el_f, "HandFar": wr_f,
        "KneeNear": kn_n, "FootNear": (an_n[0], min(an_n[1], ground_y - 1.5)),
        "KneeFar": kn_f, "FootFar": (an_f[0], min(an_f[1], ground_y - 1.5)),
    }
    grip = weapon_grip(parts, wr_n)
    if grip:
        joints["WeaponPivot"], joints["WeaponTip"] = grip
    else:
        joints["WeaponPivot"] = joints["WeaponTip"] = wr_n
    wx = np.nonzero(parts & BIT["Weapon"])[1]
    source = {name: "sdpose" for name in joints}
    source["HairPivot"] = "part map + sdpose ears"
    source["WeaponPivot"] = source["WeaponTip"] = "part map (pivot = grip)" if len(wx) else "none (no weapon)"
    conf_ids = {"Neck": [NECK], "Hip": [RHIP, LHIP], "ShoulderNear": [sk.near_arm_ids[0]], "ElbowNear": [sk.near_arm_ids[1]],
                "HandNear": [sk.near_arm_ids[2]], "ShoulderFar": [sk.far_arm_ids[0]], "ElbowFar": [sk.far_arm_ids[1]],
                "HandFar": [sk.far_arm_ids[2]], "KneeNear": [sk.near_leg_ids[1]], "FootNear": [sk.near_leg_ids[2]],
                "KneeFar": [sk.far_leg_ids[1]], "FootFar": [sk.far_leg_ids[2]]}
    out = {}
    for name in JOINTS:
        x, y = joints[name]
        c = min((kps[i][2] for i in conf_ids.get(name, [])), default=None)
        out[name] = {"x": round(float(x), 2), "y": round(float(y), 2), "source": source[name],
                     "confidence": round(float(c), 3) if c is not None else None}
    return out, ground_y


# ----------------------------------------------------------------------------------------------- files

def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _png(arr, mode):
    buf = io.BytesIO()
    Image.fromarray(arr, mode).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def encode_parts(parts, alpha):
    rgba = np.zeros(parts.shape + (4,), dtype=np.uint8)
    rgba[..., 0] = parts & 0xFF
    rgba[..., 1] = parts >> 8
    rgba[..., 3] = np.where(alpha, 255, 0)
    return rgba


def decode_parts(path):
    a = np.asarray(Image.open(path).convert("RGBA")).astype(np.uint16)
    return np.where(a[..., 3] > 0, a[..., 0] | (a[..., 1] << 8), 0).astype(np.uint16)


def parts_preview(parts, scale=8):
    h, w = parts.shape
    img = Image.new("RGBA", (w * scale, h * scale), (30, 30, 30, 255))
    d = ImageDraw.Draw(img)
    for y, x in zip(*np.nonzero(parts)):
        bits = [i for i in range(len(PARTS)) if parts[y, x] >> i & 1]
        d.rectangle([x * scale, y * scale, x * scale + scale - 1, y * scale + scale - 1], fill=PART_COLORS[bits[0]] + (255,))
        if len(bits) > 1:
            q = scale // 3
            d.rectangle([x * scale + q, y * scale + q, x * scale + scale - 1 - q, y * scale + scale - 1 - q],
                        fill=PART_COLORS[bits[1]] + (255,))
    return img


def joints_text(joints, ground_y):
    lines = [f"{n} {j['x']:.2f} {j['y']:.2f}" for n, j in joints.items()]
    return "\n".join(lines + [f"ground {ground_y}"]) + "\n"


# ----------------------------------------------------------------------------------------------- main entry

def build(cfg, name, folder, keep_comfy=False, from_saved=False, underlay=True, write=True):
    """Compute the rig data for a character folder. from_saved = reuse stored keypoints and inpainting (no GPU)."""
    folder = Path(folder)
    recipe = json.loads((folder / "character.json").read_text(encoding="utf-8"))
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    gen, spec = recipe["generation"], recipe["spec"]
    raw_path = folder / "references" / "source_raw.png"
    raw_bytes = raw_path.read_bytes()
    if meta.get("sha256", {}).get("raw") and _sha(raw_bytes) != meta["sha256"]["raw"]:
        raise GenerationError("references/source_raw.png does not match metadata.json")
    sprite_file = sprite_path(folder, recipe["name"])
    sprite_bytes = sprite_file.read_bytes()
    if spec.get("view", "side") != "side":
        raise GenerationError("rig data needs a side-view character (spec view is '%s')" % spec.get("view"))
    prompt = recipe.get("prompt") or {"positive": meta["generated_prompt"], "negative": meta["negative_prompt"]}
    rig_dir = folder / "rig"
    src_dir = rig_dir / "source"
    run_dir = cfg.runs_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{folder.name}_rig"
    run_dir.mkdir(parents=True, exist_ok=True)
    raw_img = Image.open(io.BytesIO(raw_bytes))

    rc = cfg.data["rig"]
    step = {"sdpose_model": rc["sdpose_model"]}
    if from_saved:
        kp_text = (src_dir / "keypoints.json").read_text(encoding="utf-8")
        old = json.loads((rig_dir / "rig.json").read_text(encoding="utf-8"))["recipe"]
        step.update({k: old[k] for k in ("sdpose_workflow", "sdpose_workflow_sha256_16") if k in old})
        inpaint_bytes = (src_dir / "inpaint_raw.png").read_bytes() if underlay and (src_dir / "inpaint_raw.png").exists() else None
        inpaint_step = old.get("inpaint")
        client_ctx = None
    else:
        client_ctx = ComfySession(cfg, keep_running=keep_comfy, log=_log)
    kps = None
    if client_ctx:
        with client_ctx as client:
            client.free()  # different models than the last generation: start clean (ComfyUI cache issue)
            kp_text, sd_hash = run_sdpose(cfg, client, raw_path, run_dir)
            step.update(sdpose_workflow=rc["sdpose_workflow"], sdpose_workflow_sha256_16=sd_hash)
            kps = parse_keypoints(kp_text)
            labels, cover, mask_img, sk, decisions, fg, torso = analyse(cfg, raw_img, kps, spec, gen)
            inpaint_bytes = inpaint_step = None
            if underlay and cover.any():
                client.free()
                start = prefill(raw_img, mask_img, fg, torso)
                inpaint_bytes, inpaint_step = run_inpaint(cfg, client, raw_path, mask_img, recipe, prompt, run_dir, start)
    if kps is None:
        kps = parse_keypoints(kp_text)
        labels, cover, mask_img, sk, decisions, fg, torso = analyse(cfg, raw_img, kps, spec, gen)

    layers = {"parts": {"kind": "labels", "data": labels}}
    if inpaint_bytes:
        inp = Image.open(io.BytesIO(inpaint_bytes)).convert("RGB")
        ibg, _ = pixelate.background_mask(inp)
        layers["underlay"] = {"kind": "image", "data": np.asarray(inp), "mask": (~ibg) & cover & torso}
    sprite, info = postprocess(raw_bytes, gen, layers=layers)
    sprite_png = io.BytesIO()
    sprite.save(sprite_png, format="PNG", optimize=True)
    if np.any(np.asarray(sprite) != np.asarray(Image.open(io.BytesIO(sprite_bytes)).convert("RGBA"))):
        raise GenerationError(f"{sprite_file.name} no longer matches its recipe; regenerate the sprite before building rig data")
    alpha = np.asarray(sprite)[..., 3] > 0
    sampled = np.where(alpha, info["layers"]["parts"], 0).astype(np.uint16)
    parts, held = attach_held_items(sampled, int(rc["held_item_max_gap_px"]))
    decisions["held_items"] = held
    decisions["weapon_found"] = bool((parts & BIT["Weapon"]).any())
    under = None
    if "underlay" in info["layers"]:
        under = info["layers"]["underlay"].copy()
        covered = (parts & (BIT["ArmNearUpper"] | BIT["ArmNearLower"] | BIT["Weapon"])) > 0
        under[~covered] = 0                                     # only pixels the arm/weapon hides
        under[(under[..., 3] > 0) & ~alpha] = 0                 # never outside the sprite silhouette
        # torso colours come from the labels as sampled, so only the relabelled pixels themselves can change
        bridged = list(zip(*np.nonzero(covered & (parts != sampled))))
        under = fill_bridge_underlay(under, np.asarray(sprite), sampled, bridged)
        under, replaced = torso_colours_only(under, np.asarray(sprite), sampled)
        under, held["underlay_pixels_dropped"] = drop_detached_underlay(under, parts)

    transform = to_sprite_transform(info, gen)
    joints, ground_y = sprite_joints(sk, kps, transform, parts, info["feet_row"], decisions)
    counts = {p: int(((parts >> i) & 1).sum()) for i, p in enumerate(PARTS)}

    files = {"parts": "rig/parts.png", "parts_preview": "rig/parts_preview.png", "joints_txt": "rig/joints.txt",
             "keypoints": "rig/source/keypoints.json", "arm_mask": "rig/source/arm_mask.png"}
    parts_png = _png(encode_parts(parts, alpha), "RGBA")
    data = {"parts": parts_png}
    if under is not None:
        files.update(underlay="rig/underlay.png", inpaint_raw="rig/source/inpaint_raw.png")
        data["underlay"] = _png(under, "RGBA")
    rig = {
        "format": FORMAT,
        "generator_version": __version__,
        "character": recipe["name"],
        "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "sprite": {"file": sprite_file.name, "sha256": _sha(sprite_bytes), "size": list(sprite.size),
                   "coordinates": "pixels, origin top-left, y down", "facing": "right",
                   "ground_y": ground_y, "feet_row": info["feet_row"]},
        "joints": joints,
        "parts": {"file": files["parts"], "encoding": "bitmask per pixel: R = bits 0-7, G = bits 8-15, A = 255 where the sprite is opaque",
                  "bits": PARTS, "pixel_counts": counts,
                  "overlap": "leg pixels can carry a near and a far leg bit at once (legs drawn on top of each other)"},
        "underlay": ({"file": files["underlay"], "pixels": int((under[..., 3] > 0).sum()),
                      "recoloured_to_torso_colours": int(replaced),
                      "covers": "sprite pixels of the near arm and weapon; the colours are what the torso looks like behind them",
                      "palette": "the sprite's own colours"} if under is not None else None),
        "decisions": decisions,
        # hints for the animation side; a consumer may ignore them
        "animation_hints": {
            "leg_swing_scale": float(cfg.data["rig"]["robe_leg_swing_scale"]) if decisions["robed"] else 1.0,
            "note": ("robed: the robe is part of Body; swing the legs only a little so the robe stays one silhouette"
                     if decisions["robed"] else "")},
        "warnings": ([f"low-confidence keypoints on the near side: {decisions['weak_keypoints']}"]
                     if decisions["weak_keypoints"] else [])
                    + (["the near and far side are hard to tell apart (similar confidence): check the part map"]
                       if abs(decisions["side_confidence"]["right"] - decisions["side_confidence"]["left"]) < 0.05 else []),
        "recipe": {**step, "keypoints_sha256": _sha(kp_text.encode("utf-8")),
                   "analysis": {k: rc[k] for k in ("arm_radius", "hand_radius", "leg_radius", "torso_half_width",
                                                   "min_keypoint_confidence", "held_item_max_gap_px")},
                   "inpaint": inpaint_step if under is not None else None,
                   "source_raw_sha256": _sha(raw_bytes)},
        "files": files,
        "sha256": {k: _sha(v) for k, v in data.items()},
    }
    result = {"rig": rig, "joints_txt": joints_text(joints, ground_y), "data": data, "kp_text": kp_text,
              "inpaint_bytes": inpaint_bytes, "mask_img": mask_img, "parts": parts, "run_dir": str(run_dir)}
    if write:
        src_dir.mkdir(parents=True, exist_ok=True)
        (rig_dir / "parts.png").write_bytes(parts_png)
        parts_preview(parts).save(rig_dir / "parts_preview.png")
        (rig_dir / "joints.txt").write_text(result["joints_txt"], encoding="utf-8")
        (src_dir / "keypoints.json").write_text(kp_text, encoding="utf-8")
        mask_img.convert("L").save(src_dir / "arm_mask.png")
        if under is not None:
            (rig_dir / "underlay.png").write_bytes(data["underlay"])
            if not from_saved:
                (src_dir / "inpaint_raw.png").write_bytes(inpaint_bytes)
        elif (rig_dir / "underlay.png").exists():
            (rig_dir / "underlay.png").unlink()
        (rig_dir / "rig.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
    return result


def verify(cfg, name, folder, keep_comfy=False):
    """Re-run SDPose and the inpainting for a saved rig and compare with the stored files (writes nothing)."""
    folder = Path(folder)
    stored = json.loads((folder / "rig" / "rig.json").read_text(encoding="utf-8"))
    fresh = build(cfg, name, folder, keep_comfy=keep_comfy, underlay=stored.get("underlay") is not None, write=False)
    same_kp = fresh["rig"]["recipe"]["keypoints_sha256"] == stored["recipe"]["keypoints_sha256"]
    out = {"name": stored["character"], "keypoints_identical": same_kp,
           "joints_identical": fresh["rig"]["joints"] == stored["joints"]}
    for key in ("parts", "underlay"):
        if key in stored["sha256"]:
            out[f"{key}_identical"] = fresh["rig"]["sha256"].get(key) == stored["sha256"][key]
    out["all_identical"] = all(v for k, v in out.items() if k.endswith("identical"))
    out["run_dir"] = fresh["run_dir"]
    return out


def model_ready(cfg):
    m = next(m for m in load_models() if m["id"] == "sdpose-wholebody")
    return (cfg.models_dir / m["folder"] / m["file"]).exists()
