"""Foundation smoke and environment tests for Phase 01."""

import sys

import app


def test_python_version() -> None:
    """Verify that Python runtime is 3.12."""
    assert sys.version_info.major == 3
    assert sys.version_info.minor == 12


def test_package_import() -> None:
    """Verify that the gateway application package imports properly."""
    assert hasattr(app, "__version__")
    assert app.__version__ == "0.1.0"
