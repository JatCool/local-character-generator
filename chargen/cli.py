"""Command line interface. Logs go to stderr; the result goes to stdout (JSON with --json)."""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

from . import __version__, bench, models, pixelate, prompt_builder, spec as specmod
from .comfy import ComfyClient, ComfyError, ComfyProcess
from .config import load_config, load_profiles
from .generator import (GenerationError, build_request, generate, get_profile, load_character, missing_models,
                        stored_prompt, verify_reproduction)


def _emit(result, as_json):
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f"{key}: {value}")


def _spec_from_args(args):
    if args.spec:
        data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        if args.name:
            data["name"] = args.name
        if args.description and not data.get("description"):
            data["description"] = args.description
        return data
    if not args.description:
        raise GenerationError("give --description \"...\" or --spec character_spec.json")
    return specmod.parse_description(args.description, args.name)


def _profile(args, cfg):
    if args.profile:
        return args.profile
    return cfg.defaults.get("fast_profile", "fast") if args.fast else cfg.defaults.get("profile", "quality")


def cmd_generate(args, cfg):
    spec = _spec_from_args(args)
    request = build_request(cfg, spec, _profile(args, cfg), seed=args.seed, size=args.size, colors=args.colors,
                            outline=args.outline, variation=args.variation, width=args.width, height=args.height,
                            view=args.view, style=args.style, mirror=args.mirror, char_height=args.char_height,
                            outline_color=args.outline_color, guide=not args.no_guide, guide_build=args.guide_build,
                            proportions=args.proportions)
    if args.dry_run:
        positive, negative = prompt_builder.build(request["spec"], get_profile(request["generation"]["profile"]),
                                                  request["generation"]["variation"])
        return {"dry_run": True, **request, "positive": positive, "negative": negative}
    return generate(cfg, request, output_dir=args.output, variant=args.variant, overwrite=args.overwrite,
                    reference=args.reference, keep_comfy=args.keep_comfyui, description=spec.get("description"),
                    max_attempts=args.attempts)


def cmd_regenerate(args, cfg):
    recipe, out = load_character(cfg, args.name, args.output)
    if args.verify:
        return verify_reproduction(cfg, args.name, args.output, keep_comfy=args.keep_comfyui)
    gen = dict(recipe["generation"])
    spec = recipe["spec"]
    request = build_request(cfg, spec, args.profile or gen["profile"],
                            seed=args.seed if args.seed is not None else gen["seed"],
                            size=args.size or gen["size"],
                            colors=gen["colors"] if args.colors is None else args.colors,
                            char_height=gen.get("char_height") if not args.size else None,
                            outline_color=gen.get("outline_color"),
                            outline=gen["outline"] if args.outline is None else args.outline,
                            variation=args.variation if args.variation is not None else gen["variation"],
                            width=gen["width"], height=gen["height"],
                            guide=gen.get("guide") is not None, guide_build=gen.get("guide"),
                            mirror=gen.get("mirror", False) if args.mirror is None else args.mirror)
    from_raw = None
    if args.from_raw:
        meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        from_raw = (out / "references" / "source_raw.png", meta)
    # Same seed, profile, spec and variation = reproduce: keep the stored prompts (templates may have changed since).
    if args.seed is None and args.profile is None and args.variation is None:
        request["prompt"] = stored_prompt(recipe, out)
    if "char_height" not in gen and not args.size:
        # recipe from before fixed-height scaling: keep its layout so it still reproduces
        for key in ("char_height", "feet_margin", "outline_color"):
            request["generation"].pop(key, None)
    variant = not args.replace
    return generate(cfg, request, output_dir=out, variant=variant, overwrite=args.replace,
                    keep_comfy=args.keep_comfyui, description=spec.get("description"), from_raw=from_raw)


def cmd_rig(args, cfg):
    from . import rig
    from .spec import safe_name
    folder = Path(args.output) if args.output else cfg.character_dir(safe_name(args.name))
    if not (folder / "character.json").exists():
        raise GenerationError(f"no character.json in {folder}")
    if not args.from_saved and not rig.model_ready(cfg):
        raise GenerationError("the rig step needs the SDPose model: character-generator models install --group rig")
    if args.verify:
        return rig.verify(cfg, args.name, folder, keep_comfy=args.keep_comfyui)
    res = rig.build(cfg, args.name, folder, keep_comfy=args.keep_comfyui, from_saved=args.from_saved,
                    underlay=not args.no_underlay)
    r = res["rig"]
    return {"ok": True, "name": r["character"], "rig": str(folder / "rig" / "rig.json"),
            "files": r["files"], "decisions": r["decisions"], "warnings": r["warnings"],
            "underlay_pixels": (r["underlay"] or {}).get("pixels"), "part_pixels": r["parts"]["pixel_counts"]}


