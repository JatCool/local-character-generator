# Contributing

Contributions are welcome: bug reports, new profiles and workflows, better post-processing, documentation, tests.

## License of contributions

The project is MIT-licensed ([`LICENSE`](LICENSE)). By submitting a contribution you agree that it is licensed under
the same MIT License ("inbound = outbound"). Do not submit code or assets you do not have the right to license this
way. **Never commit model files** (`*.safetensors`, `*.ckpt`, `*.gguf`): they have their own licenses and sizes; add
them to `config/models.json` instead ([docs/extending.md](docs/extending.md)).

## Development setup

1. Follow [docs/setup.md](docs/setup.md) (ComfyUI + the `sdxl` model group; the `rig` group for the rig step).
2. Run the offline tests (no GPU, a few seconds): `python -m unittest discover -s tests`
   (with ComfyUI's Python: `C:\AI\ComfyUI\python_embeded\python.exe -s -m unittest discover -s tests`).
3. Try your change end to end: `./character-generator generate --spec examples/specs/Rogue.json --seed 1840959430`
   and, for rig changes, `./character-generator rig --name Rogue`.

## Guidelines

* Keep the project self-contained: Python standard library + Pillow + numpy only; ComfyUI is reached over its HTTP API.
  No dependency on a game engine or on a specific consuming project.
* Model- and workflow-specific choices belong in data (`config/`, `workflows/`, `prompts/`), not in code.
* Everything must stay reproducible: anything that affects pixels is either stored in the recipe/metadata or versioned
  (workflow hashes, `generator_version`). Do not change the output of existing recipes silently; new behaviour goes
  behind new keys with backward-compatible defaults (see how `char_height`, `guide` and `prompt` were added).
* Pixel art stays pixel art: nearest/majority sampling only, hard alpha, palette colours only.
* Add or update tests for new behaviour (`tests/test_chargen.py`), and update the docs (README and `docs/`) when
  behaviour, CLI options, configuration or file formats change. Note user-visible changes in [CHANGELOG.md](CHANGELOG.md).
* Quality changes (new default model, new thresholds) need evidence: run `benchmark` or the acceptance sheet
  (`scripts/acceptance.py`) and summarise the result in `docs/model-benchmark.md`.
* Commit messages: short imperative subject, explanation in the body; one logical change per commit.

## Reporting problems

Include the command, the JSON result (`--json`), `character-generator doctor` output, and the run folder
(`runs/<timestamp>_<name>_.../`: `workflow_api.json`, `raw.png`, `preview.png`) and `runs/comfyui.log` if relevant.
