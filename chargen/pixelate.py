"""High-resolution generation -> clean pixel-art sprite.

Steps (all deterministic, Pillow + numpy):
 1. background removal: flood fill from the image border over pixels close to the border colour
 2. crop to the character and choose the cell size so the character fits the target canvas
 3. palette: k colours chosen from the character pixels only (no dithering)
 4. grid collapse: each output pixel = most frequent palette colour in the centre of its source cell,
    alpha = majority of the cell (so every output pixel is exactly one game pixel: uniform pixel size)
 5. cleanup: drop tiny islands, optional 1 px dark outline
 6. place on a transparent canvas, feet on the bottom row, horizontally centred (pivot bottom-centre)
"""

from collections import deque

import numpy as np
from PIL import Image


def _border_color(rgb):
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    return np.median(border, axis=0)


def _flood(seed, allowed):
    filled = seed & allowed
    while True:
        grown = filled.copy()
        grown[1:] |= filled[:-1]
        grown[:-1] |= filled[1:]
        grown[:, 1:] |= filled[:, :-1]
        grown[:, :-1] |= filled[:, 1:]
        grown &= allowed
        if (grown == filled).all():
            return filled
        filled = grown


def _work_mask(img, tolerance, work_size, remove_shadow, hole_tolerance=14.0, hole_min_fraction=0.0004):
    small = img.convert("RGB").resize((work_size, work_size), Image.Resampling.BOX)
    rgb = np.asarray(small, dtype=np.float32)
    bg = _border_color(rgb)
    similar = np.linalg.norm(rgb - bg, axis=2) < tolerance
    border = np.zeros_like(similar)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    filled = _flood(border, similar)
    # Enclosed background (between the legs, inside an arm loop): not reachable from the border. Remove regions that
    # are almost exactly the background colour and at least ~1 sprite pixel big; highlights are rarely that flat.
    dist = np.linalg.norm(rgb - bg, axis=2)
    holes = (dist < hole_tolerance) & ~filled
    min_hole = max(4, int(hole_min_fraction * holes.size))
    for comp in _component_pixels(holes):
        if len(comp) >= min_hole:
            ys, xs = zip(*comp)
            filled[list(ys), list(xs)] = True
    # Halo: anti-aliased edge pixels blended with the background (lighter fringe). Grow the background two
    # steps into pixels that are still fairly close to the background colour.
    near = np.linalg.norm(rgb - bg, axis=2) < 2.2 * tolerance
    for _ in range(2):
        grown = filled.copy()
        grown[1:] |= filled[:-1]
        grown[:-1] |= filled[1:]
        grown[:, 1:] |= filled[:, :-1]
        grown[:, :-1] |= filled[:, 1:]
        filled = filled | (grown & near)
    if remove_shadow and (~filled).any():
        # Floor shadow: grey (low chroma), darker than the background but not outline-dark, near the feet,
        # and connected to the background. Coloured boots and dark outlines are kept.
        ys = np.nonzero(~filled)[0]
        y_top, y_bot = ys.min(), ys.max()
        band = np.zeros_like(filled)
        band[int(y_top + 0.82 * (y_bot - y_top)):, :] = True
        lum = rgb.mean(axis=2)
        chroma = rgb.max(axis=2) - rgb.min(axis=2)
        bg_lum = float(bg.mean())
        shadow = band & (chroma < 28) & (lum > 0.45 * bg_lum) & (lum < bg_lum + 12)
        filled = _flood(filled, filled | shadow)
    return filled, bg


def background_mask(img, tolerance=40.0, work_size=512, remove_shadow=True):
    """True where the pixel is background. Flood fill from the border at work_size, upsampled."""
    filled, bg = _work_mask(img, tolerance, work_size, remove_shadow)
    mask = Image.fromarray((filled * 255).astype(np.uint8)).resize(img.size, Image.Resampling.NEAREST)
    return np.asarray(mask) > 127, bg


def _component_pixels(mask):
    """Pixel lists of the 4-connected True components of a boolean array."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    comps = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        comp, q = [], deque([(y, x)])
        seen[y, x] = True
        while q:
            cy, cx = q.popleft()
            comp.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        comps.append(comp)
    return comps


def _components(mask):
    """Sizes of 4-connected True components, largest first."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    sizes = []
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        n, q = 0, deque([(y, x)])
        seen[y, x] = True
        while q:
            cy, cx = q.popleft()
            n += 1
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        sizes.append(n)
    return sorted(sizes, reverse=True)


