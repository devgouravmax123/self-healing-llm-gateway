"""Static validation tests for k6 load test scripts and configuration (Phase 17.1)."""

from pathlib import Path

LOAD_TESTS_DIR = Path(__file__).resolve().parent.parent / "load_tests"


class TestLoadScriptsStaticValidation:
    """Validate existence, syntax, and structure of k6 load scripts without running load."""

    def test_load_test_files_exist(self) -> None:
        """Verify required k6 foundation files exist on disk."""
        config_js = LOAD_TESTS_DIR / "config.js"
        baseline_js = LOAD_TESTS_DIR / "baseline.js"
        concurrency_js = LOAD_TESTS_DIR / "concurrency.js"
        chaos_load_js = LOAD_TESTS_DIR / "chaos_load.js"
        rate_limit_js = LOAD_TESTS_DIR / "rate_limit.js"

        assert config_js.is_file(), f"Missing {config_js}"
        assert baseline_js.is_file(), f"Missing {baseline_js}"
        assert concurrency_js.is_file(), f"Missing {concurrency_js}"
        assert chaos_load_js.is_file(), f"Missing {chaos_load_js}"
        assert rate_limit_js.is_file(), f"Missing {rate_limit_js}"

    def test_config_js_structure_and_env_variables(self) -> None:
        """Verify config.js defines expected environment variables and helper functions."""
        config_js = LOAD_TESTS_DIR / "config.js"
        content = config_js.read_text(encoding="utf-8")

        expected_env_vars = ["BASE_URL", "API_KEY", "MODEL", "VUS", "DURATION"]
        for env_var in expected_env_vars:
            assert env_var in content, f"Expected env variable '{env_var}' in config.js"

        assert "export function getHeaders" in content
        assert "export function buildChatPayload" in content
        assert "stream: false" in content

    def test_baseline_js_structure_and_endpoint(self) -> None:
        """Verify baseline.js references /v1/chat/completions and defines options/checks."""
        baseline_js = LOAD_TESTS_DIR / "baseline.js"
        content = baseline_js.read_text(encoding="utf-8")

        assert "/v1/chat/completions" in content
        assert "export const options" in content
        assert "export default function" in content
        assert "http.post" in content
        assert "check(" in content

    def test_concurrency_js_structure_and_endpoint(self) -> None:
        """Verify concurrency.js references /v1/chat/completions, stages, and options."""
        concurrency_js = LOAD_TESTS_DIR / "concurrency.js"
        content = concurrency_js.read_text(encoding="utf-8")

        assert "/v1/chat/completions" in content
        assert "export const options" in content
        assert "stages:" in content
        assert "export default function" in content
        assert "http.post" in content
        assert "check(" in content
        assert "CONCURRENT_VUS" in content
        assert "buildChatPayload" in content
        assert "getHeaders" in content

    def test_chaos_load_js_structure_and_endpoint(self) -> None:
        """Verify chaos_load.js references /v1/chat/completions, stages, and options."""
        chaos_load_js = LOAD_TESTS_DIR / "chaos_load.js"
        content = chaos_load_js.read_text(encoding="utf-8")

        assert "/v1/chat/completions" in content
        assert "export const options" in content
        assert "stages:" in content
        assert "export default function" in content
        assert "http.post" in content
        assert "check(" in content
        assert "CHAOS_VUS" in content
        assert "buildChatPayload" in content
        assert "getHeaders" in content
        assert "ADMIN_API_KEY" not in content
        assert "/admin/chaos" not in content

    def test_rate_limit_js_structure_and_endpoint(self) -> None:
        """Verify rate_limit.js references /v1/chat/completions, stages, options, and 429 checks."""
        rate_limit_js = LOAD_TESTS_DIR / "rate_limit.js"
        content = rate_limit_js.read_text(encoding="utf-8")

        assert "/v1/chat/completions" in content
        assert "export const options" in content
        assert "stages:" in content
        assert "export default function" in content
        assert "http.post" in content
        assert "check(" in content
        assert "RATE_LIMIT_VUS" in content
        assert "buildChatPayload" in content
        assert "getHeaders" in content
        assert "Retry-After" in content
        assert "X-RateLimit-Limit" in content
        assert "X-RateLimit-Remaining" in content
        assert "X-RateLimit-Reset" in content

    def test_no_hardcoded_secrets_in_scripts(self) -> None:
        """Ensure no hardcoded API keys or secret credentials are in load test scripts."""
        for script_path in LOAD_TESTS_DIR.glob("*.js"):
            text = script_path.read_text(encoding="utf-8")
            forbidden_tokens = [
                "gw_live_",
                "gw_test_",
                "sk-",
                "secret123",
                "password",
                "DEFAULT_API_KEY",
                "X-Tenant-ID",
            ]
            for token in forbidden_tokens:
                assert token not in text, f"Forbidden token '{token}' in {script_path.name}"