def cmd_migrate_names(args, cfg):
    """Folders made before 0.4.0 hold character.png; rename it to <Name>.png (pixels and asset id unchanged)."""
    from .generator import LEGACY_SPRITE, migrate_sprite_name
    root = cfg.project_root / cfg.output_root
    folders = [cfg.character_dir(specmod.safe_name(args.name))] if args.name else sorted(p for p in root.iterdir() if p.is_dir())
    renamed = []
    for folder in folders:
        if (folder / "character.json").exists() and (folder / LEGACY_SPRITE).exists():
            new = migrate_sprite_name(folder)
            if new:
                renamed.append(str(new))
    return {"ok": True, "renamed": renamed, "count": len(renamed)}


def cmd_parse(args, cfg):
    spec = specmod.normalize(_spec_from_args(args))
    profile = get_profile(_profile(args, cfg))
    positive, negative = prompt_builder.build(spec, profile)
    return {"spec": spec, "positive": positive, "negative": negative}


def cmd_pixelate(args, cfg):
    from .generator import postprocess
    d = cfg.defaults
    size = args.size or int(d.get("size", 48))
    gen = {"size": size, "colors": int(d.get("colors", 24)) if args.colors is None else args.colors,
           "outline": bool(d.get("outline", True)) if args.outline is None else args.outline,
           "char_height": int(round(size * float(d.get("char_height_ratio", 0.9375)))),
           "feet_margin": int(d.get("feet_margin", 1)), "outline_color": d.get("outline_color", "black")}
    sprite, info = postprocess(Path(args.input).read_bytes(), gen)
    sprite.save(args.out)
    if args.preview:
        pixelate.preview(sprite).save(args.preview)
    return {"out": args.out, **info}


def cmd_check(args, cfg):
    from .metrics import sprite_metrics
    reference = args.reference or (str(cfg.reference_sprite) if cfg.reference_sprite else None)
    ref = sprite_metrics(reference) if reference else None
    rows = {}
    for path in args.sprites:
        m = sprite_metrics(path)
        if ref and not m.get("empty"):
            m["vs_reference"] = {"height_diff": m["char_height"] - ref["char_height"],
                                 "feet_row_diff": m["feet_row"] - ref["feet_row"],
                                 "edge_dark_diff": round(m["edge_dark"] - ref["edge_dark"], 2)}
        rows[path] = m
    if ref:
        rows["reference:" + reference] = ref
    return rows


def cmd_benchmark(args, cfg):
    spec = _spec_from_args(args)
    out, results = bench.run(cfg, spec, args.profiles.split(","), [int(s) for s in args.seeds.split(",")],
                             [int(s) for s in args.sizes.split(",")], colors=args.colors or 24, outline=args.outline,
                             keep_comfy=args.keep_comfyui)
    return {"folder": str(out), "contact_sheet": str(out / "contact_sheet.png"), "runs": len(results)}


def cmd_doctor(args, cfg):
    checks = {"generator_version": __version__, "python": sys.version.split()[0], "pillow": Image.__version__}
    import numpy
    checks["numpy"] = numpy.__version__
    checks["project_file"] = str(cfg.project_file) if cfg.project_file else None
    checks["project_name"] = cfg.project_name
    checks["output_dir"] = str(cfg.project_root / cfg.output_root)
    checks["reference_sprite"] = str(cfg.reference_sprite) if cfg.reference_sprite else None
    if cfg.reference_sprite:
        checks["reference_sprite_ok"] = cfg.reference_sprite.is_file()
    checks["comfyui_url"] = cfg.comfy_url
    client = ComfyClient(cfg.comfy_url)
    checks["comfyui_running"] = client.is_up()
    try:
        cmd, _ = ComfyProcess(cfg).launch_command()
        checks["comfyui_launch"] = " ".join(cmd)
    except ComfyError as e:
        checks["comfyui_launch"] = f"NOT FOUND: {e}"
    checks["models_dir"] = str(cfg.models_dir)
    profiles = {}
    for name in load_profiles():
        miss = missing_models(cfg, get_profile(name))
        profiles[name] = "ready" if not miss else "missing: " + ", ".join(m["file"] for m in miss)
    checks["profiles"] = profiles
    if checks["comfyui_running"]:
        stats = client.system_stats()
        checks["comfyui_version"] = stats.get("system", {}).get("comfyui_version")
        checks["gpu"] = [(d.get("name"), round(d.get("vram_total", 0) / 2**30, 1)) for d in stats.get("devices", [])]
    return checks


def cmd_models(args, cfg):
    if args.action == "install":
        models.install(cfg, args.group)
    rows = models.status(cfg, args.group)
    return {m["id"]: {"group": m["group"], "installed": ok, "path": str(p),
                      "size_gb": round(m["size_bytes"] / 1e9, 2) if m.get("size_bytes") else None}
            for m, p, ok in rows}


