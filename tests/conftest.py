# tests/conftest.py
# pytest hook registration for the --live flag.
# pytest requires addoption hooks to live in conftest.py, NOT in test files.

def pytest_addoption(parser):
    """Register --live flag: enables tests that actually call the Groq API."""
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run live tests that call the Groq API (requires GROQ_API_KEY in .env).",
    )
