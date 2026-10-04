# How the pipeline works

```
spec ─► prompt builder ─► profile + workflow ─► ComfyUI ─► raw 1024 px ─► quality gate ─► pixel-art processing ─► files
          prompts/*.txt     config/profiles.json   HTTP API               (retry next seed)
                            workflows/*.json        + side-view guide (img2img)
```

## 1. Spec and prompt builder (`chargen/spec.py`, `chargen/prompt_builder.py`, `prompts/`)

* A **spec** is a dict of fields ([usage.md](usage.md#character-spec)). `parse_description` is a deterministic
  keyword parser (hair, armor, weapons, clothing, body type, expression, setting, identity words such as gender, age,
  species and role); unknown phrases go to `details`. The Claude skill writes specs directly, which is more accurate.
* The prompt is rendered from **templates with one segment per line**; a segment is dropped when all its `{fields}`
  are empty, so prompts never contain dangling words and every character's prompt has the same structure (consistent
  look). `prompts/tags.txt` is for CLIP models (SDXL), `prompts/natural.txt` for LLM text encoders (Krea 2),
  `prompts/negative.txt` is the negative prompt. The profile adds a prefix (e.g. `pixel art`).
* Side view: `(side view:1.4), (from side:1.3), profile, facing right` plus weighted front/back negatives. Weapons are
  weighted `(…:1.3)`; `avoid` items go into the negative prompt.

<a id="profiles"></a>
## 2. Profiles and workflows (`config/profiles.json`, `workflows/`, `chargen/workflow.py`)

| Profile | Workflow (side view / other views) | Model | Settings |
|---|---|---|---|
| **quality** (default) | `sdxl_pixelart_guided.json` / `sdxl_pixelart.json` | SDXL 1.0 + Pixel Art XL (LoRA 1.2) | 30 steps, dpmpp_2m karras, CFG 6, denoise 0.92 (guided) |
| **fast** | `sdxl_pixelart_lcm_guided.json` / `sdxl_pixelart_lcm.json` | + LCM LoRA | 8 steps, lcm/sgm_uniform, CFG 1.5 |
| nova | `checkpoint_simple.json` | Nova Pixels XL v3.0 | 28 steps, euler_a, CFG 5, clip skip 2 |
| krea2, krea2-64, krea2-32 | `krea2_pixelart.json` | Krea 2 Turbo fp8 + Krea-2-Pixel-Art LoRA | 8 steps, euler, CFG 1; no outline step (the LoRA draws one). For 16 GB+ GPUs |
| smoke | `checkpoint_simple.json` | SD 1.5 | plumbing test only |

Workflows are ComfyUI **API-format** JSON with placeholders: a value that is exactly `"{{name}}"` is replaced by a
typed value (numbers stay numbers); `{{name}}` inside a longer string is replaced as text; a missing value is an error.
Common placeholders: `positive`, `negative`, `seed`, `width`, `height`, `prefix` (output file name), `guide`
(uploaded guide image), plus each profile's `params` (checkpoint, lora, steps, cfg, sampler, scheduler, denoise, ...).
The guided workflows decode and encode **tiled** (512 px tiles) to keep the VRAM peak low.

### ComfyUI client (`chargen/comfy.py`)
`/system_stats` (alive?), `/upload/image` (guide), `/prompt` (queue; HTTP 400 node errors are reported per node),
`/history/<id>` (poll until done; execution errors are reported with the node), `/view` (download the image),
`/free` (unload models / clear caches). Process handling: start the portable build
(`python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --port <port> <extra_args>`) only if nothing
answers; stop only what it started. When the **profile changes** between runs on the same server, models are unloaded
first: ComfyUI 0.38 otherwise reuses a cached LoRA-patched SDXL and renders pure noise.

### Running a workflow by hand
Open ComfyUI's web UI, use *Load* on a file from `runs/<run>/workflow_api.json` (the exact workflow that was submitted,
placeholders filled), and press Queue. Or rebuild it from the nodes: Load Checkpoint `sd_xl_base_1.0` -> Load LoRA
`pixel-art-xl` (1.2/1.0) -> two CLIP Text Encode -> Empty Latent 1024x1024 -> KSampler (30, 6.0, dpmpp_2m, karras)
-> VAE Decode -> Save Image. `character-generator pixelate raw.png sprite.png --preview big.png` turns the result into a
sprite.

## 3. Side-view guide (`chargen/guide.py`, `references/guides/`)

Text alone gives a side view for about 1 in 10 characters (SDXL prefers front and three-quarter views). For side
views the sampler starts from a **grey mannequin in strict profile facing right** instead of noise (img2img,
denoise 0.92): it fixes the camera and the facing, the model redraws everything else.

* The mannequin carries profile cues: face profile (nose, chin), one eye, hair at the back of the head, flat back and
  chest forward, a stride with long feet pointing right, near arm in front, lighter front.
* It must stay **narrow** (body depth 0.15-0.21 of the height): wider guides were read as front views.
* **Builds** (`slim`, `normal`, `broad`, `stocky`, `small`, `robed`) differ in head size, leg length and depth; the
  build is chosen from `body_type`/`species`/`role`/`clothing` keywords (`choose_build`) or forced with `guide_build`.
  `--proportions player` scales the head by 1.45.
* Guides are drawn procedurally and deterministically, written to `references/guides/side_right_<build>.png`, uploaded
  to ComfyUI's `input/chargen/`, and their sha256 goes into the metadata.
* Results: 9/10 strict side views and 10/10 facing right in the acceptance test ([model-benchmark.md](model-benchmark.md)).

