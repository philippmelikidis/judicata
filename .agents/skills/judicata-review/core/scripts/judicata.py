#!/usr/bin/env python3
"""judicata core: entry point for skills, agents and hooks.

Usage: python3 scripts/judicata.py <group> <command> --project <dir> [options]
All output is JSON on stdout. See CLAUDE.md, section "Architektur".
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _run() -> int:
    if sys.argv[1:2] == ["hook"]:
        # a hook never disturbs the session, not even on a too old Python
        try:
            from judicata_core.cli import main

            return main(sys.argv[1:])
        except BaseException:  # noqa: BLE001
            return 0
    from judicata_core.cli import main

    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(_run())