def cmd_comfy(args, cfg):
    client = ComfyClient(cfg.comfy_url)
    proc = ComfyProcess(cfg)
    if args.action == "start":
        if client.is_up():
            return {"running": True, "started": False}
        proc.start(client, log=lambda m: print(m, file=sys.stderr))
        return {"running": True, "started": True, "pid": proc.proc.pid}
    if args.action == "stop":
        return {"stopped": proc.stop(log=lambda m: print(m, file=sys.stderr)),
                "note": "only a ComfyUI started by this tool (runs/comfyui.pid) is stopped"}
    return {"running": client.is_up(), "url": cfg.comfy_url}


def _add_common(p):
    p.add_argument("--name", help="character name = folder name under the output root")
    p.add_argument("--description", help="free-text character description")
    p.add_argument("--spec", help="structured character spec JSON (fields: docs/usage.md)")
    p.add_argument("--profile", help="quality | fast | nova | krea2 | smoke (default: quality)")
    p.add_argument("--fast", action="store_true", help="use the fast profile (SDXL + LCM, 8 steps)")


def build_parser():
    parser = argparse.ArgumentParser(prog="character-generator",
                                     description="Local pixel-art character generator (ComfyUI).")
    parser.add_argument("--project", help="project file or folder with character-generator.project.json "
                                          "(sets output root and defaults); also CHARGEN_PROJECT. Default: none, "
                                          "characters go to the tool's output/ folder")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="generate a character into <output root>/<Name>/")
    _add_common(g)
    g.add_argument("--seed", type=int, help="seed (default: random, stored in metadata)")
    g.add_argument("--size", type=int, choices=[32, 48, 64, 96, 128, 192],
                   help="canvas height in px (default from config/project: 48); the width grows if needed")
    g.add_argument("--width", type=int, help="generation width (default from profile)")
    g.add_argument("--height", type=int, help="generation height (default from profile)")
    g.add_argument("--colors", type=int, help="palette size (default from config; 0 = no palette reduction)")
    g.add_argument("--char-height", type=int, help="body height in px incl. outline (default: char_height_ratio x --size, 45 at 48)")
    g.add_argument("--outline-color", help="outline colour: black | darkest (default from config)")
    g.add_argument("--outline", action=argparse.BooleanOptionalAction, default=None,
                   help="1 px dark outline (default: on, off for profiles whose model draws its own)")
    g.add_argument("--view", choices=list(specmod.VIEWS), help="camera view (default side, facing right)")
    g.add_argument("--style", help="art style, e.g. '16-bit', '8-bit', 'SNES'")
    g.add_argument("--variation", help="extra prompt text for a variation")
    g.add_argument("--mirror", action="store_true", help="flip the sprite horizontally (model drew the wrong facing)")
    g.add_argument("--proportions", choices=["realistic", "player"],
                   help="realistic (default: follows the description best) or player (big head, ~30%% of the height)")
    g.add_argument("--no-guide", action="store_true", help="side view without the mannequin guide (text-to-image only)")
    g.add_argument("--guide-build", help="mannequin build for the side-view guide: slim, normal, broad, stocky, small, robed "
                                         "(default: chosen from body type/species/clothing)")
    g.add_argument("--reference", help="reference image to store with the character (for future workflows)")
    g.add_argument("--output", help="character folder (default <output root>/<Name>)")
    g.add_argument("--variant", action="store_true", help="write into variants/ instead of the main sprite <Name>.png")
    g.add_argument("--overwrite", action="store_true", help="replace an existing main sprite <Name>.png")
    g.add_argument("--keep-comfyui", action="store_true", help="leave a ComfyUI we started running")
    g.add_argument("--attempts", type=int, default=6,
                   help="seeds to try when the image fails the quality gate (sprite sheet, crop, background, "
                        "front/back view instead of side); 1 = no retry")
    g.add_argument("--dry-run", action="store_true", help="only build the request and prompt")
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("regenerate", help="re-run a saved character (same seed = same image)")
    r.add_argument("--name", required=True, help="character folder name under the output root")
    r.add_argument("--output", help="character folder (default <output root>/<Name>)")
    r.add_argument("--profile", help="other profile (a change: rebuilds the prompt)")
    r.add_argument("--seed", type=int, help="new seed for a variation (default: the saved seed)")
    r.add_argument("--size", type=int, choices=[32, 48, 64, 96, 128, 192], help="new canvas height (body = ratio x size)")
    r.add_argument("--colors", type=int, help="new palette size (0 = none)")
    r.add_argument("--outline", action=argparse.BooleanOptionalAction, default=None, help="turn the outline on/off")
    r.add_argument("--variation", help="extra prompt text (a change: rebuilds the prompt)")
    r.add_argument("--mirror", action=argparse.BooleanOptionalAction, default=None,
                   help="flip horizontally (stored in the recipe)")
    r.add_argument("--from-raw", action="store_true",
                   help="no GPU: redo only post-processing (size/colors/outline/mirror) from references/source_raw.png")
    r.add_argument("--replace", action="store_true", help="replace the main sprite <Name>.png instead of adding a variant")
    r.add_argument("--verify", action="store_true", help="re-render the saved recipe and compare (writes nothing)")
    r.add_argument("--keep-comfyui", action="store_true", help="leave a ComfyUI we started running")
    r.set_defaults(func=cmd_regenerate)

    rg = sub.add_parser("rig", help="rig data for an approved side-view character: joints, part map, hidden pixels")
    rg.add_argument("--name", required=True, help="character folder name under the output root")
    rg.add_argument("--output", help="character folder (default <output root>/<Name>)")
    rg.add_argument("--no-underlay", action="store_true", help="skip the hidden-pixel layer (no inpainting)")
    rg.add_argument("--from-saved", action="store_true",
                    help="no GPU: rebuild from the stored keypoints and inpainting (rig/source/)")
    rg.add_argument("--verify", action="store_true", help="re-run SDPose and inpainting and compare (writes nothing)")
    rg.add_argument("--keep-comfyui", action="store_true", help="leave a ComfyUI we started running")
    rg.set_defaults(func=cmd_rig)

    mn = sub.add_parser("migrate-names", help="rename character.png of folders made before 0.4.0 to <Name>.png")
    mn.add_argument("--name", help="only this character (default: every character under the output root)")
    mn.set_defaults(func=cmd_migrate_names)

    p = sub.add_parser("parse", help="show the structured spec and prompt for a description (no GPU)")
    _add_common(p)
    p.set_defaults(func=cmd_parse)

    x = sub.add_parser("pixelate", help="post-process an existing image into a sprite")
    x.add_argument("input", help="high-resolution image (character on a plain background)")
    x.add_argument("out", help="output sprite PNG")
    x.add_argument("--size", type=int, help="canvas height (default from config/project)")
    x.add_argument("--colors", type=int, help="palette size (default from config/project; 0 = none)")
    x.add_argument("--outline", action=argparse.BooleanOptionalAction, default=None, help="default from config/project")
    x.add_argument("--preview", help="also write an 8x preview PNG")
    x.set_defaults(func=cmd_pixelate)

    b = sub.add_parser("benchmark", help="compare profiles/seeds at final sprite sizes (writes runs/bench_*)")
    _add_common(b)
    b.add_argument("--profiles", default="quality,fast,nova,krea2", help="comma-separated profiles to compare")
    b.add_argument("--seeds", default="1,2,3", help="comma-separated seeds (same for every profile)")
    b.add_argument("--sizes", default="48,64,96,128", help="comma-separated canvas sizes to judge")
    b.add_argument("--colors", type=int, help="palette size")
    b.add_argument("--outline", action=argparse.BooleanOptionalAction, default=None, help="outline on/off")
    b.add_argument("--keep-comfyui", action="store_true", help="leave a ComfyUI we started running")
    b.set_defaults(func=cmd_benchmark)

    k = sub.add_parser("check", help="sprite metrics (scale, feet row, alpha, outline, halo, noise)")
    k.add_argument("sprites", nargs="+", help="sprite PNG(s) to measure")
    k.add_argument("--reference", help="reference sprite to compare with (default: the project's reference_sprite)")
    k.set_defaults(func=cmd_check)

    d = sub.add_parser("doctor", help="check Python, ComfyUI, models and the project/output folder")
    d.set_defaults(func=cmd_doctor)

    m = sub.add_parser("models", help="list or install model files (explicit only)")
    m.add_argument("action", choices=["list", "install"], help="list = what is installed; install = download a group")
    m.add_argument("--group", help="sdxl | nova | krea2 | smoke (required for install)")
    m.set_defaults(func=cmd_models)

    c = sub.add_parser("comfy", help="status/start/stop the local ComfyUI")
    c.add_argument("action", choices=["status", "start", "stop"],
                   help="stop only stops a ComfyUI that this tool started (runs/comfyui.pid)")
    c.set_defaults(func=cmd_comfy)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.project)
    except FileNotFoundError as e:
        _emit({"ok": False, "error": str(e)}, args.json)
        return 1
    try:
        result = args.func(args, cfg)
    except (GenerationError, ComfyError, ValueError, FileNotFoundError) as e:
        _emit({"ok": False, "error": str(e)}, args.json)
        return 1
    _emit(result, args.json)
    return 0
