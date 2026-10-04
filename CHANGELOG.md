# Changelog

All notable changes. Versions follow semantic versioning; `generator_version` in every `metadata.json` / `rig.json`
records which version made a file.

## 0.4.0 - 2026-10-04
* **Unique sprite names:** the main sprite is `<Name>.png` (was `character.png` in every folder), so anything named
  after the sprite file, e.g. the AI Sprite Animation package's `Assets/Animations/Generated/<sprite name>/`, is unique
  per character and generating one character never overwrites another's animations. Names that differ only in letter
  case are refused. Readers fall back to a legacy `character.png`; `migrate-names` (and any write into such a folder)
  renames it with its `.meta`, keeping the asset id. `rig.json -> sprite.file` and `metadata.json -> files.sprite`
  follow.
* **Held items stay attached** (rig data, `attach_held_items`, on the sprite grid): far-hand pieces without a visible
  arm move with the Body; stray arm-part fragments take the label around them; every Weapon piece shares an edge with
  the near hand, with gaps of up to `rig.held_item_max_gap_px` (3) Body or leg pixels bridged; underlay pieces that
  touch no visible body are dropped. Reported in `rig.json -> decisions.held_items`; `weapon_found` reflects the final
  map.
* **WeaponPivot = the grip:** the centre of the item/hand contact instead of the item pixel nearest to the SDPose
  wrist, so a rotating item keeps touching the hand.
* Floating pieces over eight animations on 12 characters: 113 -> 0 for held items, far-hand items and limb fragments
  (the robe/Run case was fixed in the package 1.7.0 by a smaller Run lean for robed rigs: docs/animation-investigation.md). Tests: 49 (fixtures of four real
  part maps in `tests/fixtures/rig/`).

## 0.3.0 - 2026-10-04
* `rig` command: rig data for approved side-view characters in `<character>/rig/` (format `chargen-rig/1`,
  docs/rig-data.md): SDPose joints in sprite pixels, per-pixel body-part map, hidden-pixel underlay reconstructed by
  inpainting with the character's model, prompt and seed (palette-restricted to the torso's colours), robe handling,
  animation hints; `--from-saved` (no GPU) and `--verify` (byte-identical reproduction).
* `models install --group rig` (SDPose-Wholebody, SHA-256 verified).
* Pixel-art layers: extra rasters sampled on exactly the sprite's grid (the sprite itself is unchanged).
* Standalone repository: MIT license, CONTRIBUTING, third-party license overview, project files instead of
  game-specific defaults.
* ComfyUI model unloading (`/free`) waits until the worker is idle before the next job, so a following inpaint or
  `--verify` run no longer stalls on a half-unloaded model.
* Rig data is consumed by the AI Sprite Animation package 1.7.0 (part map, underlay, `leg_swing_scale`); results in
  docs/animation-investigation.md.

## 0.2.0 - 2026-10-04
* Player-scale layout (48 px canvas, 45 px body, feet row 46), side-view mannequin guides, front/back-view gate,
  outline/palette defaults from the acceptance test, `--proportions player`, `check` metrics, recipes with frozen
  prompts, project file (`--project`), tiled VAE and `--reserve-vram` against VRAM stalls.

## 0.1.0 - 2026-10-04
* First version: description -> spec -> prompt -> ComfyUI (SDXL + Pixel Art XL) -> pixel-art post-processing ->
  character folder with recipe and metadata; quality gate; benchmark; reproduction check.
