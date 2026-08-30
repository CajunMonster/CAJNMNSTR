from pathlib import Path

import pytest

from cajnmnstr.config import EXECUTION_CONFIRMATION, PAPER_API_URL, TERRA_MODEL, Settings
from cajnmnstr.errors import BrokerLockedError, ConfigurationError, ExecutionDisabledError


def env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    values = {
        "CAJNMNSTR_ENV": "paper",
        "CAJNMNSTR_DATA_ROOT": str(tmp_path),
        "ALPACA_API_BASE_URL": PAPER_API_URL,
        "CAJNMNSTR_ENTRY_ENABLED": "false",
        "CAJNMNSTR_POSITION_MANAGEMENT_ENABLED": "true",
        "CAJNMNSTR_BROKER_LOCK": "false",
    }
    values.update(overrides)
    return values


def test_default_entry_is_closed_and_position_management_is_not_armed_without_gate(
    tmp_path: Path,
) -> None:
    settings = Settings.from_env(env(tmp_path), load_local_file=False)
    assert settings.paper_mode
    assert not settings.credentials_present
    assert not settings.entry_enabled
    assert not settings.entry_armed
    assert settings.position_management_enabled
    assert not settings.position_management_armed
    assert not settings.broker_lock
    with pytest.raises(ExecutionDisabledError):
        settings.require_entry_armed()
    with pytest.raises(ExecutionDisabledError):
        settings.require_position_management_armed()


def test_entry_and_position_management_authority_are_independent(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env(
            tmp_path,
            ALPACA_API_KEY="paper-key",
            ALPACA_SECRET_KEY="paper-secret",
            CAJNMNSTR_EXECUTION_CONFIRMATION=EXECUTION_CONFIRMATION,
        ),
        load_local_file=False,
    )
    assert not settings.entry_armed
    assert settings.position_management_armed
    with pytest.raises(ExecutionDisabledError):
        settings.require_entry_armed()
    settings.require_position_management_armed()


def test_entry_can_be_armed_without_changing_position_management_semantics(
    tmp_path: Path,
) -> None:
    settings = Settings.from_env(
        env(
            tmp_path,
            ALPACA_API_KEY="paper-key",
            ALPACA_SECRET_KEY="paper-secret",
            CAJNMNSTR_ENTRY_ENABLED="true",
            CAJNMNSTR_EXECUTION_CONFIRMATION=EXECUTION_CONFIRMATION,
        ),
        load_local_file=False,
    )
    assert settings.entry_armed
    assert settings.position_management_armed
    settings.require_entry_armed()
    settings.require_position_management_armed()


def test_broker_lock_blocks_both_authorities(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env(
            tmp_path,
            ALPACA_API_KEY="paper-key",
            ALPACA_SECRET_KEY="paper-secret",
            CAJNMNSTR_ENTRY_ENABLED="true",
            CAJNMNSTR_BROKER_LOCK="true",
            CAJNMNSTR_EXECUTION_CONFIRMATION=EXECUTION_CONFIRMATION,
        ),
        load_local_file=False,
    )
    assert not settings.entry_armed
    assert not settings.position_management_armed
    with pytest.raises(BrokerLockedError):
        settings.require_entry_armed()
    with pytest.raises(BrokerLockedError):
        settings.require_position_management_armed()


def test_legacy_execution_flag_is_an_entry_only_migration_alias(tmp_path: Path) -> None:
    values = env(tmp_path)
    values.pop("CAJNMNSTR_ENTRY_ENABLED")
    values["CAJNMNSTR_EXECUTION_ENABLED"] = "false"
    settings = Settings.from_env(values, load_local_file=False)
    assert not settings.entry_enabled
    assert settings.position_management_enabled
    assert settings.legacy_execution_alias_present


def test_conflicting_legacy_and_explicit_entry_flags_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="conflicts"):
        Settings.from_env(
            env(tmp_path, CAJNMNSTR_EXECUTION_ENABLED="true"),
            load_local_file=False,
        )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CAJNMNSTR_ENV", "live"),
        ("ALPACA_API_BASE_URL", "https://api.alpaca.markets"),
        ("ALPACA_OPTIONS_FEED", "mystery"),
        ("CAJNMNSTR_TERRA_MODEL", "gpt-5.6-sol"),
    ],
)
def test_unsafe_configuration_is_rejected(tmp_path: Path, key: str, value: str) -> None:
    with pytest.raises(ConfigurationError):
        Settings.from_env(env(tmp_path, **{key: value}), load_local_file=False)


def test_redacted_config_never_returns_secret_values(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env(tmp_path, ALPACA_API_KEY="visible-key", ALPACA_SECRET_KEY="visible-secret"),
        load_local_file=False,
    )
    output = str(settings.redacted())
    assert "visible-key" not in output
    assert "visible-secret" not in output


def test_legacy_generic_model_variable_is_ignored_and_never_reported(tmp_path: Path) -> None:
    legacy_value = "credential-like-legacy-value"
    settings = Settings.from_env(
        env(
            tmp_path,
            OPENAI_API_KEY="fixture-openai-key",
            OPENAI_MODEL=legacy_value,
        ),
        load_local_file=False,
    )
    assert settings.openai_model == TERRA_MODEL
    assert legacy_value not in str(settings.redacted())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ALPACA_STOCK_FEED", "sip"),
        ("ALPACA_OPTIONS_FEED", "opra"),
    ],
)
def test_paid_feeds_require_verified_plus_entitlement(
    tmp_path: Path, key: str, value: str
) -> None:
    with pytest.raises(ConfigurationError, match="requires verified"):
        Settings.from_env(env(tmp_path, **{key: value}), load_local_file=False)


def test_verified_plus_entitlement_accepts_sip_and_opra(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env(
            tmp_path,
            ALPACA_STOCK_FEED="sip",
            ALPACA_OPTIONS_FEED="opra",
            ALPACA_DATA_ENTITLEMENT="algo_trader_plus",
        ),
        load_local_file=False,
    )
    assert settings.stock_feed == "sip"
    assert settings.options_feed == "opra"
