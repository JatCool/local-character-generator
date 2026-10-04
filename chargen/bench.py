"""Benchmark: the same character with several profiles and seeds, judged at the final sprite sizes.

Writes runs/bench_<timestamp>/ with every raw image, sprites at each size, a contact sheet and results.json.
Nothing is written into the output folder.
"""

import datetime as dt
import io
import json

from PIL import Image, ImageDraw

from . import pixelate
from .comfy import ComfySession
from .generator import _log, build_request, missing_models, get_profile, postprocess, render


def run(cfg, spec, profiles, seeds, sizes, colors=24, outline=None, keep_comfy=False):
    out = cfg.runs_dir / f"bench_{dt.datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with ComfySession(cfg, keep_running=keep_comfy, log=_log) as client:
        for profile in profiles:
            if missing_models(cfg, get_profile(profile)):
                _log(f"skip {profile}: models not installed")
                continue
            client.free()  # unload the previous model so each profile's VRAM/time includes its own load
            for i, seed in enumerate(seeds):
                req = build_request(cfg, spec, profile, seed=seed, size=min(sizes), colors=colors, outline=outline)
                tag = f"{profile}_s{seed}"
                try:
                    raw, details = render(cfg, req, out / tag, client=client)
                except Exception as e:
                    _log(f"{tag}: FAILED {e}")
                    results.append({"profile": profile, "seed": seed, "error": str(e)})
                    continue
                img = Image.open(io.BytesIO(raw))
                row = {"profile": profile, "seed": seed, "first_of_profile": i == 0,
                       "seconds_total": details["seconds_total"], "seconds_execution": details["seconds_execution"],
                       "vram": details["vram"], "prompt": details["positive"], "sprites": {}}
                for size in sizes:
                    sprite, info = postprocess(raw, dict(req["generation"], size=size, char_height=None)
                                               if size != req["generation"]["size"] else req["generation"])
                    path = out / tag / f"sprite_{size}.png"
                    sprite.save(path)
                    row["sprites"][size] = {"path": str(path), **info}
                results.append(row)
                _log(f"{tag}: {details['seconds_total']}s, peak VRAM {(details['vram'] or {}).get('peak_mib')} MiB")
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    sheet = contact_sheet(out, results, sizes)
    _log(f"Benchmark written to {out} (sheet: {sheet})")
    return out, results


def contact_sheet(out, results, sizes, cell=256):
    rows = [r for r in results if "error" not in r]
    if not rows:
        return None
    cols = 1 + len(sizes) + len(sizes)  # raw | each size at 256 | each size at 1:1 x2 game scale
    label_h = 18
    sheet = Image.new("RGB", (cols * cell, len(rows) * (cell + label_h)), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for r_i, row in enumerate(rows):
        y = r_i * (cell + label_h)
        raw = Image.open(out / f"{row['profile']}_s{row['seed']}" / "raw.png").convert("RGB")
        sheet.paste(raw.resize((cell, cell), Image.Resampling.LANCZOS), (0, y + label_h))
        draw.text((4, y + 2), f"{row['profile']} seed {row['seed']}  {row['seconds_total']}s", fill=(255, 255, 255))
        for s_i, size in enumerate(sizes):
            sprite = Image.open(row["sprites"][size]["path"]).convert("RGBA")
            big = pixelate.preview(sprite, scale=max(1, cell // size))
            sheet.paste(big.crop((0, 0, cell, cell)), ((1 + s_i) * cell, y + label_h))
            draw.text(((1 + s_i) * cell + 4, y + 2), f"{size}px (zoomed)", fill=(255, 255, 255))
            game = pixelate.preview(sprite, scale=2)
            x = (1 + len(sizes) + s_i) * cell
            sheet.paste(game.crop((0, 0, min(cell, game.size[0]), min(cell, game.size[1]))), (x, y + label_h))
            draw.text((x + 4, y + 2), f"{size}px at 2x", fill=(255, 255, 255))
    path = out / "contact_sheet.png"
    sheet.save(path)
    return path
