"""Packaging and repository hygiene contracts.

These guard the metadata that ComfyUI-Manager, the Comfy Registry and
``pip install .`` consume, and the ignore rules that keep local secrets
(``config.json`` holds the CivitAI API key) out of the repository.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# PEP 508 requirement: name, optional extras, optional version spec.
_VERSION_CLAUSE = r"[<>=!~]=?\s*[A-Za-z0-9.*+!-]+\s*"
_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"  # distribution name
    r"(\[[A-Za-z0-9,._-]+\])?\s*"  # optional extras
    r"(" + _VERSION_CLAUSE + r"(,\s*" + _VERSION_CLAUSE + r")*)?$"  # version spec
)


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def _pyproject():
    try:
        import tomllib  # Python 3.11+
    except ImportError:  # pragma: no cover - exercised on 3.10 only
        tomllib = None
    text = _read("pyproject.toml")
    if tomllib is not None:
        return tomllib.loads(text)
    # Minimal fallback for 3.10: pull the dependency list out with a regex.
    match = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
    deps = re.findall(r'"([^"]*)"', match.group(1)) if match else []
    return {
        "project": {
            "dependencies": deps,
            "requires-python": (
                re.search(r'requires-python\s*=\s*"([^"]+)"', text).group(1)
                if "requires-python" in text
                else None
            ),
        },
        "build-system": {} if "[build-system]" in text else None,
    }


class PyprojectContract(unittest.TestCase):
    def test_dependencies_are_valid_requirements(self):
        deps = _pyproject()["project"]["dependencies"]
        self.assertTrue(deps, "dependencies must not be empty")
        for dep in deps:
            self.assertRegex(dep, _REQUIREMENT, f"not a PEP 508 requirement: {dep!r}")

    def test_dependencies_match_requirements_txt(self):
        deps = set(_pyproject()["project"]["dependencies"])
        wanted = {
            line.split("#", 1)[0].strip()
            for line in _read("requirements.txt").splitlines()
            if line.split("#", 1)[0].strip()
        }
        self.assertEqual(deps, wanted)

    def test_requires_python_floor_is_3_10(self):
        self.assertEqual(_pyproject()["project"]["requires-python"], ">=3.10")

    def test_build_system_is_declared(self):
        self.assertIsNotNone(_pyproject().get("build-system"))


class IgnoreRulesContract(unittest.TestCase):
    def test_local_secrets_and_artifacts_are_ignored(self):
        patterns = {
            line.strip()
            for line in _read(".gitignore").splitlines()
            if line.strip() and not line.startswith("#")
        }
        for required in (
            "config.json",
            "resume.sh",
            "*.backup_*",
            "promptmanager_logs/",
        ):
            self.assertIn(required, patterns)


class LintConfigContract(unittest.TestCase):
    def test_flake8_reads_its_own_config_file(self):
        # flake8 ignores pyproject.toml, so the settings must live in .flake8.
        text = _read(".flake8")
        self.assertIn("max-line-length = 88", text)
        self.assertIn("E203", text)


if __name__ == "__main__":
    unittest.main()
