#!/usr/bin/env python3
"""Generate the pydantic models in contracts/python from contracts/schemas.

    python tools/generate_contracts.py          # regenerate
    python tools/generate_contracts.py --check  # fail if the committed models are stale (CI)

Pinned to the datamodel-code-generator version in contracts/python's `codegen` extra,
with the builtin formatter, so output does not drift with black/isort versions.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "contracts" / "schemas"
MODELS = ROOT / "contracts" / "python" / "src" / "observatory_contracts" / "models"

ARGS = [
    "--input-file-type", "jsonschema",
    "--output-model-type", "pydantic_v2.BaseModel",
    "--target-python-version", "3.10",
    "--use-annotated",
    "--field-constraints",
    "--use-double-quotes",
    "--disable-timestamp",
    "--use-standard-collections",
    "--use-union-operator",
    "--use-title-as-name",
    "--enum-field-as-literal", "all",
    "--collapse-root-models",
    "--formatters", "builtin",
]


def generate(out: Path) -> None:
    subprocess.run(
        [sys.executable, "-m", "datamodel_code_generator", "--input", str(SCHEMAS), "--output", str(out), *ARGS],
        check=True,
    )


def differences(a: Path, b: Path) -> list[str]:
    cmp = filecmp.dircmp(a, b)
    diffs = [f"{a.name}/{n}" for n in cmp.left_only + cmp.right_only + cmp.diff_files if n != "__pycache__"]
    for sub in cmp.common_dirs:
        if sub != "__pycache__":
            diffs += differences(a / sub, b / sub)
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "models"
        generate(out)
        if args.check:
            diffs = differences(out, MODELS) if MODELS.exists() else ["models/ (missing)"]
            if diffs:
                print("Generated models are stale; run tools/generate_contracts.py:", *diffs, sep="\n  ", file=sys.stderr)
                return 1
            print("Generated models are up to date.")
            return 0
        if MODELS.exists():
            shutil.rmtree(MODELS)
        shutil.copytree(out, MODELS)
        print(f"Wrote {MODELS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
