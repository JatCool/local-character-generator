"""Orchestration: spec -> prompt -> ComfyUI -> raw PNG -> pixel-art sprite -> character folder + metadata."""

import datetime as dt
import hashlib
import io
import json
import random
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

from . import __version__, guide as guidemod, pixelate, prompt_builder, spec as specmod, workflow as wf
from .comfy import ComfyError, ComfySession
from .config import TOOL_ROOT, load_models, load_profiles


class GenerationError(RuntimeError):
    pass


# Body proportion presets. "realistic" follows descriptions best (acceptance test: 9/10 strict side views);
# "player" matches the player sprite's big-head look (8/10 strict side views, drops some described items).
PROPORTIONS = {
    "realistic": {},
    "player": {"prompt": "chibi proportions, big head", "guide_head_scale": 1.45},
}


LEGACY_SPRITE = "character.png"   # sprite name before 0.4.0: the same in every folder, so tools keyed on the file name collided


def sprite_file(name):
    """The main sprite's file name: the character's own name, so every character's sprite (and whatever a consumer
    derives from the file name, e.g. an animation output folder) is unique and deterministic."""
    return f"{specmod.safe_name(name)}.png"


def sprite_path(folder, name=None):
    """The main sprite of a character folder: <Name>.png, or the legacy character.png of a folder made before 0.4.0."""
    folder = Path(folder)
    path = folder / sprite_file(name or folder.name)
    legacy = folder / LEGACY_SPRITE
    return legacy if not path.exists() and legacy.exists() else path


def migrate_sprite_name(folder, name=None):
    """Renames a legacy character.png to <Name>.png (with a .meta sidecar, so an engine keeps its asset id) and updates
    the file names recorded in metadata.json and rig/rig.json. Pixels are untouched. Returns the new path or None."""
    folder = Path(folder)
    legacy, path = folder / LEGACY_SPRITE, folder / sprite_file(name or folder.name)
    if not legacy.exists() or path.exists():
        return None
    legacy.rename(path)
    meta_sidecar = legacy.with_name(LEGACY_SPRITE + ".meta")
    if meta_sidecar.exists():
        meta_sidecar.rename(path.with_name(path.name + ".meta"))
    for rel, keys in (("metadata.json", ("files", "sprite")), ("rig/rig.json", ("sprite", "file"))):
        p = folder / rel
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        node = data.get(keys[0])
        if isinstance(node, dict) and isinstance(node.get(keys[1]), str) and node[keys[1]].endswith(LEGACY_SPRITE):
            node[keys[1]] = node[keys[1]][: -len(LEGACY_SPRITE)] + path.name
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _check_unique_name(out):
    """Two characters whose names differ only in letter case would share a folder on Windows (and so would anything
    a consumer names after them): refuse that."""
    parent = out.parent
    if parent.exists():
        for other in parent.iterdir():
            if other.is_dir() and other.name != out.name and other.name.lower() == out.name.lower():
                raise GenerationError(f"'{out.name}' differs from the existing character '{other.name}' only in letter "
                                      "case; names must be unique ignoring case. Use another --name.")


def _log(msg):
    import sys
    print(msg, file=sys.stderr, flush=True)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class VramSampler:
    """Samples whole-GPU memory use with nvidia-smi (includes other apps; baseline recorded separately)."""

    def __init__(self, interval=0.5):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._thread = None

    @staticmethod
    def read():
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout
            used, total = out.strip().splitlines()[0].split(",")
            return int(used), int(total)
        except Exception:
            return None

    def __enter__(self):
        first = self.read()
        self.baseline = first[0] if first else None
        self.total = first[1] if first else None
        if first:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self.interval):
            r = self.read()
            if r:
                self.samples.append(r[0])

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        return False

    def report(self):
        if self.baseline is None:
            return None
        peak = max(self.samples + [self.baseline])
        return {"gpu_total_mib": self.total, "baseline_mib": self.baseline, "peak_mib": peak,
                "peak_delta_mib": peak - self.baseline}