def mirror_symmetry(fg):
    """IoU of the figure with its mirror image about the vertical axis through its centre of mass.
    Front/back views are close to symmetric (0.80-0.91 measured), strict side views are not (0.65-0.73)."""
    ys, xs = np.nonzero(fg)
    if len(ys) == 0:
        return 0.0
    m = fg[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    cx = int(round(np.nonzero(m)[1].mean()))
    w = m.shape[1]
    half = max(cx, w - cx)
    pad = np.zeros((m.shape[0], 2 * half + 1), dtype=bool)
    pad[:, half - cx:half - cx + w] = m
    mir = pad[:, ::-1]
    return float((pad & mir).sum() / max(1, (pad | mir).sum()))


def torso_width(fg):
    """Median width of the rows between 25 % and 60 % of the figure height, as a share of the height.
    Side views measured 0.15-0.25, front/back views 0.25-0.42."""
    ys, _ = np.nonzero(fg)
    if len(ys) == 0:
        return 0.0
    y0, h = ys.min(), ys.max() - ys.min() + 1
    band = fg[y0 + int(0.25 * h): y0 + int(0.60 * h)]
    widths = [np.ptp(np.nonzero(r)[0]) + 1 for r in band if r.any()]
    return float(np.median(widths)) / h if widths else 0.0


# A side-view request fails when the figure is BOTH mirror-symmetric and wide (front/back view). Either alone is not
# enough: thin straight side views can be symmetric (up to 0.94) and side views with a cape can be wide.
# Calibrated on 71 labelled images: rejects 18/18 front/back views, 1/53 side views.
SIDE_VIEW_MAX_SYMMETRY = 0.78
SIDE_VIEW_MAX_TORSO = 0.245


def analyze(img, tolerance=40.0, work_size=128, min_part=0.10, expect_side=False):
    """Cheap quality gate on the raw generation: one character, fully in frame.

    Returns a dict with 'ok' and the reasons, used by the generator to skip sprite sheets / cropped bodies.
    """
    filled, _ = _work_mask(img, tolerance, work_size, remove_shadow=True)
    fg = ~filled
    sizes = _components(fg)
    reasons = []
    if not sizes or sizes[0] < 0.01 * fg.size:
        reasons.append("no character found")
    elif fg.mean() > 0.6:
        reasons.append("background not separable (not a plain background)")
    else:
        large = [s for s in sizes if s >= min_part * sizes[0]]
        if len(large) > 1:
            reasons.append(f"{len(large)} separate figures (sprite sheet or several characters)")
        edge = np.concatenate([fg[0], fg[-1], fg[:, 0], fg[:, -1]])
        if edge.mean() > 0.02:
            reasons.append("character touches the image border (cropped)")
        ys, xs = np.nonzero(fg)
        if (ys.max() - ys.min()) < 0.35 * work_size:
            reasons.append("character too small in frame")
    symmetry = torso = None
    if expect_side and not reasons:
        bg_full, _ = background_mask(img, tolerance)  # same resolution the thresholds were calibrated at
        main = _main_figure(~bg_full)
        symmetry = round(mirror_symmetry(main), 3)
        torso = round(torso_width(main), 3)
        if symmetry >= SIDE_VIEW_MAX_SYMMETRY and torso >= SIDE_VIEW_MAX_TORSO:
            reasons.append(f"looks like a front or back view (symmetry {symmetry}, torso width {torso})")
    return {"ok": not reasons, "reasons": reasons, "components": sizes[:5],
            "foreground_fraction": round(float(fg.mean()), 3), "symmetry": symmetry, "torso_width": torso}


def _palette(rgb, fg, colors):
    pixels = rgb[fg]
    if len(pixels) == 0:
        raise ValueError("no character pixels found (background removal removed everything)")
    strip = Image.fromarray(pixels.reshape(1, -1, 3).astype(np.uint8))
    q = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT, kmeans=3, dither=Image.Dither.NONE)
    used = sorted(i for _, i in q.getcolors())
    flat = q.getpalette()
    real = np.array([flat[3 * i: 3 * i + 3] for i in used], dtype=np.uint8)
    # Pad the 256-entry palette with copies of a real colour, so no pixel can map to a padding black.
    full = np.concatenate([real, np.repeat(real[:1], 256 - len(real), axis=0)])
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(full.flatten().tolist())
    return pal_img, full


