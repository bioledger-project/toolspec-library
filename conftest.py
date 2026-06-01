"""Path-addressable pytest collector for tool specs.

Lives at the repo root so its ``pytest_collect_file`` hook fires while pytest
walks ``specs/``. Emits pytest items per command directory. Allows:
- pytest                     # all commands
- pytest specs/samtools/     # whole family
- pytest specs/samtools/sort/ # one command
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Repo root (this file lives at the repo root).
ROOT = Path(__file__).parent
SPECS_DIR = ROOT / "specs"


def pytest_collect_file(parent, file_path: Path):
    """Collect spec.yaml and tests.yaml files under specs/ (pytest >= 8 API)."""
    try:
        rel_path = file_path.relative_to(ROOT)
    except ValueError:
        return
    if rel_path.parts[:1] != ("specs",):
        return
    if file_path.name in ("spec.yaml", "tests.yaml"):
        return SpecFile.from_parent(parent, path=file_path)


class SpecFile(pytest.File):
    """A spec.yaml or tests.yaml file."""

    def collect(self):
        spec_path = self.path
        tests_path = spec_path.parent / "tests.yaml"
        is_spec = spec_path.name == "spec.yaml"

        if is_spec:
            # Layer A: schema/lint checks
            yield SpecValidationItem.from_parent(
                self,
                name=f"{spec_path.parent.name}::load_and_validate",
                spec_path=spec_path,
                tests_path=tests_path,
            )
        else:
            # Layer B: behavioral test cases
            import yaml

            if not tests_path.exists():
                return

            try:
                data = yaml.safe_load(tests_path.read_text()) or {}
                cases = data.get("cases", [])
            except Exception:
                return  # skip if tests.yaml is malformed

            for case in cases:
                case_name = case.get("name", "unnamed")
                yield BehavioralTestCase.from_parent(
                    self,
                    name=f"{spec_path.parent.name}::{case_name}",
                    spec_path=spec_path.parent / "spec.yaml",
                    case=case,
                )


class SpecValidationItem(pytest.Item):
    """Layer A: schema/lint validation."""

    def __init__(self, parent, name, spec_path, tests_path):
        super().__init__(name, parent)
        self.spec_path = Path(spec_path)
        self.tests_path = Path(tests_path)

    def runtest(self):
        from bioledger_toolspec_schema import Severity, load_spec, validate_spec

        # 1. load_spec succeeds
        spec = load_spec(self.spec_path)

        # 2. validate_spec reports zero ERRORs
        result = validate_spec(spec)
        errors = [i for i in result.issues if i.severity == Severity.ERROR]
        if errors:
            raise AssertionError(
                "Validation errors:\n"
                + "\n".join("  {}: {}".format(i.field, i.message) for i in errors)
            )

        # 3. directory name matches spec.execution.name
        if self.spec_path.parent.name != spec.execution.name:
            raise AssertionError(
                "Directory name '{}' does not match spec.execution.name '{}'".format(
                    self.spec_path.parent.name, spec.execution.name
                )
            )

        # 4. (Soft) strict validation - report warnings but don't fail
        strict_result = validate_spec(spec, strict=True)
        warns = [i for i in strict_result.issues if i.severity == Severity.WARNING]
        if warns:
            print("\n  Warnings (strict mode):")
            for w in warns:
                print("    {}: {}".format(w.field, w.message))

    def reportinfo(self):
        return self.path, 0, f"spec: {self.name}"


class BehavioralTestCase(pytest.Item):
    """Layer B: behavioral test case from tests.yaml."""

    def __init__(self, parent, name, spec_path, case):
        super().__init__(name, parent)
        self.spec_path = Path(spec_path)
        self.case = case

    def runtest(self):
        from bioledger_toolspec_schema import load_spec
        from jinja2 import Template

        if not self.spec_path.exists():
            pytest.skip("spec.yaml not found")

        spec = load_spec(self.spec_path)

        inputs = self.case.get("inputs", {})
        parameters = self.case.get("parameters", {})
        expects = self.case.get("expects", {})

        # Render check (always runs). NOTE: inputs render to dummy paths, so
        # command_contains should assert literals + parameter values, not input
        # file paths (Phase 1 limitation).
        context = {
            "inputs": {k: f"/input/{k}/dummy" for k in inputs},
            "parameters": parameters,
            "outputs": {"_dir": "/output"},
        }

        template = Template(spec.execution.command)
        rendered = template.render(context)

        for substr in expects.get("command_contains", []):
            if substr not in rendered:
                raise AssertionError(f"Expected '{substr}' in rendered command: {rendered}")

        # Container run (opt-in via run: true) — deferred to Phase 2.
        if self.case.get("run", False):
            import shutil

            if not shutil.which("docker") and not shutil.which("podman"):
                pytest.skip("Docker/Podman not available")

            for key, rel_path in inputs.items():
                fixture_path = self.spec_path.parent / rel_path
                if not fixture_path.exists():
                    pytest.skip(f"Fixture not found: {rel_path}")

            pytest.skip("Container execution not yet implemented")

    def reportinfo(self):
        return self.path, 0, f"case: {self.name}"
