"""Acceptance test sheet: generated characters vs the player sprite.

Usage (from tools/local-character-generator):
    <python> scripts/acceptance.py [--project <project>] [--reference sprite.png] DwarfWarrior OldWizard ...

Writes runs/acceptance_<timestamp>/:
  lineup.png        every character sprite next to the reference sprite, at x4, on one ground line
  matrix.png        each character re-post-processed from its stored raw with several outline/palette settings
  metrics.json/.md  sprite metrics per character and variant (see chargen/metrics.py)
No GPU is used: everything is derived from references/source_raw.png.
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chargen.config import load_config  # noqa: E402
from chargen.generator import postprocess, sprite_path  # noqa: E402
from chargen.metrics import sprite_metrics  # noqa: E402

VARIANTS = {
    "default (pal 24, black outline)": dict(colors=24, outline=True, outline_color="black"),
    "no outline": dict(colors=24, outline=False),
    "no palette (black outline)": dict(colors=0, outline=True, outline_color="black"),
    "no palette, no outline": dict(colors=0, outline=False),
    "darkest-colour outline": dict(colors=24, outline=True, outline_color="darkest"),
    "palette 48 (black outline)": dict(colors=48, outline=True, outline_color="black"),
}
BG = (52, 61, 82, 255)


def scaled(img, s):
    return img.resize((img.size[0] * s, img.size[1] * s), Image.Resampling.NEAREST)


def lineup(items, scale=4, pad=16, label_h=16):
    width = sum(i.size[0] * scale + pad for _, i in items) + pad
    height = max(i.size[1] for _, i in items) * scale + label_h + 8
    sheet = Image.new("RGBA", (width, height), BG)
    d = ImageDraw.Draw(sheet)
    x = pad
    for label, im in items:
        big = scaled(im.convert("RGBA"), scale)
        sheet.alpha_composite(big, (x, height - big.size[1] - 4))
        d.text((x, 3), label[:22], fill=(255, 255, 255))
        x += big.size[0] + pad
    ground = height - 4 - scale  # player feet row (46 of 48) sits one pixel above the canvas bottom
    d.line([(0, ground + scale - 1), (width, ground + scale - 1)], fill=(120, 130, 150, 255))
    return sheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--project", help="project file/folder (output root, reference sprite)")
    ap.add_argument("--reference", help="reference sprite (default: the project's reference_sprite)")
    args = ap.parse_args()
    cfg = load_config(args.project)
    reference = args.reference or cfg.reference_sprite
    if not reference:
        ap.error("no reference sprite: pass --reference or use a project file with reference_sprite")
    out = cfg.runs_dir / f"acceptance_{dt.datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True)
    player = Image.open(reference).convert("RGBA")
    ref = sprite_metrics(player)

    chars = []
    for name in args.names:
        folder = cfg.character_dir(name)
        recipe = json.loads((folder / "character.json").read_text(encoding="utf-8"))
        chars.append((name, folder, recipe))

    items = [("Reference", player)] + [(n, Image.open(sprite_path(f, n))) for n, f, _ in chars]
    lineup(items).convert("RGB").save(out / "lineup.png")

    metrics = {"reference": ref, "characters": {}}
    rows = []
    for name, folder, recipe in chars:
        raw = (folder / "references" / "source_raw.png").read_bytes()
        row = [("Reference", player)]
        metrics["characters"][name] = {"seed": recipe["generation"]["seed"]}
        for label, overrides in VARIANTS.items():
            gen = dict(recipe["generation"], **overrides)
            sprite, _ = postprocess(raw, gen)
            row.append((label, sprite))
            metrics["characters"][name][label] = sprite_metrics(sprite)
        rows.append(lineup(row, scale=3))
    sheet = Image.new("RGBA", (max(r.size[0] for r in rows), sum(r.size[1] for r in rows)), BG)
    y = 0
    for r in rows:
        sheet.alpha_composite(r, (0, y))
        y += r.size[1]
    sheet.convert("RGB").save(out / "matrix.png")

    (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    keys = ["char_height", "feet_row", "alpha_binary", "colors", "edge_dark", "halo", "isolated", "islands"]
    lines = ["| character | variant | " + " | ".join(keys) + " |", "|---|---|" + "---|" * len(keys),
             "| **Player** | reference | " + " | ".join(str(ref[k]) for k in keys) + " |"]
    for name, per in metrics["characters"].items():
        for label in VARIANTS:
            m = per[label]
            lines.append(f"| {name} | {label} | " + " | ".join(str(m[k]) for k in keys) + " |")
    (out / "metrics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
