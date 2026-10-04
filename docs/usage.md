# Usage

All examples run from this folder. Launchers: `./character-generator` (Git Bash / POSIX), `character-generator.cmd`
(cmd / PowerShell), or `<python> character_generator.py`. Global options go **before** the command:

```
character-generator [--project <file|folder>] [--json] [--version] <command> [options]
```

* `--project` - a consuming project's `character-generator.project.json` (or the folder containing it). It sets the
  output root and the generation defaults ([integration.md](integration.md)). Also via `CHARGEN_PROJECT`. Without it,
  characters go to this tool's `output/` folder with the defaults in `config/config.json`.
* `--json` - print the result as JSON on stdout (logs always go to stderr). Exit code 0 = success, 1 = error
  (`{"ok": false, "error": "..."}`).

## Generate a character

From a description (a keyword parser turns it into a spec):

```bash
./character-generator generate --name Rogue --description "Young female rogue, short dark brown hair, dark green hood, lightweight leather armor, small dagger, slim athletic body, confident expression, medieval fantasy"
```

From a structured spec (recommended; this is what the Claude skill does):

```bash
./character-generator --json generate --spec examples/specs/KnightWoman.json
```

What happens: spec -> prompt -> ComfyUI (started if needed) -> 1024x1024 image -> quality gate (re-rolls the seed up to
6 times) -> 48x48 sprite -> files below. It prints the sprite path, seed, rejected seeds, warnings, time and the prompt.