def get_profile(name):
    profiles = load_profiles()
    if name not in profiles:
        raise GenerationError(f"unknown profile '{name}'; available: {', '.join(profiles)}")
    return profiles[name]


def missing_models(cfg, profile):
    by_id = {m["id"]: m for m in load_models()}
    missing = []
    for mid in profile.get("models", []):
        m = by_id[mid]
        if not (cfg.models_dir / m["folder"] / m["file"]).exists():
            missing.append(m)
    return missing


def build_request(cfg, spec, profile_name, seed=None, size=None, colors=None, outline=None, variation=None,
                  width=None, height=None, view=None, style=None, mirror=False, char_height=None,
                  outline_color=None, guide=True, guide_build=None, proportions=None):
    """Everything needed to (re)produce a generation, as plain data (stored as character.json)."""
    spec = dict(spec)
    if view:
        spec["view"] = view
    proportions = proportions or cfg.defaults.get("proportions", "realistic")
    if proportions not in PROPORTIONS:
        raise GenerationError(f"proportions must be one of {', '.join(PROPORTIONS)}")
    if proportions == "player" and "guide_head_scale" not in spec:
        # like the player sprite: head ~30 % of the height (bigger head in the guide + matching prompt words)
        spec["proportions"] = PROPORTIONS["player"]["prompt"]
        spec["guide_head_scale"] = PROPORTIONS["player"]["guide_head_scale"]
    if style:
        spec["art_style"] = style
    spec = specmod.normalize(spec)
    profile = get_profile(profile_name)
    d = cfg.defaults
    size = int(size or d.get("size", 48))
    if char_height is None and d.get("char_height_ratio"):
        char_height = int(round(size * float(d["char_height_ratio"])))
    # Side view: start from a mannequin guide whose build follows the spec (see chargen/guide.py).
    build = None
    if guide and profile.get("guided_workflow") and spec["view"] in profile.get("guided_views", []):
        build = guide_build or guidemod.choose_build(spec)
        if build not in guidemod.BUILDS:
            raise GenerationError(f"guide build must be one of {', '.join(guidemod.BUILDS)}")
    return {
        "spec": spec,
        "generation": {
            "profile": profile_name,
            "seed": int(seed if seed is not None else random.randrange(2**31)),
            "size": size,
            # fixed character height = consistent scale between characters (45 of 48 px like the player)
            "char_height": int(char_height) if char_height else None,
            "feet_margin": int(d.get("feet_margin", 0)),
            "outline_color": outline_color or profile.get("outline_color", d.get("outline_color", "darkest")),
            "colors": int(colors or cfg.defaults.get("colors", 24)),
            # CLI flag > profile (models that draw their own outline turn it off) > config default
            "outline": bool(outline if outline is not None
                            else profile.get("outline", cfg.defaults.get("outline", False))),
            "variation": variation or "",
            "mirror": bool(mirror),  # flip horizontally after post-processing (model drew the wrong facing)
            "guide": build,  # mannequin build used as img2img start (None = text-to-image from noise)
            **({"guide_head_scale": float(spec["guide_head_scale"])} if build and spec.get("guide_head_scale") else {}),
            "width": int(width or profile["width"]),
            "height": int(height or profile["height"]),
        },
    }


def _unload_on_profile_switch(cfg, client, profile_name):
    """ComfyUI 0.38 can reuse a cached, LoRA-patched SDXL when the next workflow uses the same checkpoint with a
    different LoRA chain (quality -> fast), which renders pure noise. Unload models whenever the profile changes
    on this server; consecutive runs of one profile keep the warm cache."""
    state_file = cfg.runs_dir / "comfy_last_profile.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    if state.get("url") != cfg.comfy_url or state.get("profile") != profile_name:
        _log(f"Last profile on this server: {state.get('profile') or 'unknown'} -> {profile_name}: "
             "unloading models in ComfyUI first.")
        client.free()
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"url": cfg.comfy_url, "profile": profile_name}), encoding="utf-8")


