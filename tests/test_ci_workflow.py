"""Static validation tests for GitHub Actions CI/CD workflow configuration (Phase 19)."""

from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github" / "workflows" / "ci.yml"


def _load_workflow_data() -> dict[str, Any]:
    """Load workflow YAML using PyYAML with string-key fallback for YAML 1.1 bool keys ('on')."""
    assert WORKFLOW_PATH.is_file(), f"Workflow file does not exist at {WORKFLOW_PATH}"
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict), "Workflow YAML must parse to a dictionary"
    return data


def _get_workflow_triggers(data: dict[str, Any]) -> dict[str, Any]:
    """Extract triggers accounting for PyYAML bool key conversion of 'on' -> True."""
    raw_dict: dict[Any, Any] = data
    if "on" in raw_dict:
        triggers = raw_dict["on"]
    elif True in raw_dict:
        triggers = raw_dict[True]
    else:
        raise AssertionError("No 'on' trigger found in workflow YAML")

    assert isinstance(triggers, dict), "Triggers must be a dictionary"
    return triggers


class TestCIWorkflow:
    """Static validation of CI workflow structure, quality gates, and security constraints."""

    def test_workflow_file_exists(self) -> None:
        """Verify .github/workflows/ci.yml exists on disk."""
        assert WORKFLOW_PATH.is_file(), f"Missing workflow at {WORKFLOW_PATH}"

    def test_triggers_configured_for_main_branch(self) -> None:
        """Verify workflow triggers on push to main and pull_request against main."""
        data = _load_workflow_data()
        triggers = _get_workflow_triggers(data)

        assert "push" in triggers, "Missing 'push' trigger in CI workflow"
        assert "pull_request" in triggers, "Missing 'pull_request' trigger in CI workflow"

        push_branches = triggers["push"].get("branches", [])
        pr_branches = triggers["pull_request"].get("branches", [])

        assert "main" in push_branches, "'push' must target 'main' branch"
        assert "main" in pr_branches, "'pull_request' must target 'main' branch"

    def test_job_and_runner_environment(self) -> None:
        """Verify workflow runs on clean ubuntu-latest runner."""
        data = _load_workflow_data()
        assert "jobs" in data
        jobs = data["jobs"]
        assert len(jobs) >= 1

        # Check main quality job
        job_key = next(iter(jobs))
        job = jobs[job_key]
        assert job.get("runs-on") == "ubuntu-latest"

    def test_python_and_uv_setup_action(self) -> None:
        """Verify Astral setup-uv action is used with Python 3.12 and caching enabled."""
        data = _load_workflow_data()
        jobs = data["jobs"]
        job = next(iter(jobs.values()))
        steps = job.get("steps", [])

        uv_step = next(
            (s for s in steps if isinstance(s.get("uses"), str) and "setup-uv" in s["uses"]),
            None,
        )
        assert uv_step is not None, "Missing setup-uv step in CI workflow"

        with_block = uv_step.get("with", {})
        assert with_block.get("python-version") == "3.12"
        assert with_block.get("enable-cache") is True

    def test_frozen_dependency_installation(self) -> None:
        """Verify dependencies are installed reproducibly with --frozen and --dev."""
        data = _load_workflow_data()
        jobs = data["jobs"]
        job = next(iter(jobs.values()))
        steps = job.get("steps", [])

        install_step = next(
            (s for s in steps if "uv sync" in s.get("run", "")),
            None,
        )
        assert install_step is not None, "Missing dependency installation step"
        run_cmd = install_step["run"]
        assert "--frozen" in run_cmd, "uv sync must specify --frozen"
        assert "--dev" in run_cmd, "uv sync must specify --dev"

    def test_all_quality_gates_present_and_explicit(self) -> None:
        """Verify separate quality gate steps for ruff lint, ruff format, mypy, and pytest."""
        data = _load_workflow_data()
        jobs = data["jobs"]
        job = next(iter(jobs.values()))
        steps = job.get("steps", [])
        run_commands = [s.get("run", "") for s in steps if "run" in s]

        # 1. Ruff lint
        assert any("uv run ruff check" in cmd for cmd in run_commands), "Missing Ruff lint gate"
        # 2. Ruff format check
        assert any("uv run ruff format --check" in cmd for cmd in run_commands), (
            "Missing Ruff format check gate"
        )
        # 3. Mypy type check
        assert any("uv run mypy app tests" in cmd for cmd in run_commands), "Missing mypy gate"
        # 4. Pytest test suite
        assert any("uv run pytest" in cmd for cmd in run_commands), "Missing pytest gate"

    def test_docker_build_validation_step(self) -> None:
        """Verify Docker build step builds without pushing or publishing."""
        data = _load_workflow_data()
        jobs = data["jobs"]
        job = next(iter(jobs.values()))
        steps = job.get("steps", [])
        run_commands = [s.get("run", "") for s in steps if "run" in s]

        docker_build_cmd = next((cmd for cmd in run_commands if "docker build" in cmd), None)
        assert docker_build_cmd is not None, "Missing Docker build validation step"
        assert "docker push" not in docker_build_cmd, "Docker step must not push"
        assert "llm-gateway:ci" in docker_build_cmd or "llm-gateway" in docker_build_cmd

    def test_zero_secrets_or_hardcoded_credentials(self) -> None:
        """Verify workflow contains no repository secrets or hardcoded credentials."""
        content = WORKFLOW_PATH.read_text(encoding="utf-8")

        forbidden_strings = [
            "secrets.ADMIN_API_KEY",
            "secrets.DATABASE_URL",
            "secrets.OPENAI_API_KEY",
            "secrets.ANTHROPIC_API_KEY",
            "gw_live_",
            "password",
            "api_key",
        ]
        for forbidden in forbidden_strings:
            assert forbidden not in content.lower(), (
                f"Potentially sensitive credential or secret reference found: {forbidden}"
            )