def _islands(alpha, min_size):
    h, w = alpha.shape
    seen = np.zeros_like(alpha)
    out = alpha.copy()
    for y in range(h):
        for x in range(w):
            if alpha[y, x] and not seen[y, x]:
                comp, q = [], deque([(y, x)])
                seen[y, x] = True
                while q:
                    cy, cx = q.popleft()
                    comp.append((cy, cx))
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and alpha[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            q.append((ny, nx))
                if len(comp) < min_size:
                    for cy, cx in comp:
                        out[cy, cx] = False
    return out


def _outline(rgba, palette_rgb, color="darkest"):
    """1 px outline on transparent pixels that touch the sprite (4-neighbour).
    color: "darkest" (darkest palette colour), "black", or [r, g, b]."""
    alpha = rgba[..., 3] > 0
    ring = np.zeros_like(alpha)
    ring[1:] |= alpha[:-1]
    ring[:-1] |= alpha[1:]
    ring[:, 1:] |= alpha[:, :-1]
    ring[:, :-1] |= alpha[:, 1:]
    ring &= ~alpha
    if color == "darkest":
        rgb = palette_rgb[np.argmin(palette_rgb.astype(int).sum(axis=1))]
    elif color == "black":
        rgb = np.array([0, 0, 0], dtype=np.uint8)
    else:
        rgb = np.array(color, dtype=np.uint8)
    out = rgba.copy()
    out[ring, :3] = rgb
    out[ring, 3] = 255
    return out


def _main_figure(fg, work=256, min_part=0.10):
    """Foreground without detached specks: keep components >= min_part of the largest (measured at low res)."""
    small = np.asarray(Image.fromarray((fg * 255).astype(np.uint8)).resize((work, work), Image.Resampling.NEAREST)) > 127
    comps = _component_pixels(small)
    if not comps:
        return fg
    largest = max(len(c) for c in comps)
    keep = np.zeros_like(small)
    for comp in comps:
        if len(comp) >= min_part * largest:
            cy, cx = zip(*comp)
            keep[list(cy), list(cx)] = True
    # dilate one low-res pixel so the upsampled mask does not shave edges, then intersect with the real mask
    grown = keep.copy()
    grown[1:] |= keep[:-1]
    grown[:-1] |= keep[1:]
    grown[:, 1:] |= keep[:, :-1]
    grown[:, :-1] |= keep[:, 1:]
    big = np.asarray(Image.fromarray((grown * 255).astype(np.uint8)).resize(fg.shape[::-1], Image.Resampling.NEAREST)) > 127
    return fg & big


def _body_top(fg, y0, y1, width_fraction=0.12):
    """First row (from the top) that is at least width_fraction of the typical row width: skips thin staff tips."""
    counts = fg[y0:y1].sum(axis=1)
    typical = np.median(counts[counts > 0])
    rows = np.nonzero(counts >= width_fraction * typical)[0]
    return y0 + int(rows[0]) if len(rows) else y0


def pixelate(img, size=64, colors=24, tolerance=40.0, outline=False, margin_top=1, margin_side=1,
             char_height=None, min_island=3, center_fraction=0.6, remove_shadow=True,
             feet_margin=0, outline_color="darkest", layers=None):
    """Returns (sprite RGBA Image, info dict).

    size          canvas height (and minimum width).
    char_height   fixed character height in pixels, outline included (consistent scale between characters): the
                  scale follows the height only and the canvas grows wider when a weapon/cape needs it.
                  None = legacy "fit into a size x size square".
    colors        palette size; 0 = no palette reduction (each pixel = median colour of its cell).
    feet_margin   empty rows below the feet (the player sprite has 1).
    outline_color "darkest" | "black" | [r, g, b].
    layers        optional extra rasters at the source resolution, sampled on exactly the same grid as the sprite
                  (the sprite itself is not affected). {name: {"kind": "labels", "data": uint16 HxW}} gives the most
                  frequent label per cell; {name: {"kind": "image", "data": RGB HxWx3, "mask": bool HxW}} gives the
                  most frequent sprite-palette colour per cell where the mask covers the cell. Results are returned in
                  info["layers"][name] as canvas-sized arrays (uint16 labels, RGBA images).
    """
    img = img.convert("RGB")
    rgb = np.asarray(img, dtype=np.uint8)
    bg_mask, bg_color = background_mask(img, tolerance, remove_shadow=remove_shadow)
    fg = ~bg_mask
    ys, xs = np.nonzero(fg)
    if len(ys) == 0:
        raise ValueError("no character found in the generated image")
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    box_h, box_w = y1 - y0, x1 - x0

    reserve = 2 if outline else 0  # room for the outline ring
    body_top = y0
    if char_height:
        # Consistent scale: measure the main body, not stray specks or thin protrusions (staff/bow/spear tips).
        fg = _main_figure(fg)
        ys, xs = np.nonzero(fg)
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        box_h, box_w = y1 - y0, x1 - x0
        body_top = _body_top(fg, y0, y1)
        body_h = max(1, int(char_height) - reserve)
        cell = (y1 - body_top) / body_h
        out_h = max(body_h, int(round(box_h / cell)))  # taller than the body only for protrusions above the head
        out_w = max(1, int(round(box_w / cell)))
        # The head normally leaves size - feet_margin - char_height empty rows on top (player: 2); a protrusion
        # (helmet spike, raised staff) uses that headroom first and only then makes the canvas taller.
        canvas_h = max(size, out_h + reserve + feet_margin)
        canvas_w = max(size, out_w + reserve + 2 * margin_side)
        canvas_w += canvas_w % 2  # keep the centre pivot on a pixel boundary
        if body_h + reserve + feet_margin > size:
            raise ValueError(f"char_height {char_height} + feet_margin {feet_margin} does not fit a {size} px canvas")
    else:
        max_h = size - margin_top - reserve
        max_w = size - 2 * margin_side - reserve
        cell = max(box_h / max_h, box_w / max_w)
        out_h = max(1, min(max_h, int(round(box_h / cell))))
        out_w = max(1, min(max_w, int(round(box_w / cell))))
        canvas_h = canvas_w = size

    if colors:
        pal_img, palette_rgb = _palette(rgb, fg, colors)
        indexed = np.asarray(img.quantize(palette=pal_img, dither=Image.Dither.NONE))
    else:
        palette_rgb = None

    sprite = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    layers = layers or {}
    layer_out = {}
    for name, layer in layers.items():
        if layer["kind"] == "labels":
            layer_out[name] = np.zeros((out_h, out_w), dtype=np.uint16)
        else:
            layer_out[name] = np.zeros((out_h, out_w, 4), dtype=np.uint8)
            if colors:
                layer["_indexed"] = np.asarray(Image.fromarray(layer["data"]).quantize(palette=pal_img, dither=Image.Dither.NONE))
    inset = (1.0 - center_fraction) / 2
    for oy in range(out_h):
        sy0 = y0 + oy * cell
        sy1 = y0 + (oy + 1) * cell
        for ox in range(out_w):
            sx0 = x0 + ox * cell
            sx1 = x0 + (ox + 1) * cell
            a0, a1 = int(sy0), max(int(sy0) + 1, int(np.ceil(sy1)))
            b0, b1 = int(sx0), max(int(sx0) + 1, int(np.ceil(sx1)))
            cell_fg = fg[a0:a1, b0:b1]
            if cell_fg.size == 0:
                continue
            c0 = int(sy0 + inset * cell)
            c1 = max(c0 + 1, int(np.ceil(sy1 - inset * cell)))
            d0 = int(sx0 + inset * cell)
            d1 = max(d0 + 1, int(np.ceil(sx1 - inset * cell)))
            if layers:
                _sample_layers(layers, layer_out, oy, ox, (a0, a1, b0, b1), (c0, c1, d0, d1), fg, colors, palette_rgb)
            if cell_fg.mean() < 0.5:
                continue
            centre_fg = fg[c0:c1, d0:d1]
            if colors:
                centre = indexed[c0:c1, d0:d1][centre_fg]
                if centre.size == 0:
                    centre = indexed[a0:a1, b0:b1][cell_fg]
                sprite[oy, ox, :3] = palette_rgb[np.bincount(centre).argmax()]
            else:
                centre = rgb[c0:c1, d0:d1][centre_fg]
                if centre.size == 0:
                    centre = rgb[a0:a1, b0:b1][cell_fg]
                sprite[oy, ox, :3] = np.median(centre, axis=0).astype(np.uint8)
            sprite[oy, ox, 3] = 255

    alpha = _islands(sprite[..., 3] > 0, min_island)
    sprite[~alpha] = 0
    if palette_rgb is None:
        opaque = sprite[alpha][:, :3]
        palette_rgb = opaque if len(opaque) else np.zeros((1, 3), dtype=np.uint8)

    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    off = reserve // 2
    top = canvas_h - feet_margin - (out_h + reserve) + off
    left = (canvas_w - out_w - reserve) // 2 + off
    canvas[top:top + out_h, left:left + out_w] = sprite
    if outline:
        canvas = _outline(canvas, palette_rgb, outline_color)
    placed_layers = {}
    for name, arr in layer_out.items():
        big = np.zeros((canvas_h, canvas_w) + arr.shape[2:], dtype=arr.dtype)
        if arr.ndim == 2:
            arr = np.where(alpha, arr, 0).astype(arr.dtype)        # labels only where the sprite has a pixel
        big[top:top + out_h, left:left + out_w] = arr
        if arr.ndim == 2:
            big = _fill_ring_labels(big, canvas[..., 3] > 0)
        placed_layers[name] = big

    used = canvas[canvas[..., 3] > 0][:, :3]
    info = {
        "background_color": [int(v) for v in bg_color],
        "character_box_source": [int(x0), int(y0), int(x1), int(y1)],
        "cell_size_source_px": round(float(cell), 3),
        "character_size_px": [int(out_w + reserve), int(out_h + reserve)],
        "body_height_px": int(char_height) if char_height else None,
        "palette_size": int(len(np.unique(used, axis=0))) if len(used) else 0,
        "canvas": [int(canvas_w), int(canvas_h)],
        "feet_row": int(canvas_h - 1 - feet_margin),
        "pivot": "bottom-center",
        "outline": bool(outline),
        "outline_color": outline_color if outline else None,
    }
    if placed_layers:
        info["layers"] = placed_layers
    return Image.fromarray(canvas, "RGBA"), info


def _sample_layers(layers, layer_out, oy, ox, cell_box, centre_box, fg, colors, palette_rgb):
    a0, a1, b0, b1 = cell_box
    c0, c1, d0, d1 = centre_box
    for name, layer in layers.items():
        if layer["kind"] == "labels":
            centre = layer["data"][c0:c1, d0:d1][fg[c0:c1, d0:d1]]
            if centre.size == 0:
                centre = layer["data"][a0:a1, b0:b1][fg[a0:a1, b0:b1]]
            if centre.size:
                values, counts = np.unique(centre, return_counts=True)
                layer_out[name][oy, ox] = values[np.argmax(counts)]
        else:
            mask = layer["mask"]
            if mask[a0:a1, b0:b1].mean() < 0.5:
                continue
            sel = mask[c0:c1, d0:d1]
            if colors:
                centre = layer["_indexed"][c0:c1, d0:d1][sel]
                if centre.size == 0:
                    centre = layer["_indexed"][a0:a1, b0:b1][mask[a0:a1, b0:b1]]
                layer_out[name][oy, ox, :3] = palette_rgb[np.bincount(centre).argmax()]
            else:
                centre = layer["data"][c0:c1, d0:d1][sel]
                if centre.size == 0:
                    centre = layer["data"][a0:a1, b0:b1][mask[a0:a1, b0:b1]]
                layer_out[name][oy, ox, :3] = np.median(centre, axis=0).astype(np.uint8)
            layer_out[name][oy, ox, 3] = 255


def _fill_ring_labels(labels, opaque):
    """Outline-ring pixels have no source cell: give each the most common label of its labelled 4-neighbours."""
    out = labels.copy()
    h, w = labels.shape
    for y, x in zip(*np.nonzero(opaque & (labels == 0))):
        neigh = [labels[ny, nx] for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                 if 0 <= ny < h and 0 <= nx < w and labels[ny, nx]]
        if neigh:
            values, counts = np.unique(neigh, return_counts=True)
            out[y, x] = values[np.argmax(counts)]
    # a second pass for ring pixels that only touch other ring pixels
    for y, x in zip(*np.nonzero(opaque & (out == 0))):
        neigh = [out[ny, nx] for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                 if 0 <= ny < h and 0 <= nx < w and out[ny, nx]]
        out[y, x] = neigh[0] if neigh else 1
    return out


def preview(sprite, scale=8, checker=8):
    """Upscaled (nearest) preview on a checkerboard, for humans and reports."""
    w, h = sprite.size
    big = sprite.resize((w * scale, h * scale), Image.Resampling.NEAREST)
    yy, xx = np.indices((big.size[1], big.size[0])) // max(1, checker * scale // 2)
    shade = np.where((yy + xx) % 2 == 1, 230, 200).astype(np.uint8)
    bg = Image.fromarray(np.dstack([shade, shade, shade, np.full_like(shade, 255)]), "RGBA")
    bg.alpha_composite(big)
    return bg.convert("RGB")