def render(cfg, request, run_dir, keep_comfy=False, client=None):
    """Runs ComfyUI for a request. Returns (raw PNG bytes, details dict). Writes the workflow/raw to run_dir."""
    gen = request["generation"]
    profile = get_profile(gen["profile"])
    miss = missing_models(cfg, profile)
    if miss:
        names = ", ".join(f"{m['file']} ({m['folder']})" for m in miss)
        groups = sorted({m["group"] for m in miss})
        raise GenerationError(f"profile '{gen['profile']}' needs models that are not installed: {names}. "
                              f"Install with: character-generator models install --group {' / '.join(groups)}")
    if request.get("prompt"):
        # a stored recipe: use its exact prompts, so later template changes cannot alter a saved character
        positive, negative = request["prompt"]["positive"], request["prompt"]["negative"]
    else:
        positive, negative = prompt_builder.build(request["spec"], profile, gen["variation"])
    guide_build = gen.get("guide")
    workflow_name = profile["guided_workflow"] if guide_build else profile["workflow"]
    template, wf_hash = wf.load(workflow_name)
    guide_path = guide_sha = None
    if guide_build:
        guide_path, guide_sha = guidemod.guide_file(guide_build, gen.get("guide_head_scale", 1.0))
    name = specmod.safe_name(request["spec"]["name"])
    values = dict(profile["params"])
    values.update(positive=positive, negative=negative, seed=gen["seed"], width=gen["width"], height=gen["height"],
                  prefix=f"chargen/{name}/{name}_{gen['profile']}_s{gen['seed']}")
    if guide_path:
        values["guide"] = "chargen/" + guide_path.name  # the name ComfyUI gets on upload
    api = wf.fill(template, values)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "workflow_api.json").write_text(json.dumps(api, indent=2), encoding="utf-8")

    def run(c):
        _unload_on_profile_switch(cfg, c, gen["profile"])
        api_now = wf.fill(template, dict(values, guide=c.upload_image(guide_path))) if guide_path else api
        stats = c.system_stats()
        t0 = time.time()
        with VramSampler() as vram:
            pid = c.queue(api_now)
            _log(f"Queued prompt {pid} ({gen['profile']}, seed {gen['seed']}, {gen['width']}x{gen['height']})...")
            entry = c.wait(pid, cfg.generation_timeout)
        images = c.output_images(entry, wf.save_image_nodes(api))
        if not images:
            raise ComfyError("ComfyUI finished but returned no image")
        data = c.fetch_image(images[0])
        return data, {
            "comfyui_version": stats.get("system", {}).get("comfyui_version"),
            "device": (stats.get("devices") or [{}])[0].get("name"),
            "prompt_id": pid,
            "comfy_output": images[0],
            "seconds_total": round(time.time() - t0, 2),
            "seconds_execution": _execution_seconds(entry),
            "vram": vram.report(),
        }

    if client is not None:
        data, details = run(client)
    else:
        with ComfySession(cfg, keep_running=keep_comfy, log=_log) as c:
            data, details = run(c)
    (run_dir / "raw.png").write_bytes(data)
    details.update(guide={"build": guide_build, "file": guide_path.relative_to(TOOL_ROOT).as_posix(),
                          "sha256": guide_sha, "denoise": profile["params"].get("denoise")} if guide_build else None)
    details.update(positive=positive, negative=negative, workflow=workflow_name, workflow_sha256_16=wf_hash,
                   models=[m for m in load_models() if m["id"] in profile.get("models", [])],
                   params=profile["params"])
    return data, details


def _execution_seconds(entry):
    stamps = {kind: d.get("timestamp") for kind, d in entry.get("status", {}).get("messages", []) if isinstance(d, dict)}
    if stamps.get("execution_start") and stamps.get("execution_success"):
        return round((stamps["execution_success"] - stamps["execution_start"]) / 1000.0, 2)
    return None


