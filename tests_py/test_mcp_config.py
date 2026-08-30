from pathlib import Path

from cajnmnstr.cli import _mcp_config_check


def test_codex_mcp_example_is_secret_free_and_read_only() -> None:
    assert _mcp_config_check(Path("config/codex-mcp.example.toml")) == 0


def test_generic_mcp_example_is_read_only() -> None:
    assert _mcp_config_check(Path("config/alpaca-mcp.example.json")) == 0
