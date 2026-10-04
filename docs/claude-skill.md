# The Claude Code `generate-character` skill

A Claude Code skill is a Markdown file with instructions that Claude loads when a request matches its description.
A generic, project-neutral template ships with this repository:
[`integrations/claude-code/generate-character/SKILL.md`](../integrations/claude-code/generate-character/SKILL.md)
(copy it to `<project>/.claude/skills/generate-character/` and fill in two paths).
The game project ships one for this generator: **`<game>/.claude/skills/generate-character/SKILL.md`**. It is part of
the consuming project (not of this generator) because it encodes that project's conventions (paths, the player as
reference, the approval loop); this page documents it so it can be maintained or copied to another project.

## What it does

When the user asks for a new character ("generate character ...", "generate character fast ...", "create a new enemy
sprite ...", also in Ukrainian), Claude:

1. **Builds a spec** from the user's words and saves it as `../local-character-generator/runs/specs/<Name>.json`
   (git-ignored). Claude fills the fields itself (more accurate than the keyword parser), keeps the user's words, adds
   nothing the user did not ask for, and stores the original text in `description`.
2. **Runs the generator** from the game root:
   `../local-character-generator/character-generator --project . --json generate --spec ../local-character-generator/runs/specs/<Name>.json`
   adding `--fast` for "generate character fast", `--proportions player` when the user wants the player's big-head
   look, `--seed`, `--size`, `--variant`/`--overwrite` as needed.
3. **Reads the JSON result**: `ok`, `sprite`, `project_path`, `seed`, `rejected_seeds`, `warnings`, `preview`
   (8x preview PNG in `runs/`), `seconds`, `prompt`. A non-empty `warnings` means every seed failed the quality gate.
4. **Looks at the preview** and fixes clear problems before showing it: facing left -> `regenerate --from-raw --mirror
   --replace` (no GPU); wrong item -> add to `avoid` and regenerate; three-quarter view or other failures -> another
   seed. Optional sanity check: `character-generator --project . check <sprite>` against the player.
5. **Unity**: the files land in `Assets/GeneratedCharacters/<Name>/`; the importer applies the pixel-art settings.
   If the unity-mcp tools are available, Claude refreshes the AssetDatabase and checks the console.
6. **Reports** name, path, seed, size, palette, time, assumptions, and asks for approval. The result is a draft until
   the user approves it (approval loop: variants with other seeds or spec changes, `regenerate --verify` to prove
   reproducibility).

7. **After approval**: runs the rig step (`... --project . --json rig --name <Name>`) so the character is ready for
   animation, checks `warnings` and `decisions` (near side, robed, weapon) and the `rig/parts_preview.png` picture, and
   reports it. The animation itself is a separate step in the animation project.

Hard rules in the skill: local only (no cloud APIs), never modify the animation package, never download models
without the user's OK, never overwrite an existing `<Name>.png` unless asked, keep pixel art unfiltered.

## Input format

The user's free text. Claude converts it to the spec format documented in [usage.md](usage.md#character-spec):
identity (`role`, `species`, `gender`, `age`), appearance (`body_type`, `face`, `hair`, `expression`), equipment
lists (`clothing`, `armor`, `weapons`, `accessories`, `colors`, `details`), `avoid`, `view` (default `side`),
optional `guide_build`. Example: [`examples/specs/Rogue.json`](../examples/specs/Rogue.json).

## How it invokes the generator

Only through the CLI, always with `--project .` (the game's `character-generator.project.json`: output root
`Assets/GeneratedCharacters`, 48 px layout, black outline, 24 colours, player as reference) and `--json`. It never
imports the Python code and never talks to ComfyUI directly, so the generator can change internally without touching
the skill as long as the CLI and the JSON result stay compatible.

Commands the skill uses:

| Purpose | Command (from the game root) |
|---|---|
| generate | `../local-character-generator/character-generator --project . --json generate --spec <spec> [--fast] [--seed N] [--size N] [--variant\|--overwrite] [--proportions player]` |
| dry run | `... generate --spec <spec> --dry-run` |
| fix facing / size / palette without GPU | `... --project . regenerate --name <Name> --from-raw [--mirror] [--size N] [--colors N] --replace` |
| variation | `... --project . regenerate --name <Name> --seed <N> [--variation "..."]` |
| reproduce | `... --project . regenerate --name <Name> --verify` |
| rig data after approval | `... --project . --json rig --name <Name>` (`--verify` to prove reproduction, `--from-saved` without GPU) |
| compare with the player | `... --project . check Assets/GeneratedCharacters/<Name>/<Name>.png` |
| missing models | tell the user `... models install --group <group>` (never run it unasked) |

PowerShell uses `tools\local-character-generator\character-generator.cmd` with the same arguments.

## Example prompts

* `generate character: an old blind monk with a long white beard, grey robe and a walking stick`
* `generate character fast: goblin archer with a crooked bow`
* `make a variant of KnightWoman with a blue cape`
* `the Rogue faces left, fix it` (-> `regenerate --from-raw --mirror --replace`)
* `approve the Merchant and prepare it for animation` (-> `rig --name Merchant`)
* `prove that the Ninja can be reproduced` (-> `regenerate --verify`, `rig --verify`)
* Ukrainian works too: `згенеруй персонажа: гном-коваль з молотом`

## Changing or adding commands

* **A new CLI feature** (e.g. a new option): implement it in the generator (`chargen/cli.py` + module), document it in
  [usage.md](usage.md), then add one line to the skill where the user's intent maps to it (Step 2 options list).
* **A new trigger phrase** (e.g. "make an enemy"): extend the `description:` line in the skill's front matter; that
  line decides when Claude loads the skill.
* **A new workflow step** (e.g. "after approval, prepare the rig"): add a numbered step to the skill and keep the hard
  rules intact.
* **Another project**: copy `SKILL.md`, replace the paths (`../local-character-generator`, the project folder,
  `Assets/GeneratedCharacters`, the reference sprite) and the engine-specific Step 4.
* Keep the skill in sync with the CLI: if an option is renamed, update every command line in the skill.

A Unity-free alternative entry point for scripts and other agents is the CLI itself with `--json`.