def postprocess(raw_bytes, gen, layers=None):
    """raw image -> sprite. `layers` (see pixelate.pixelate) are sampled on the same grid and mirrored with it."""
    img = Image.open(io.BytesIO(raw_bytes))
    # Keys missing in old recipes fall back to the behaviour they were made with (fit-to-square, darkest outline).
    sprite, info = pixelate.pixelate(img, size=gen["size"], colors=gen["colors"], outline=gen["outline"],
                                     char_height=gen.get("char_height"), feet_margin=gen.get("feet_margin", 0),
                                     outline_color=gen.get("outline_color", "darkest"), layers=layers)
    if gen.get("mirror"):
        sprite = sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        info["mirrored"] = True
        for name, arr in info.get("layers", {}).items():
            info["layers"][name] = arr[:, ::-1].copy()
    return sprite, info


def _png_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _render_checked(cfg, request, name, keep_comfy, max_attempts):
    """Render; if the raw image fails the quality gate (sprite sheet, cropped, no plain background),
    try seed+1, seed+2, ... The accepted seed is written back into the request, so it reproduces exactly."""
    rejected = []
    gen = request["generation"]
    base_seed = gen["seed"]
    with ComfySession(cfg, keep_running=keep_comfy, log=_log) as client:
        max_attempts = max(1, max_attempts)
        for attempt in range(max_attempts):
            gen["seed"] = base_seed + attempt
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            run_dir = cfg.runs_dir / f"{stamp}_{name}_{gen['profile']}_s{gen['seed']}"
            raw, details = render(cfg, request, run_dir, client=client)
            check = pixelate.analyze(Image.open(io.BytesIO(raw)), expect_side=request["spec"].get("view") == "side")
            if check["ok"] or attempt == max_attempts - 1:
                if not check["ok"]:
                    _log(f"Warning: seed {gen['seed']} also failed the check ({'; '.join(check['reasons'])}); keeping it.")
                details["quality_check"] = check
                return raw, details, run_dir, rejected
            _log(f"Seed {gen['seed']} rejected: {'; '.join(check['reasons'])}. Trying seed {gen['seed'] + 1}.")
            rejected.append({"seed": gen["seed"], "reasons": check["reasons"], "run_dir": str(run_dir)})


def _from_raw(cfg, name, gen, raw_path, meta):
    raw = Path(raw_path).read_bytes()
    if meta.get("sha256", {}).get("raw") and _sha256(raw) != meta["sha256"]["raw"]:
        raise GenerationError(f"{raw_path} does not match the raw hash in metadata.json; regenerate without --from-raw")
    if meta.get("seed") != gen["seed"] or meta.get("profile") != gen["profile"]:
        raise GenerationError("--from-raw only changes post-processing; seed/profile must stay as stored")
    run_dir = cfg.runs_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{name}_{gen['profile']}_s{gen['seed']}_post"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "raw.png").write_bytes(raw)
    details = {
        "positive": meta["generated_prompt"], "negative": meta["negative_prompt"], "models": meta["model_details"],
        "workflow": meta["workflow"], "workflow_sha256_16": meta["workflow_sha256_16"], "params": meta["sampler_params"],
        "comfyui_version": meta.get("comfyui_version"), "device": meta.get("device"),
        "seconds_total": 0.0, "seconds_execution": None, "vram": None, "quality_check": meta.get("quality_check"),
        "guide": meta.get("guide"),
    }
    _log(f"Re-using the stored raw generation ({raw_path}); no ComfyUI run.")
    return raw, details, run_dir, meta.get("rejected_seeds", [])


def _warnings(details):
    check = details.get("quality_check") or {}
    if check and not check.get("ok", True):
        return [f"quality check failed on every tried seed: {'; '.join(check.get('reasons', []))}. "
                "Look at the preview; try other seeds (--seed), --guide-build, or a simpler description."]
    return []


