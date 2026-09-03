# Alpaca MCP Server verification

CAJNMNSTR satisfies the hackathon's Alpaca integration path with two deliberately separate
components:

- Alpaca Trading API through `alpaca-py` is the deterministic PAPER runtime for account and market
  reads, broker lifecycle handling, positions, and reconciliation.
- The official Alpaca MCP Server v2.3.1 is a locally registered, read-oriented AI/developer
  capability. It is not an execution interface.

## Local registration

- Codex registration: `alpaca_market_readonly`
- Transport: local STDIO
- Server: `alpaca-mcp-server==2.3.1`
- Compatibility dependency: `fastmcp==3.1.0`; Alpaca v2.3.1 explicitly requires FastMCP 3.x
- Mode: PAPER (`ALPACA_PAPER_TRADE=true`)
- Enabled toolsets: `assets`, `stock-data`, `options-data`, `news`
- Excluded toolsets: `account`, `trading`, `watchlists`, and all others

The Codex registration contains no credential values or credential environment settings. It
starts `launcher/Start-Alpaca-Mcp-Readonly.ps1`, which reads only `ALPACA_API_KEY` and
`ALPACA_SECRET_KEY` from the ignored local `.env.local` and passes them to the child process.

## Authenticated read-only proof

On 2026-09-03, `scripts/verify_alpaca_mcp_readonly.py`:

1. initialized the official v2.3.1 server through the same local launcher;
2. discovered 33 tools from the four permitted toolsets;
3. verified that account, order, position, and broker-write capabilities were absent; and
4. successfully invoked the authenticated read-only `get_clock` tool in PAPER mode.

The proof recorded no credentials or account identifiers and performed no broker mutation. It is
repeatable locally with:

```powershell
uv run python scripts/verify_alpaca_mcp_readonly.py
```

The official project documentation is available from the
[Alpaca MCP Server repository](https://github.com/alpacahq/alpaca-mcp-server). Codex local STDIO
server registration follows the
[official OpenAI Codex MCP configuration](https://developers.openai.com/codex/extend/mcp).
