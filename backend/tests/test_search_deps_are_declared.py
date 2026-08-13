"""Everything `semantic_index` imports at module level is a base requirement.

`numpy` was imported at the top of `services/semantic_index.py` and declared only
in `requirements-voice.txt`, which is deliberately NOT installed by default
(it pulls onnxruntime, ctranslate2 and a phonemizer, several hundred megabytes).
So a backend brought up without the voice extras raised ImportError on the FIRST
search call — and the search tool is now handed to every roled agent and to the
unattended worker.

Pinned as a test rather than fixed and forgotten because the failure is invisible
here: this box HAS the voice extras installed, so nothing local ever reproduces
it.
"""
import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
_STDLIB = set(sys.stdlib_module_names)


def _base_requirements() -> set[str]:
    names = set()
    for line in (BACKEND / "requirements.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split("==")[0].split(">=")[0].split("[")[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _module_level_imports(path: Path) -> set[str]:
    """Top-level imports only. A lazily imported package inside a function is a
    supported-absent state (that is how voice_engine handles its own stack); a
    module-level one is a hard requirement of importing the module at all."""
    tree = ast.parse(path.read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def test_numpy_is_a_base_requirement():
    assert "numpy" in _base_requirements()


def test_the_pin_matches_what_is_installed():
    import numpy
    pin = [l for l in (BACKEND / "requirements.txt").read_text().splitlines()
           if l.strip().startswith("numpy")]
    assert pin, "numpy is not pinned in requirements.txt"
    assert pin[0].strip() == f"numpy=={numpy.__version__}"


def test_semantic_index_imports_nothing_undeclared():
    declared = _base_requirements()
    local = {p.stem for p in (BACKEND / "services").glob("*.py")} | {"services"}
    missing = sorted(
        m for m in _module_level_imports(BACKEND / "services" / "semantic_index.py")
        if m not in _STDLIB and m not in local and m.lower() not in declared
    )
    assert not missing, (
        f"{missing} are imported at module level but are not in requirements.txt, "
        f"so a default install raises on the first search call"
    )
