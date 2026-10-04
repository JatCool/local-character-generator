"""Entry point: python character_generator.py <command> ... (works with ComfyUI's embedded Python)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chargen.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