Produced files (default output root `output/`, or the project's `output_root`):

```
output/Rogue/
  Rogue.png                  48x48 RGBA sprite (always <Name>.png): transparent background, 45 px body, feet on row 46, black outline
  character.json             the recipe: spec + generation settings + exact prompts (regenerate reads this)
  prompt.txt                 positive and negative prompt, human-readable
  metadata.json              everything about the run (see "Output structure")
  references/source_raw.png  the 1024x1024 generation (identity reference; post-processing source)
runs/<timestamp>_Rogue_quality_s<seed>/   workflow_api.json, raw.png, sprite.png, preview.png (8x, for review)
```

An existing sprite is never replaced silently: add `--variant` (writes
`variants/<Name>_<profile>_s<seed>.png` + `.json` + `_raw.png`) or `--overwrite`.

More examples:

```bash
./character-generator generate --name Rogue --description "..." --fast --variant        # fast mode, as a variant
./character-generator generate --name Dwarf --spec examples/specs/DwarfWarrior.json --seed 1001
./character-generator generate --name Rogue --description "..." --size 96               # bigger sprite, same layout
./character-generator generate --name Guard --description "..." --view front            # no guide for front views
./character-generator generate --name Rogue --description "..." --dry-run               # spec + prompt only, no GPU
./character-generator parse --description "old dwarf blacksmith with a big hammer"      # what the parser makes of it
```

## Character spec

A JSON object; every field optional except `name` (lists are lists of short phrases). Defaults in brackets.

| Field | Meaning |
|---|---|
| `name` | folder name, e.g. `Rogue` (characters `A-Z a-z 0-9 _ -`; others become `_`) |
| `description` | the original free text (stored, not used for the prompt when a spec is given) |
| `role` | class/job: rogue, knight, merchant... |
| `species` [human], `gender`, `age` | identity |
| `body_type`, `face`, `hair`, `expression` | appearance phrases |
| `clothing[]`, `armor[]`, `weapons[]`, `accessories[]`, `colors[]`, `details[]` | equipment and look; weapons get extra prompt weight `(…:1.3)` |
| `avoid[]` | things the model must not draw, added to the negative prompt (e.g. `["long sword"]` for a dagger character) |
| `pose` [standing idle pose] | |
| `view` [side] | `side` (facing right), `front`, `three-quarter`, `back` |
| `proportions` [normal game proportions] | set through `--proportions` rather than by hand |
| `setting`, `art_style` [16-bit], `palette` [limited color palette], `background` [plain white background] | keep the background plain: it is removed |
| `guide_build` | force the side-view mannequin build: `slim`, `normal`, `broad`, `stocky`, `small`, `robed` |
| `guide_head_scale` | bigger mannequin head (set by `--proportions player`) |

Example (`examples/specs/Rogue.json`):

```json
{ "name": "Rogue", "role": "rogue", "species": "human", "gender": "female", "age": "young",
  "body_type": "slim athletic", "hair": "short dark brown hair", "expression": "confident",
  "clothing": ["dark green hood"], "armor": ["lightweight leather armor"], "weapons": ["small dagger"],
  "avoid": ["long sword", "greatsword"], "setting": "medieval fantasy", "view": "side",
  "description": "Young female rogue, short dark brown hair, dark green hood, ..." }
```

## Seeds and recipes

* Every generation has a seed. Default: random (0..2^31-1); `--seed N` fixes it. The **accepted** seed is stored: if
  the quality gate rejects seed N, it tries N+1, N+2, ... and records the rejected ones (`rejected_seeds`).
* `character.json` is the **recipe**: `spec`, `generation` (profile, seed, size, body height, feet margin, colours,
  outline + colour, mirror, guide build, generation size, variation) and `prompt` (the exact positive/negative text).
  Prompts are frozen in the recipe, so later changes to the prompt templates do not change a saved character.
* Same recipe + same models + same workflow files + same ComfyUI version = the **same image, byte for byte**
  (verified on this machine). `metadata.json` stores model files, the workflow hash and the guide hash, so a change in
  any of them is visible.

## Regenerate, vary and reproduce

```bash
./character-generator regenerate --name Rogue --verify                     # reproduce: re-render the recipe, compare, write nothing
./character-generator regenerate --name Rogue                              # re-render the recipe as a variant
./character-generator regenerate --name Rogue --seed 1234                  # variation: other seed -> variants/
./character-generator regenerate --name Rogue --variation "red cape"       # variation: extra prompt text -> variants/
./character-generator regenerate --name Rogue --seed 1234 --replace        # make the variation the main sprite (Rogue.png)
./character-generator regenerate --name Rogue --from-raw --size 96 --replace   # no GPU: re-cut from the stored raw
./character-generator regenerate --name Rogue --from-raw --mirror --replace    # no GPU: flip a left-facing sprite
```

* `--verify` clears ComfyUI's model/result cache first (otherwise ComfyUI returns its cached image), re-renders, and
  reports `sprite_identical`, `sprite_pixels_different`, `raw_identical`, and `workflow_changed` (true when the
  workflow file differs from the one the character was made with).
* A **reproduction** (no `--seed`, `--profile` or `--variation`) uses the recipe's frozen prompts; a **variation**
  rebuilds the prompt from the spec and the current templates.
* `--from-raw` re-runs only the pixel-art processing (size, colours, outline, mirror) on
  `references/source_raw.png`, checked against its hash in `metadata.json`. Seed and profile must stay the same.

## Rig data for animation

After a side-view character is approved, add the data an animation tool needs (details: [rig-data.md](rig-data.md)):

```bash
./character-generator models install --group rig        # once: SDPose-Wholebody (1.9 GB, SHA-256 verified)
./character-generator rig --name Rogue                  # SDPose + inpainting, ~30 s incl. model loads
./character-generator rig --name Rogue --verify         # re-run and compare with the stored files (writes nothing)
./character-generator rig --name Rogue --from-saved     # no GPU: rebuild from rig/source/ (e.g. after tuning)
```

It writes `rig/joints.txt`, `rig/parts.png` (body-part bitmask per pixel), `rig/underlay.png` (pixels hidden behind
the arm, reconstructed with the character's model, prompt and seed), `rig/rig.json` and `rig/source/`. The sprite is
never changed. Run it again whenever the sprite changes (the step refuses a sprite that no longer matches its
recipe).

## References

`--reference <image>` copies an image into `references/` and records it in `metadata.json` (`reference`), but the
current text-to-image profiles do **not** use it for generation yet; it is kept for future identity/style-reference
workflows ([extending.md](extending.md)). Separately, every character keeps its own 1024 px generation in
`references/source_raw.png`: it is the input of post-processing (`--from-raw`) and of the rig step.

## Size and resolution

* `--size` = canvas **height** in px (default 48). The body height is `char_height_ratio x size` (0.9375: 45 px at 48,
  60 at 64, 90 at 96, 120 at 128), so every character has the same scale; `--char-height N` overrides it.
* The canvas widens (even widths) when a weapon or cape is wider than the canvas, and grows taller only when something
  sticks out above the head by more than the 2 px headroom; the character is never shrunk to fit.
* `feet_margin` (default 1) empty rows below the feet; the sprite pivot is bottom-centre.
* `--width/--height` change the **generation** resolution (default 1024x1024, SDXL's native size). Leave them.
* Defaults live in `config/config.json` -> `defaults`, or in the project file's `defaults`.
* Change an existing character's size without the GPU: `regenerate --name X --from-raw --size 64 --replace`.

## Command reference

### generate
| Option | Default | Meaning |
|---|---|---|
| `--name` | from the role | character folder name |
| `--description` / `--spec FILE` | | free text or a spec JSON (one is required) |
| `--profile` / `--fast` | `quality` / `fast` | generation profile ([pipeline.md](pipeline.md#profiles)) |
| `--seed` | random | seed (accepted seed is stored) |
| `--size` | 48 | canvas height: 32, 48, 64, 96, 128, 192 |
| `--char-height` | ratio x size | body height in px incl. outline |
| `--width`, `--height` | 1024 | generation resolution |
| `--colors` | 24 | palette size; 0 = no palette reduction |
| `--outline` / `--no-outline` | on (off for `krea2*`) | 1 px outline |
| `--outline-color` | black | `black` or `darkest` (darkest palette colour) |
| `--view` | side | side, front, three-quarter, back |
| `--style` | 16-bit | art style words |
| `--variation` | | extra prompt text |
| `--mirror` | off | flip horizontally (stored in the recipe) |
| `--proportions` | realistic | `player` = big head (~30 % of the height): closer to a chibi hero, follows descriptions less well |
| `--no-guide` | | side view without the mannequin guide (text-to-image) |
| `--guide-build` | from the spec | slim, normal, broad, stocky, small, robed |
| `--reference FILE` | | stored in `references/` for future reference-based workflows (not used by the current profiles) |
| `--output DIR` | `<output root>/<Name>` | character folder |
| `--variant` / `--overwrite` | | add a variant / replace the main sprite `<Name>.png` |
| `--attempts` | 6 | seeds to try when the quality gate fails; 1 = no retry |
| `--keep-comfyui` | | leave a tool-started ComfyUI running |
| `--dry-run` | | print the request and prompts, no GPU |

### regenerate
`--name` (required), `--output`, `--profile`, `--seed`, `--size`, `--colors`, `--outline/--no-outline`,
`--variation`, `--mirror/--no-mirror`, `--from-raw`, `--replace` (default: write a variant), `--verify`,
`--keep-comfyui`. Changing `--size` recomputes the body height from the ratio.

### rig
`rig --name N [--output DIR] [--no-underlay] [--from-saved] [--verify] [--keep-comfyui]` - rig data for an approved
side-view character (see above). Needs the `rig` model group; the underlay also needs the `quality` profile's models.

### migrate-names
`migrate-names [--name N]` - folders made before 0.4.0 hold `character.png`; renames it to `<Name>.png` (with a
`.meta` sidecar if present, so Unity keeps the asset's GUID) and updates the file names in `metadata.json` and
`rig/rig.json`. Pixels and hashes are unchanged. Generating into such a folder renames it the same way.

### parse
`--name`, `--description` or `--spec`, `--profile`/`--fast`: prints the normalised spec and the positive/negative
prompt. No GPU.

### check
`check SPRITE... [--reference SPRITE]` - metrics per sprite: size, body height/width, top and feet row, binary alpha,
colours, `edge_dark` (share of outline pixels darker than luminance 60), `halo` (light edge pixels), `isolated`
(share of pixels unlike all four neighbours: detail/noise density), `islands`. With a reference (default: the
project's `reference_sprite`) it adds the differences.

### pixelate
`pixelate INPUT OUT [--size] [--colors] [--outline/--no-outline] [--preview FILE]` - run only the pixel-art
processing on any high-resolution image of a character on a plain background (defaults from the config/project).

### benchmark
`benchmark --description/--spec ... [--profiles quality,fast,nova,krea2] [--seeds 1,2,3] [--sizes 48,64,96,128]
[--colors] [--outline/--no-outline] [--keep-comfyui]` - renders every profile x seed (unloading models between
profiles), cuts every size, writes `runs/bench_<timestamp>/` with `results.json` (time, VRAM, prompt, sprite info) and
`contact_sheet.png`. Writes nothing into the output root.

### doctor
Python/Pillow/numpy versions, ComfyUI URL, running or not, launch command, models folder, per-profile model status,
project file, output folder, reference sprite.

### models
`models list [--group G]`, `models install --group G` (groups: sdxl, nova, krea2, smoke). Downloads only when asked.

### comfy
`comfy status | start | stop` (stop only affects an instance this tool started).

### scripts/acceptance.py
`<python> scripts/acceptance.py [--project P] [--reference SPRITE] NAME...` - for existing characters: `lineup.png`
(next to the reference at 4x), `matrix.png` (each re-processed with six outline/palette settings), `metrics.json/.md`.
No GPU. Generate the example set first, e.g. (Git Bash):

```bash
i=1001; for n in DwarfWarrior OldWizard KnightWoman GoblinThief ElfArcher SkeletonWarrior Barbarian Merchant Necromancer Ninja; do
  ./character-generator generate --spec examples/specs/$n.json --seed $i --keep-comfyui; i=$((i+1)); done
./character-generator comfy stop
```

## Output structure

```
<output root>/<Name>/
  <Name>.png                    sprite
  character.json                {"name", "generator_version", "spec", "generation", "prompt"}
  prompt.txt
  metadata.json
  references/source_raw.png     1024 px generation
  references/<file>             a --reference image, if given
  variants/<Name>_<profile>_s<seed>.png, .json (recipe + metadata), _raw.png
  rig/                          after `rig`: rig.json, joints.txt, parts.png, parts_preview.png, underlay.png,
                                source/keypoints.json, source/arm_mask.png, source/inpaint_raw.png (rig-data.md)
```

`metadata.json` fields: `character_name`, `original_description`, `generated_prompt`, `negative_prompt`, `profile`,
`model` (files), `model_details` (manifest entries incl. license), `workflow`, `workflow_sha256_16`, `guide` (build,
file, sha256, denoise), `sampler_params`, `seed`, `requested_seed`, `rejected_seeds`, `quality_check` (ok, reasons,
components, foreground fraction, symmetry, torso width), `warnings`, `generation_resolution`, `final_resolution`,
`palette_colors_requested`, `postprocess` (background colour, source box, cell size, character size, body height,
palette size, canvas, feet row, pivot, outline), `variation`, `reference`, `timestamp`, `generator_version`,
`comfyui_version`, `device`, `seconds_total`, `seconds_execution`, `vram` (baseline, peak), `files`, `sha256`
(sprite, raw), `run_dir`.