def generate(cfg, request, output_dir=None, variant=False, overwrite=False, reference=None, keep_comfy=False,
             description=None, max_attempts=6, from_raw=None):
    """from_raw=(raw_png_path, previous_metadata): skip ComfyUI and only redo the post-processing
    (size, colours, outline, mirror) on a stored raw generation of the same recipe."""
    spec = request["spec"]
    gen = request["generation"]
    name = specmod.safe_name(spec["name"])
    out = Path(output_dir) if output_dir else cfg.character_dir(name)
    _check_unique_name(out)
    existing = sprite_path(out, name)
    if not variant and existing.exists() and not overwrite:
        raise GenerationError(f"{existing} already exists. Use --variant to add a variant, "
                              "--overwrite to replace it, or another --name.")
    if from_raw:
        raw, details, run_dir, rejected = _from_raw(cfg, name, gen, *from_raw)
    else:
        raw, details, run_dir, rejected = _render_checked(cfg, request, name, keep_comfy, max_attempts)
    gen = request["generation"]  # the seed may have moved on if earlier seeds were rejected
    sprite, pix = postprocess(raw, gen)
    sprite_png = _png_bytes(sprite)
    (run_dir / "sprite.png").write_bytes(sprite_png)
    pixelate.preview(sprite).save(run_dir / "preview.png")

    out.mkdir(parents=True, exist_ok=True)
    tag = f"{name}_{gen['profile']}_s{gen['seed']}"
    if variant:
        target_png = out / "variants" / f"{tag}.png"
        target_raw = out / "variants" / f"{tag}_raw.png"
    else:
        migrate_sprite_name(out, name)               # a folder from before 0.4.0 keeps its sprite's asset id
        target_png = out / sprite_file(name)
        target_raw = out / "references" / "source_raw.png"
    target_png.parent.mkdir(parents=True, exist_ok=True)
    target_raw.parent.mkdir(parents=True, exist_ok=True)
    target_png.write_bytes(sprite_png)
    target_raw.write_bytes(raw)

    ref_info = None
    if reference:
        ref = Path(reference)
        dest = out / "references" / ref.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if ref.resolve() != dest.resolve():
            shutil.copyfile(ref, dest)
        ref_info = {"path": _rel(cfg, dest), "used_by_workflow": False,
                    "note": "stored for future reference-based workflows; the current text-to-image profiles do not consume it"}
        _log("Note: --reference was saved to references/ but the current profiles are text-to-image only.")

    recipe = {"name": name, "generator_version": __version__, "spec": spec, "generation": gen,
              "prompt": {"positive": details["positive"], "negative": details["negative"]}}
    metadata = {
        "character_name": name,
        "original_description": description if description is not None else spec.get("description", ""),
        "generated_prompt": details["positive"],
        "negative_prompt": details["negative"],
        "profile": gen["profile"],
        "model": [m["file"] for m in details["models"]],
        "model_details": details["models"],
        "workflow": details["workflow"],
        "workflow_sha256_16": details["workflow_sha256_16"],
        "guide": details.get("guide"),
        "sampler_params": details["params"],
        "seed": gen["seed"],
        "requested_seed": rejected[0]["seed"] if rejected else gen["seed"],
        "rejected_seeds": rejected,
        "quality_check": details.get("quality_check"),
        "warnings": _warnings(details),
        "generation_resolution": [gen["width"], gen["height"]],
        "final_resolution": list(sprite.size),
        "palette_colors_requested": gen["colors"],
        "postprocess": pix,
        "variation": gen["variation"],
        "reference": ref_info,
        "timestamp": _now(),
        "generator_version": __version__,
        "comfyui_version": details["comfyui_version"],
        "device": details["device"],
        "seconds_total": details["seconds_total"],
        "seconds_execution": details["seconds_execution"],
        "vram": details["vram"],
        "files": {"sprite": _rel(cfg, target_png), "raw": _rel(cfg, target_raw)},
        "sha256": {"sprite": _sha256(sprite_png), "raw": _sha256(raw)},
        "run_dir": str(run_dir),
    }
    if variant:
        (target_png.with_suffix(".json")).write_text(json.dumps({"recipe": recipe, "metadata": metadata}, indent=2),
                                                     encoding="utf-8")
    else:
        (out / "character.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        (out / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (out / "prompt.txt").write_text(f"POSITIVE:\n{details['positive']}\n\nNEGATIVE:\n{details['negative']}\n",
                                        encoding="utf-8")
    return {
        "ok": True,
        "name": name,
        "sprite": str(target_png),
        "project_path": _rel(cfg, target_png),
        "raw": str(target_raw),
        "preview": str(run_dir / "preview.png"),
        "folder": str(out),
        "variant": variant,
        "profile": gen["profile"],
        "seed": gen["seed"],
        "size": list(sprite.size),
        "character_px": pix["character_size_px"],
        "palette_size": pix["palette_size"],
        "rejected_seeds": [r["seed"] for r in rejected],
        "warnings": _warnings(details),
        "seconds": details["seconds_total"],
        "vram_peak_mib": (details["vram"] or {}).get("peak_mib"),
        "prompt": details["positive"],
    }


