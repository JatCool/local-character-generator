# Extending: models, profiles, workflows, prompts, guides

Everything model-specific is data (`config/`, `workflows/`, `prompts/`); code changes are rarely needed.
After any change run `python -m unittest discover -s tests` (offline) and one real generation; for a new default,
re-run the acceptance test ([model-benchmark.md](model-benchmark.md)).

## Add a model file

Add an entry to `config/models.json`:

```json
{ "id": "my-lora", "group": "mygroup", "folder": "loras", "file": "my-lora.safetensors",
  "url": "https://huggingface.co/<org>/<repo>/resolve/main/my-lora.safetensors",
  "size_bytes": 170543052, "license": "...", "source": "https://..." }
```

`folder` is the ComfyUI models subfolder (`checkpoints`, `loras`, `vae`, `diffusion_models`, `text_encoders`, ...).
`models install --group mygroup` downloads it (resumable); `doctor` shows whether profiles that need it are ready.
A file that needs a login cannot be downloaded by the tool: put it there by hand.

## Add or change a profile

`config/profiles.json` -> `profiles`:

```json
"my-profile": {
  "description": "SDXL + my LoRA",
  "workflow": "sdxl_pixelart.json",
  "guided_workflow": "sdxl_pixelart_guided.json",
  "guided_views": ["side"],
  "models": ["sdxl-base", "my-lora"],
  "prompt_style": "tags",
  "prompt_prefix": "pixel art",
  "negative_file": "negative.txt",
  "width": 1024, "height": 1024,
  "outline": true,
  "params": { "checkpoint": "sd_xl_base_1.0.safetensors", "lora": "my-lora.safetensors",
              "lora_strength": 1.0, "lora_clip_strength": 1.0, "steps": 30, "cfg": 6.0,
              "sampler": "dpmpp_2m", "scheduler": "karras", "denoise": 0.92 }
}
```

| Key | Meaning |
|---|---|
| `workflow` | workflow file used from noise (and for views not in `guided_views`) |
| `guided_workflow`, `guided_views` | img2img workflow that starts from the side-view mannequin; omit both to disable guides |
| `models` | ids from `models.json` that must exist (`doctor`, clear error message) |
| `prompt_style` | `tags` (`prompts/tags.txt`, CLIP models) or `natural` (`prompts/natural.txt`, LLM encoders) |
| `prompt_prefix` / `prompt_suffix` | style words added around the template (e.g. trigger words) |
| `negative_file` | negative template, `""` for models without a negative prompt (cfg 1 / distilled) |
| `width`, `height` | generation size |
| `outline` | `false` if the model already draws outlines (otherwise config default) |
| `outline_color` | optional per-profile outline colour |
| `params` | values for the workflow placeholders |

Use it with `--profile my-profile`; make it the default in `config/config.json` (`defaults.profile`) or a project file.
Compare it first: `benchmark --spec examples/specs/Rogue.json --profiles quality,my-profile --seeds 1,2,3`.
If the profile reuses the SDXL checkpoint with another LoRA chain, the generator unloads models when switching profiles
(ComfyUI 0.38 caching bug); nothing to do.

## Add or change a workflow

1. Build it in the ComfyUI web UI and export it in **API format** (*Workflow -> Export (API)*).
2. Replace the values that change per run with placeholders: `"{{positive}}"`, `"{{negative}}"`, `"{{seed}}"`,
   `"{{width}}"`, `"{{height}}"`, `"{{prefix}}"` (SaveImage `filename_prefix`), `"{{guide}}"` (LoadImage of the guide,
   guided workflows), and any `params` key of the profile (e.g. `"{{checkpoint}}"`, `"{{denoise}}"`).
3. Keep exactly one `SaveImage` node (the generator downloads its first image).
4. Save as `workflows/<name>.json` and reference it from a profile. `tests/test_chargen.py`
   (`test_every_profile_fills_completely`) checks that every placeholder of every profile gets a value.
5. Changing an existing workflow changes the images its profile makes. Saved characters keep the hash of the workflow
   they were made with; `regenerate --verify` reports `workflow_changed`. Prefer a new file for incompatible changes.

Image inputs other than the guide (e.g. a reference image for a future identity/style workflow) follow the guide
pattern: upload with `ComfyClient.upload_image` in `generator.render` and pass the returned name as a placeholder.
`--reference` already stores the file and records it in `metadata.json`.

## Change the prompt

* Wording for all characters: `prompts/tags.txt`, `prompts/natural.txt`, `prompts/negative.txt` (one segment per
  line, `{field}` placeholders, `#` comments). Saved characters are not affected (their prompts are frozen in
  `character.json`); new characters and variations use the new templates.
* View phrases and view negatives: `VIEWS` in `chargen/spec.py`, `VIEW_NEGATIVE` and `VIEWS_NATURAL` in
  `chargen/prompt_builder.py`.
* New spec fields: add to `TEXT_FIELDS`/`LIST_FIELDS` in `chargen/spec.py` and to `_values` in `prompt_builder.py`,
  then use `{field}` in the templates.

## Change the side-view guide

`chargen/guide.py`: `BUILDS` (head share, body depth, leg share, limb thickness, robe), `_RULES` (keywords -> build),
`draw_guide`. Keep the depth narrow (<= ~0.21 of the height) or side views turn into front views. Render all builds to
check them:

```bash
<python> -c "import sys; sys.path.insert(0,'.'); from chargen import guide; [guide.draw_guide(b).save(f'runs/guide_{b}.png') for b in guide.BUILDS]"
```

Guides for other views (e.g. a front mannequin) would need a new entry in `guided_views` and a view parameter in
`draw_guide`.

## Change defaults, layout and post-processing

* Defaults (size, body ratio, feet margin, colours, outline, profile, proportions): `config/config.json` -> `defaults`,
  or per consuming project in its project file.
* Quality-gate thresholds: `analyze`, `SIDE_VIEW_MAX_SYMMETRY`, `SIDE_VIEW_MAX_TORSO` in `chargen/pixelate.py`
  (calibrate on labelled images; the numbers in `pipeline.md` explain the current values).
* Post-processing steps: `pixelate()` and helpers in `chargen/pixelate.py`.

## Tests

`tests/test_chargen.py` (unittest, no GPU, ~1.5 s): spec parsing, prompts, workflow filling for every profile,
pixel-art processing on synthetic images (layout, alpha, palette, outline, shadow, holes, halo, determinism), quality
gate, guides, project file handling, recipes. Run with ComfyUI's Python:

```bash
C:\AI\ComfyUI\python_embeded\python.exe -s -m unittest discover -s tests
```
