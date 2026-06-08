from __future__ import annotations

import shlex


def q(value: str) -> str:
    return shlex.quote(str(value))


def bash_script(script: str) -> str:
    # Run a multi-line script through bash -lc with safe quoting.
    return "bash -lc " + q("set -euo pipefail\n" + script)


def mask_secrets(text: str, secrets: list[str]) -> str:
    out = text or ""
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out
