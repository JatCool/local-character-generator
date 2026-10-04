# Integration with a project (Unity example)

The generator does not depend on any project. A consumer integrates it in three thin layers:

1. a **project file** that says where characters go and which defaults the project uses;
2. something that **imports** the generated files (e.g. a Unity `AssetPostprocessor`);
3. optional front ends that **invoke** the CLI (an editor window, a Claude Code skill, a build script).

## Project file

`character-generator.project.json`, usually in the project root. Paths are relative to the file.

```json
{
  "name": "The Legacy of Shadows",
  "output_root": "Assets/GeneratedCharacters",
  "reference_sprite": "Assets/Art/Player/player_east.png",
  "defaults": { "size": 48, "char_height_ratio": 0.9375, "feet_margin": 1, "colors": 24,
                "outline": true, "outline_color": "black", "proportions": "realistic", "view": "side" }
}
```

| Key | Meaning |
|---|---|
| `generator` | where this generator is checked out, relative to the project file; only read by the project's front ends (skill, editor window), ignored by the generator |
| `output_root` | characters are written to `<project>/<output_root>/<Name>/` (default `GeneratedCharacters`) |
| `reference_sprite` | default reference for `check` and `scripts/acceptance.py` (e.g. the hero sprite the style must match) |
| `defaults` | overrides `config/config.json` -> `defaults` (any key) |

Pass it explicitly: `character-generator --project <project folder or file> ...` (or set `CHARGEN_PROJECT`). Paths in
`metadata.json` (`files`) are then relative to the project. Nothing is discovered implicitly: without `--project`
characters go to the generator's own `output/` folder.

## Two repositories

The generator and a consuming project are **separate repositories**. The recommended layout is side by side:

```
Projects/
  local-character-generator/     this repository (code, workflows, models manifest, tests, docs)
  MyGame/                        the consumer: only a project file + import settings + optional front ends
    character-generator.project.json   { "generator": "../local-character-generator", "output_root": ..., ... }
```

* Cloning the generator alone is enough to generate characters (they go to its `output/` folder).
* Cloning the game gives the generated characters (they are ordinary assets) but no generator code; to generate new
  ones, clone the generator next to it (or anywhere, and set `generator` in the project file).
* The generator never reads anything from the game except the project file passed with `--project`; the game never
  imports the generator's code. The `generator` key is only used by the game's front ends (skill, editor window) to
  find the CLI; the generator ignores it.

## How Unity receives the assets (The Legacy of Shadows)

```
<game>/character-generator.project.json            generator location, output root, defaults (the player's 48 px layout)
<game>/Assets/GeneratedCharacters/<Name>/          generated characters (output root)
<game>/Assets/Scripts/Editor/LocalCharacterGenerator/
    GeneratedCharacterImporter.cs                  import settings for that folder
    CharacterGeneratorWindow.cs                    Tools > Local Character Generator > Generate Character...
<game>/.claude/skills/generate-character/SKILL.md  Claude Code skill (docs/claude-skill.md)
```

* **Importer** (`AssetPostprocessor.OnPreprocessTexture`): every PNG under `Assets/GeneratedCharacters/` is imported
  on its **first** import (when its `.meta` does not exist yet) as Sprite (single), **PPU 16, Point filter,
  uncompressed, no mipmaps, alpha is transparency, clamp, bottom-centre pivot, full-rect mesh**. Later Inspector
  changes are kept when a character is regenerated. With the 48 px layout the pivot is (24, 0) and the sprite is
  3 units tall, the same as the player.
* **Editor window**: name, description, fast mode, size (48/64/96/128), player-like proportions, seed (0 = random),
  add as variant. It finds the generator through the project file's `generator` entry (warning if it is not
  there) and runs `<python> -s <generator>/character_generator.py --project <game root>
  --json generate ...` in the background, shows the log, then refreshes the AssetDatabase and pings the sprite. The
  Python path is stored in EditorPrefs (`LocalCharacterGenerator.Python`, default ComfyUI's embedded Python).
  `CharacterGeneratorWindow.Generate(name, description, fast, size, seed, variant)` starts a generation from editor
  scripts.
* The window passes the free-text description to the generator's keyword parser, which is simpler than a spec
  written by hand or by the Claude skill (long phrases can end up in one field, e.g. a whole sentence in `hair`). For
  important characters, prefer the skill or a spec file.
* **Text files** (`character.json`, `metadata.json`, `prompt.txt`) import as TextAssets; game code can read the recipe
  if it needs the description, seed or palette.
* The editor imports new files when it regains focus (or `AssetDatabase.Refresh()`).

## Integrating another project

1. Copy or clone `local-character-generator` anywhere (inside the project is fine; keep it out of the engine's
   asset folder).
2. Write a `character-generator.project.json` with your `output_root` and pixel-art defaults (canvas size, body ratio,
   palette, outline).
3. Make your engine import the PNGs without filtering or compression (nearest/point), and use a bottom-centre pivot:
   the feet are on row `size - 1 - feet_margin`, horizontally centred.
4. Call the CLI with `--project` and `--json`, parse the result (`sprite`, `project_path`, `seed`, `warnings`).

## What a consumer can rely on

| File | Use it for |
|---|---|
| `<Name>.png` | the sprite (named after the character, so tools that name output after the file, such as an animation package's `Generated/<sprite name>/` folder, never collide; folders from before 0.4.0 hold `character.png`, renamed by `migrate-names`): transparent, hard alpha, feet on row `size - 1 - feet_margin`, bottom-centre pivot, faces right (side view) |
| `character.json` | the recipe (spec, generation settings, exact prompts): show the description, regenerate, make variants |
| `metadata.json` | provenance (models and their licenses, seed, versions, hashes), the post-processing record |
| `rig/rig.json`, `rig/joints.txt`, `rig/parts.png`, `rig/underlay.png` | animation: joints, per-pixel body parts, pixels hidden behind the arm, hints such as `leg_swing_scale` for robes ([rig-data.md](rig-data.md)) |

The folder is self-describing: a consumer needs no part of this repository to read it (PNG + JSON + text).

## Animation

The intended pipeline keeps a pixel-exact rig renderer on the consumer side and never generates frames with the image
model:

```
generator: <Name>.png + recipe + rig/ (joints, part map, underlay, hints)
   -> animation tool: rig from joints + part map, underlay drawn under the arms, robes with small leg swing
   -> idle / walk / run / attack frames made only of the sprite's own pixels
```

For the Unity game *The Legacy of Shadows* the animation tool is the AI Sprite Animation package. From version 1.7.0 it
reads the whole `rig/` folder: when a generated character has no rig asset yet, *Generate Animation* imports joints,
part map, underlay and `leg_swing_scale` automatically (or explicitly: right-click the sprite > *AI > Import Generated
Rig Data*, batch `-aiRigImport auto -aiRigOnly 1`). The data must match the sprite exactly (size and sha256 in
`rig.json`); after changing the sprite, run `rig` again. Each character's clips go to `Assets/Animations/Generated/<Name>/`. Results and known issues:
[animation-investigation.md](animation-investigation.md#phase-23-integration-ai-sprite-animation-package-170). Any other engine can implement the
same consumer from [rig-data.md](rig-data.md#using-it-consumers).
