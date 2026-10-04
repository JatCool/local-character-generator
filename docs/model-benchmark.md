# Model benchmark and choice (2026-10-04)

**Default: SDXL 1.0 + Pixel Art XL v1.1 LoRA (`quality`), fast mode: + LCM LoRA (`fast`)**, side views started from a
side-view mannequin guide (see the acceptance test at the end: 9/10 strict side views, all facing right, every
character at the player's scale). Alternatives kept: `nova` (Nova Pixels XL v3.0) and `krea2` / `krea2-64` /
`krea2-32` (Krea 2 Turbo + Krea-2-Pixel-Art), the latter documented for future testing on a 16 GB+ GPU.

Machine: RTX 3080 **10 GB** (about 1.8-2.6 GB already used by Windows and open Unity editors), 32 GB RAM,
ComfyUI 0.38.0 portable, PyTorch 2.14 + CUDA 13. Every candidate was judged on the **final sprite**
(64/96/128 px after post-processing), not on the 1024 px image.

Test character: *"Young female rogue, short dark brown hair, dark green hood, lightweight leather armor, small dagger,
slim athletic body, confident expression, medieval fantasy game character."* Seeds 1-3 (round 1), 1-4 / 1-3
(round 2). Reproduce with `character-generator benchmark --description "..." --profiles ... --seeds 1,2,3`.

## Results

| | SDXL + Pixel Art XL (`quality`) | + LCM (`fast`) | Nova Pixels XL v3 | Krea 2 + Pixel Art 128 |
|---|---|---|---|---|
| Time per image (warm / first) | **11-12 s** / 14-17 s | **4 s** / 6-8 s | 12 s / 13 s | 22 s / 26 s (one 118 s spike from paging) |
| Peak GPU memory (whole card) | 7.2-9.7 GB | 7.2-9.2 GB | 9.5 GB | 9.5-9.9 GB (12 GB model streamed from RAM) |
| Download | 7.1 GB (+0.4 GB LCM) | shared | 6.9 GB | 17.4 GB |
| Side view, facing right (asked) | **yes: 4 of 4 with normal proportions** (one faced left -> `mirror`); 0 of 3 with a "chibi" spec | mostly three-quarter / back | front / three-quarter | front / three-quarter, even with an explicit "strict profile" prompt |
| Single character (quality gate) | 5 of 7 (1 sprite sheet per round) | 5 of 7 (backgrounds, a loose sword) | 2 of 3 (one pair) | **3 of 3** |
| Pixel consistency at 64 px | good; a little texture noise | noisier, dithered | very clean | **cleanest**: flat colours, real grid |
| Silhouette / readability at 64 px | good with the outline step | fair | good | **best** (thick outline, big head) |
| Anatomy / proportions | realistic, tall; good | ok | anime, long legs | chibi, very consistent |
| Prompt adherence (hood, armor, dagger) | hood yes, dagger often became a sword (fixed with weighting + `avoid`) | weak | revealing outfit instead of armor | **hood + dagger 3/3** |
| Background handling | flat grey/white + soft shadow (removed) | sometimes striped backgrounds | white, sometimes a ground patch | white + small shadow (removed) |
| Consistency between seeds | similar design, varying detail | varies | varies | **almost the same character every seed** |
| License | OpenRAIL++ / OpenRAIL-M | same | Illustrious license | Krea 2: free commercial use only under $1M revenue and 50 seats |

Krea-2-Pixel-Art LoRA variants (round 2, same spec): **128** = most detail, keeps the dagger; **64** = cleaner and
simpler, dropped the dagger, less chibi; **32** = very chunky, the grid is coarser than a 64-128 px sprite needs (each
model pixel becomes 2-4 sprite pixels). All three show the same identity, which makes Krea 2 the strongest base for
future consistency work, if the VRAM and license fit.

Images: [round 1 SDXL/fast/Nova](images/bench-round1-sdxl-fast-nova.jpg), [round 1 Krea 2](images/bench-round1-krea2.jpg),
[round 2 SDXL with chibi spec + outline](images/bench-round2-sdxl-chibi-outline.jpg),
[round 2 Krea 2 LoRA 128/64/32](images/bench-round2-krea2-lora-variants.jpg),
[64 px sprites next to the game's player at game scale](images/game-scale-vs-player.jpg),
[final Rogue (default, 128 px, fast) next to the player](images/rogue-result.png).
Columns: raw 1024 px, each size zoomed, each size at 2x.

## Why SDXL + Pixel Art XL is the default

1. **It is the only candidate that follows "side view, facing right".** The game is a side-scroller, sprites face
   right (`flipX` mirrors them), and the AI Sprite Animation rig needs a side-view sprite. Wrong facing is fixed
   losslessly with `--mirror`; a wrong camera angle cannot be fixed.
2. It fits 10 GB with headroom and needs no RAM streaming; 11 s per image, 4 s in fast mode with the same models.
3. Permissive license, no revenue cap.
4. Its weaknesses are handled by the pipeline: the quality gate re-rolls sprite sheets/backgrounds, the shadow and
   halo passes clean the edges, the outline step restores a readable silhouette at 64 px, weapon weighting and the
   `avoid` field stop "dagger -> sword".

**Krea 2** produced the best pixel art and the closest match to the player's chibi style, but it ignores the side
view, sits at the VRAM limit of a 10 GB card (and hits 100 s+ when Windows needs memory), takes twice as long and has
a revenue-capped license. Use `--profile krea2` for front-facing characters (portraits, NPCs, UI) or when the
art-style match matters more than the view. On a 16 GB+ card it would be the first thing to re-evaluate as default.
**Nova Pixels XL** gives clean pixels but drifts to anime/front views and ignores clothing descriptions.
**Fast** is for iteration: same models, about 3x faster, weaker prompt adherence.

## Findings that shaped the pipeline

* Generating at 1024 and reducing with a palette + per-cell mode (not plain nearest-neighbour) gives a uniform pixel
  size by construction: every output pixel is one game pixel.
* "game sprite" in the prompt triggers sprite sheets in SDXL; "video game character, solo" + sheet negatives reduced
  them. The gate (separate figures >= 10 % of the main one, figure touching the border, >60 % foreground) matched
  my visual verdict on all 31 benchmark images (0.25 missed one sheet; 0.05-0.12 had no mismatch).
* "chibi proportions" makes SDXL turn the character to the front: keep `normal game proportions` for side views.
* **ComfyUI 0.38 renders pure noise** when the same SDXL checkpoint is reused with a different LoRA chain
  (quality <-> fast) in one session. The generator unloads models whenever the profile changes.
* ComfyUI caches identical workflows, so a reproduction test must clear the cache first (`regenerate --verify` does).
  Verified: a fresh ComfyUI re-rendered the stored recipe byte-identically (raw PNG hash and sprite pixels).

## Not selected (not downloaded)

| Model | Why not (for this machine / goal) |
|---|---|
| FLUX.1 dev / Kontext | 12B, needs offloading on 10 GB; non-commercial dev license; no pixel-art advantage over the LoRAs above |
| FLUX.2 [klein] 4B | Apache 2.0, about 8 GB, multi-reference **editing**: the best candidate for a later "same character, new pose/equipment" stage, not needed for first-pass generation |
| Qwen Image | 20B, too large for 10 GB |
| Z-Image Turbo | 6B, good photoreal/illustration model; no proven pixel-art LoRA advantage; worth a test later |
| PixelDiT (NVIDIA) | "pixel-space" diffusion (no VAE), not pixel-art; irrelevant here |

---

# Acceptance test: 10 characters at the player's scale (2026-10-04)

Goal: production readiness. Ten different characters (body types, species, clothing, weapons, no weapon), 48 px
default, required **side view facing right**, compared with `Assets/Art/Player/player_east.png`. Specs:
`runs/specs/<Name>.json` (DwarfWarrior, OldWizard, KnightWoman, GoblinThief, ElfArcher, SkeletonWarrior, Barbarian,
Merchant, Necromancer, Ninja), seeds 1001-1010. Re-run the sheet with `scripts/acceptance.py <Names...>`.

## Result per round

| Round | Change | Strict side view, facing right | Notes |
|---|---|---|---|
| 1 | text prompt only ("side view") | **1/10** | 9 front / three-quarter; 2 sprite sheets re-rolled by the gate ([sprites](images/acceptance-round1-48px.png)) |
| - | weighted prompt `(side view:1.4)` + weighted negatives | 0/6 (problem characters) | the prompt cannot fix the camera |
| - | img2img from a thin grey side-view mannequin, denoise 0.80/0.88/0.94 | 18/18 in the experiment | denoise 0.92 keeps most of the description; 0.80 copies the mannequin too literally |
| 2 | mannequin per build (slim ... stocky), symmetric | 5/10 | wide/robed guides read as front views; one ComfyUI stall in the VAE decode -> tiled VAE + `--reserve-vram 1.5` |
| 3 | profile mannequin (face, eye, hair at the back, stride, feet right) | 6/10 (+2 three-quarter) | still too wide for stocky/broad/robed ([denoise does not help](images/guide-width-failure-denoise-sweep.jpg)) |
| **4 (final)** | **narrow profile mannequin for every build** (depth 0.15-0.21 of the height) + front/back gate | **9/10** (Dwarf three-quarter) | all 10 face right; 1 front view re-rolled by the gate ([raw](images/acceptance-final-raw.jpg), [48 px](images/acceptance-final-48px.png)) |

The guide must stay narrow: [the same four characters with the narrow guide](images/guide-narrow-success.jpg) are
8/8 strict side views; their bulk comes from the prompt. Builds now differ by head size and leg length
([guides](images/guide-builds.png)).

## Consistency metrics (final round, `character-generator check`)

| | Player | 10 generated characters |
|---|---|---|
| Canvas / body height / top row / feet row | 48x48 / 45 / 2 / 46 | **48x48 / 45 / 2 / 46 for all 10** |
| Transparency | binary | binary, 1 island each (no specks) |
| Outline darkness (edge pixels with luminance < 60) | 0.97 | 1.00 |
| Halo (light edge pixels) | 0 | **0** |
| Detail density (pixels unlike all 4 neighbours) | 0.30 | **0.30 average** (0.26-0.38) |
| Colours | 51 | 24-25 |
| Unity import | PPU 16, Point, pivot (24, 0) | 10/10 identical settings, pivot (24, 0), 3 units tall |
| Reproduction (`regenerate --verify`, fresh ComfyUI) | | byte-identical (KnightWoman, DwarfWarrior incl. a gate re-roll) |

## Outline and palette reduction: do they help?

Every character re-processed from the same raw image with six settings ([matrix](images/outline-palette-matrix.jpg)):

| Variant | Outline darkness | Halo | Detail density | Colours |
|---|---|---|---|---|
| **palette 24 + black outline (default)** | 1.00 | 0.000 | **0.30** (player 0.30) | 25 |
| no outline | 0.66 | 0.049 | 0.40 | 23 |
| no palette reduction | 1.00 | 0.000 | 0.75 | 309 |
| no palette, no outline | 0.66 | 0.034 | 0.99 | 336 |
| darkest-colour outline | 1.00 | 0.000 | 0.30 | 24 |
| palette 48 | 1.00 | 0.000 | 0.39 | 44 |

Both steps improve consistency and bring the sprites to the player's style: without the outline the silhouettes go
soft with a light fringe; without palette reduction the sprites are 2.5x noisier than the player. Defaults: palette 24
+ **black** outline (the player's outline is black). All remain switches (`--no-outline`, `--colors 0/48`,
`--outline-color darkest`).

## Remaining differences and known limits

* **Proportions**: the player is semi-chibi (head ~30 % of the height). Default characters have realistic proportions
  (head ~19 %). `--proportions player` uses a big-head guide and prompt: closer to the player's look, 8/10 strict side
  views, but it drops some described items (Dwarf lost helmet and axe, Wizard's blue robe turned brown)
  ([comparison](images/proportions-realistic-vs-player.png)). Default stays `realistic`.
* **Facing** is set by the guide (10/10 right in the final round) but not verified automatically (a feet-direction
  heuristic was wrong on 2/7 labelled images). The skill checks it visually; `regenerate --from-raw --mirror` fixes it
  losslessly without the GPU.
* **Three-quarter views** pass the gate (symmetry cannot tell them from side views); about 1 in 10 (stocky dwarf).
  Re-roll with `--seed` if it matters.
* **Small items** are sometimes dropped (Skeleton's shield). Name them first in `weapons` or use `avoid`.
* **Width**: generated side views are 10-18 px wide; the player is 33 px including ponytail and sword. Expected for
  realistic side views; the canvas widens automatically for long weapons.
