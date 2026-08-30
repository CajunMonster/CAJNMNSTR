from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigurationError, CredentialsMissingError, ExecutionDisabledError

PAPER_API_URL = "https://paper-api.alpaca.markets"
EXECUTION_CONFIRMATION = "PAPER_ONLY_I_ACCEPT"
TERRA_MODEL = "gpt-5.6-terra"


def _bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigurationError(f"Invalid boolean value: {value!r}")


def _positive_float(value: str | None, *, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError("AI timeout must be a number") from exc
    if parsed <= 0:
        raise ConfigurationError("AI timeout must be greater than zero")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    data_root: Path
    runtime_root: Path
    paper_mode: bool
    alpaca_api_base_url: str
    alpaca_api_key: str | None
    alpaca_secret_key: str | None
    stock_feed: str
    options_feed: str
    data_entitlement: str
    execution_enabled: bool
    execution_confirmation: str | None
    ai_provider: str
    openai_api_key: str | None
    openai_model: str
    ai_timeout_seconds: float

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        load_local_file: bool = True,
    ) -> Settings:
        if load_local_file and environ is None:
            load_dotenv(os.getenv("CAJNMNSTR_ENV_FILE", ".env.local"), override=False)
        env = os.environ if environ is None else environ
        environment = env.get("CAJNMNSTR_ENV", "paper").strip().lower()
        paper_mode = environment == "paper"
        settings = cls(
            environment=environment,
            data_root=Path(env.get("CAJNMNSTR_DATA_ROOT", r"F:\CAJNMNSTR")),
            runtime_root=Path(env.get("CAJNMNSTR_RUNTIME_ROOT", "runtime")),
            paper_mode=paper_mode,
            alpaca_api_base_url=env.get("ALPACA_API_BASE_URL", PAPER_API_URL).rstrip("/"),
            alpaca_api_key=env.get("ALPACA_API_KEY") or None,
            alpaca_secret_key=env.get("ALPACA_SECRET_KEY") or None,
            stock_feed=env.get("ALPACA_STOCK_FEED", "iex").strip().lower(),
            options_feed=env.get("ALPACA_OPTIONS_FEED", "indicative").strip().lower(),
            data_entitlement=env.get("ALPACA_DATA_ENTITLEMENT", "unknown").strip().lower(),
            execution_enabled=_bool(env.get("CAJNMNSTR_EXECUTION_ENABLED"), default=False),
            execution_confirmation=env.get("CAJNMNSTR_EXECUTION_CONFIRMATION") or None,
            ai_provider=env.get("CAJNMNSTR_AI_PROVIDER", "openai").strip().lower(),
            openai_api_key=env.get("OPENAI_API_KEY") or None,
            openai_model=env.get("CAJNMNSTR_TERRA_MODEL", TERRA_MODEL).strip(),
            ai_timeout_seconds=_positive_float(
                env.get("CAJNMNSTR_AI_TIMEOUT_SECONDS"), default=30.0
            ),
        )
        settings.validate_static_safety()
        return settings

    @property
    def journal_path(self) -> Path:
        return self.data_root / "journal" / "cajnmnstr.sqlite3"

    @property
    def emergency_incident_path(self) -> Path:
        return self.runtime_root / "incidents.jsonl"

    @property
    def credentials_present(self) -> bool:
        return bool(self.alpaca_api_key and self.alpaca_secret_key)

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_provider and self.openai_api_key and self.openai_model)

    @property
    def execution_armed(self) -> bool:
        return (
            self.execution_enabled
            and self.execution_confirmation == EXECUTION_CONFIRMATION
            and self.paper_mode
            and self.alpaca_api_base_url == PAPER_API_URL
            and self.credentials_present
        )

    def validate_static_safety(self) -> None:
        if not self.paper_mode:
            raise ConfigurationError("CAJNMNSTR accepts only CAJNMNSTR_ENV=paper")
        if self.alpaca_api_base_url != PAPER_API_URL:
            raise ConfigurationError(
                f"Trading base URL must be the Alpaca paper endpoint: {PAPER_API_URL}"
            )
        if self.stock_feed not in {"iex", "sip", "delayed_sip"}:
            raise ConfigurationError("ALPACA_STOCK_FEED must be iex, sip, or delayed_sip")
        if self.options_feed not in {"indicative", "opra"}:
            raise ConfigurationError("ALPACA_OPTIONS_FEED must be indicative or opra")
        if (
            self.stock_feed == "sip" or self.options_feed == "opra"
        ) and self.data_entitlement != "algo_trader_plus":
            raise ConfigurationError(
                "SIP or OPRA requires verified ALPACA_DATA_ENTITLEMENT=algo_trader_plus"
            )
        if self.ai_provider != "openai":
            raise ConfigurationError("CAJNMNSTR_AI_PROVIDER must be openai for the Terra baseline")
        if self.openai_model != TERRA_MODEL:
            raise ConfigurationError(
                f"CAJNMNSTR_TERRA_MODEL must be the approved baseline model: {TERRA_MODEL}"
            )

    def require_credentials(self) -> None:
        if not self.credentials_present:
            raise CredentialsMissingError(
                "Alpaca credentials are absent; authenticated access remains unavailable"
            )

    def require_execution_armed(self) -> None:
        self.validate_static_safety()
        if not self.execution_armed:
            raise ExecutionDisabledError(
                "Paper execution is disabled; both the enable flag and exact paper "
                "confirmation are required, along with local paper credentials"
            )
        self.require_credentials()

    def redacted(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "paper_mode": self.paper_mode,
            "alpaca_api_base_url": self.alpaca_api_base_url,
            "alpaca_credentials_present": self.credentials_present,
            "stock_feed": self.stock_feed,
            "options_feed": self.options_feed,
            "data_entitlement": self.data_entitlement,
            "data_root": str(self.data_root),
            "journal_path": str(self.journal_path),
            "execution_enabled": self.execution_enabled,
            "execution_armed": self.execution_armed,
            "ai_provider": self.ai_provider,
            "ai_configured": self.ai_configured,
            "openai_model": self.openai_model,
            "ai_timeout_seconds": self.ai_timeout_seconds,
        }
