from pathlib import Path

from cajnmnstr.cli import _mcp_config_check


def test_codex_mcp_example_is_secret_free_and_read_only() -> None:
    assert _mcp_config_check(Path("config/codex-mcp.example.toml")) == 0


def test_generic_mcp_example_is_read_only() -> None:
    assert _mcp_config_check(Path("config/alpaca-mcp.example.json")) == 0


def test_local_mcp_launcher_is_paper_only_and_excludes_write_toolsets() -> None:
    launcher = Path("launcher/Start-Alpaca-Mcp-Readonly.ps1").read_text(encoding="utf-8")

    assert "alpaca-mcp-server==2.3.1" in launcher
    assert "fastmcp==3.1.0" in launcher
    assert "ALPACA_PAPER_TRADE = 'true'" in launcher
    assert "assets,stock-data,options-data,news" in launcher
    assert "ALPACA_TOOLSETS" in launcher
    assert "account,trading" not in launcher
    assert "ALPACA_API_KEY" in launcher
    assert "ALPACA_SECRET_KEY" in launcher
    assert ".env.local" in launcher


def test_mcp_proof_rejects_broker_write_tools() -> None:
    proof = Path("scripts/verify_alpaca_mcp_readonly.py").read_text(encoding="utf-8")

    for forbidden_prefix in (
        '"cancel_"',
        '"close_"',
        '"exercise_"',
        '"place_"',
        '"replace_"',
        '"update_"',
    ):
        assert forbidden_prefix in proof
    assert 'PROOF_TOOL = "get_clock"' in proof