def _rel(cfg, path):
    try:
        return Path(path).resolve().relative_to(cfg.project_root).as_posix()
    except ValueError:
        return str(path)


def load_character(cfg, name, output_dir=None):
    out = Path(output_dir) if output_dir else cfg.character_dir(specmod.safe_name(name))
    path = out / "character.json"
    if not path.exists():
        raise GenerationError(f"no character.json in {out}; generate the character first")
    recipe = json.loads(path.read_text(encoding="utf-8"))
    return recipe, out


def stored_prompt(recipe, folder):
    """The exact prompts a character was made with (character.json, or metadata.json for older recipes)."""
    if recipe.get("prompt"):
        return recipe["prompt"]
    meta_path = Path(folder) / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {"positive": meta["generated_prompt"], "negative": meta["negative_prompt"]}
    return None


def verify_reproduction(cfg, name, output_dir=None, keep_comfy=False):
    """Re-renders the stored recipe and compares it with the saved sprite/raw. Writes nothing into the output folder."""
    recipe, out = load_character(cfg, name, output_dir)
    request = {"spec": recipe["spec"], "generation": recipe["generation"], "prompt": stored_prompt(recipe, out)}
    run_dir = cfg.runs_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{recipe['name']}_verify"
    with ComfySession(cfg, keep_running=keep_comfy, log=_log) as client:
        # ComfyUI caches node outputs of identical workflows; clear models and caches so this is a real re-render.
        client.free()
        raw, details = render(cfg, request, run_dir, client=client)
    sprite, _ = postprocess(raw, request["generation"])
    meta_path = out / "metadata.json"
    stored_wf = json.loads(meta_path.read_text(encoding="utf-8")).get("workflow_sha256_16") if meta_path.exists() else None
    import numpy as np
    new = np.asarray(sprite)
    old = np.asarray(Image.open(sprite_path(out, recipe["name"])).convert("RGBA"))
    diff = int((new != old).any(axis=2).sum()) if new.shape == old.shape else -1
    raw_old = (out / "references" / "source_raw.png").read_bytes()
    return {
        "name": recipe["name"],
        "seed": recipe["generation"]["seed"],
        "sprite_identical": diff == 0,
        "sprite_pixels_different": diff,
        "raw_identical": _sha256(raw) == _sha256(raw_old),
        "seconds_execution": details["seconds_execution"],
        # a changed workflow file explains a mismatch (the recipe stores the hash of the one it was made with)
        "workflow_changed": bool(stored_wf and stored_wf != details["workflow_sha256_16"]),
        "run_dir": str(run_dir),
    }
