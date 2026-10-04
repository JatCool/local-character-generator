"""Objective sprite metrics, used by `character-generator check` and the acceptance test.

Compare a generated sprite with the game's reference sprite (the player): same height, feet row, hard alpha,
outline darkness, no light halo, similar detail density.
"""

import numpy as np
from PIL import Image

from .pixelate import _components


def sprite_metrics(path_or_img):
    img = path_or_img if isinstance(path_or_img, Image.Image) else Image.open(path_or_img)
    a = np.asarray(img.convert("RGBA")).astype(int)
    alpha = a[..., 3] > 0
    rgb = a[..., :3]
    lum = rgb.mean(axis=2)
    h, w = alpha.shape
    if not alpha.any():
        return {"size": [w, h], "empty": True}
    ys, xs = np.nonzero(alpha)
    pad = np.pad(alpha, 1)
    inner = pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
    edge = alpha & ~inner
    # pixels whose colour differs from every opaque 4-neighbour (single-pixel detail or noise)
    iso = 0
    for y, x in zip(ys, xs):
        same, n = False, 0
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and alpha[ny, nx]:
                n += 1
                if (rgb[ny, nx] == rgb[y, x]).all():
                    same = True
                    break
        if n and not same:
            iso += 1
    comps = _components(alpha)
    return {
        "size": [w, h],
        "char_height": int(ys.max() - ys.min() + 1),
        "char_width": int(xs.max() - xs.min() + 1),
        "top_row": int(ys.min()),
        "feet_row": int(ys.max()),
        "alpha_binary": bool(set(np.unique(a[..., 3]).tolist()) <= {0, 255}),
        "colors": int(len(np.unique(rgb[alpha], axis=0))),
        "edge_dark": round(float((lum[edge] < 60).mean()), 2),
        "halo": round(float((lum[edge] > 170).mean()), 3),
        "isolated": round(iso / int(alpha.sum()), 2),
        "islands": len(comps),
        "largest_island_share": round(comps[0] / sum(comps), 3),
    }