## 4. Quality gate (`pixelate.analyze`, used by `generator._render_checked`)

Run on every raw image; if it fails, the next seed is tried (`--attempts`, default 6). After the last attempt the image
is kept and the result carries a `warnings` entry.

| Check | Rule |
|---|---|
| no character | the main figure covers < 1 % of the image |
| background not separable | > 60 % of the image is foreground (scenery, gradients, noise) |
| sprite sheet / several characters | more than one figure with >= 10 % of the largest one's area |
| cropped | > 2 % of the image border is foreground |
| too small | figure < 35 % of the image height |
| front/back view (side-view requests only) | silhouette mirror symmetry >= 0.78 **and** torso width >= 0.245 of the height |

The side-view rule was calibrated on 71 labelled images: it rejects all front/back views and 1 of 53 side views.
It cannot tell three-quarter from side views (both are asymmetric), and facing left/right is not checked
(heuristics were unreliable): the guide makes both rare, and the skill reviews the preview.

## 5. Pixel-art processing (`chargen/pixelate.py`)

From the 1024 px image to the sprite, deterministic (Pillow + numpy):

1. **Background**: the border's median colour; flood fill from the border over pixels within distance 40 (RGB).
2. **Enclosed background** (between legs, arm loops): regions within distance 14 of the background colour that are at
   least 0.04 % of the image are removed too (small highlights stay).
3. **Halo**: the background grows two steps into pixels within distance 88 (anti-aliased fringe).
4. **Floor shadow**: low-chroma (< 28), mid-luminance pixels in the bottom 18 % of the figure that touch the background.
5. **Main figure**: detached specks smaller than 10 % of the largest part are ignored.
6. **Scale from the body**: the body top is the first row at least 12 % as wide as the typical row (staff and bow tips
   do not count), so the body is exactly `char_height` px tall (outline included) for every character.
7. **Palette**: k colours (default 24) by median cut + k-means over the figure's pixels only, no dithering.
8. **Grid collapse**: each output pixel = the most frequent palette colour in the central 60 % of its source cell;
   alpha = majority of the cell. Every output pixel is one game pixel (uniform pixel size by construction).
9. **Cleanup**: opaque islands < 3 px are removed.
10. **Outline**: 1 px ring (4-neighbour) around the figure in black (or the darkest palette colour).
11. **Placement**: feet on row `size - 1 - feet_margin`, horizontally centred (bottom-centre pivot); the canvas widens
    for wide characters and grows upward only for protrusions above the 2 px headroom.
12. **Mirror** (optional): horizontal flip.

Why the outline and palette are on by default: measured against a hand-made 48 px player sprite, the defaults match its
outline darkness (1.00 vs 0.97), halo (0) and detail density (0.30 vs 0.30); without the outline edges turn grey with a
3-5 % halo; without the palette the sprite has ~300 colours and 2.5x the noise ([model-benchmark.md](model-benchmark.md)).

## 6. Rig data (`chargen/rig.py`, `rig` command)

Optional, after approval, side views only ([rig-data.md](rig-data.md)):

1. SDPose-Wholebody keypoints on `references/source_raw.png` (`workflows/sdpose_keypoints.json`, text output).
2. Near side = the body side with the higher keypoint confidence; part labels at 1024 px (capsules around the measured
   limbs, weapon by connectivity to the near hand, far-hand items with the far arm, legs = nearest leg chain, robes stay
   Body except the feet, far arm only outside the torso).
3. Hidden pixels: the near arm/weapon area over the torso (per-row torso span) is pre-filled from torso colours and
   inpainted with the character's own model, prompt and seed (`workflows/sdxl_inpaint_underlay.json`, denoise 0.55,
   negatives against arms/hands); pixels outside the mask are kept exactly.
4. Both rasters go through `pixelate` as **layers**: sampled cell by cell on exactly the sprite's grid in the same pass
   (the sprite is unchanged and re-checked). The underlay uses the sprite's palette and only colours of the visible
   torso. Joints are mapped with the recorded raw -> sprite transform.
5. Everything needed to reproduce the step is stored (`rig.json -> recipe`, `rig/source/`).

## 7. Metrics (`chargen/metrics.py`, `check` command)

`char_height`, `char_width`, `top_row`, `feet_row`, `alpha_binary`, `colors`, `edge_dark`, `halo`, `isolated`
(detail/noise density), `islands`, `largest_island_share`. Used by `check` and `scripts/acceptance.py`.

## Code map

| Module | Responsibility |
|---|---|
| `chargen/cli.py` | argument parsing, commands |
| `chargen/config.py` | tool config, local overrides, env, project file |
| `chargen/spec.py` | spec fields, keyword parser, validation, safe names |
| `chargen/prompt_builder.py` | templates -> prompts, view phrases and negatives |
| `chargen/workflow.py` | load workflows, fill placeholders, hash |
| `chargen/comfy.py` | ComfyUI HTTP client, upload, start/stop, session |
| `chargen/guide.py` | side-view mannequin builds and drawing |
| `chargen/generator.py` | requests/recipes, rendering with retries, output files, metadata, reproduction |
| `chargen/pixelate.py` | background/shadow/halo/hole removal, quality gate, scaling, palette, grid, outline, preview |
| `chargen/metrics.py` | sprite metrics |
| `chargen/rig.py` | rig data: SDPose keypoints, near/far, part labels, inpainted underlay, joints, rig.json |
| `chargen/bench.py` | benchmark runs and contact sheets |
| `chargen/models.py` | model manifest status and downloader |
