---
name: generate-character
description: Generate a new static pixel-art game character (a transparent sprite PNG with a reproducible recipe) from a text description with the LOCAL image AI (ComfyUI on this machine) using the Local Character Generator, and prepare approved characters for animation (rig data). Use for "generate character", "generate character fast", "create/make/draw a new character/enemy/NPC sprite", variants of a generated character, reproducing one, or "prepare <character> for animation". Never use a cloud image API.
---

# Generate a pixel-art character (Local Character Generator)

TEMPLATE: copy this folder to `<your project>/.claude/skills/generate-character/` and replace the two placeholders:
`<GEN>` = path to the generator from the project root (e.g. `../local-character-generator` when it is cloned next to the project), `<PROJECT>` = the
folder that contains your `character-generator.project.json` (usually `.`). Full documentation: `<GEN>/README.md`,
`<GEN>/docs/usage.md`, `<GEN>/docs/claude-skill.md`.

## Steps

1. **Spec**: turn the user's words into a spec JSON (`<GEN>/runs/specs/<Name>.json`): `name` (PascalCase), `role`,
   `species`, `gender`, `age`, `body_type`, `face`, `hair`, `expression`, lists `clothing`, `armor`, `weapons`,
   `accessories`, `colors`, `details`, `avoid`; `view` = `side` unless asked otherwise; `description` = the user's text.
   Do not invent items the user did not mention.
2. **Generate** (from the project root):
   `<GEN>/character-generator --project <PROJECT> --json generate --spec <GEN>/runs/specs/<Name>.json`
   (`--fast` for "generate character fast"; `--seed N`, `--size N`, `--variant` / `--overwrite` as asked;
   PowerShell: `<GEN>\character-generator.cmd`). Long timeout (first run loads models). Read `ok`, `error`,
   `warnings`, `rejected_seeds`, `preview`, `seed`.
3. **Look** at the `preview` PNG: one full-body character, requested view, facing right for side views, items present.
   Fix before showing: facing left -> `regenerate --name <Name> --from-raw --mirror --replace`; wrong item -> add it to
   `avoid` and regenerate; three-quarter or broken -> another seed.
4. **Report** path, seed, size, assumptions; ask for approval. It is a draft until approved.
5. **After approval** (side views): `<GEN>/character-generator --project <PROJECT> --json rig --name <Name>` and report
   `warnings`, `decisions` and `rig/parts_preview.png`.

Other commands: variants `regenerate --name <Name> --seed N [--variation "..."]`; reproduce `regenerate --name <Name>
--verify`, `rig --name <Name> --verify`; compare with a reference sprite `check <sprite>`; missing models: tell the user
`models install --group <group>` (never download unasked).

## Rules

Local only; never overwrite an existing `<Name>.png` unless asked; keep pixel art unfiltered; do not change the
generator's code to fix a single character.
