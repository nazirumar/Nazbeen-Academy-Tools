"""Unit tests: Phase 9 global log redaction.

Every log record's ``extra`` payload passes through ``redact_mapping``
before reaching a handler — credential-shaped keys and values must never
reach the log stream in clear text, no matter which module logged them.
"""

from __future__ import annotations

import logging

from app.core.logging import (
    ConsoleFormatter,
    JsonFormatter,
    redact_mapping,
    redact_text,
)


def _format_extra(extra: dict[str, object], formatter: logging.Formatter) -> str:
    logger = logging.getLogger("redaction-test")
    logger.setLevel(logging.INFO)
    record = logger.makeRecord(
        name="redaction-test",
        level=logging.INFO,
        fn="(unknown)",
        lno=1,
        msg="event",
        args=(),
        exc_info=None,
        extra=extra,  # type: ignore[arg-type]
    )
    return formatter.format(record)


class TestRedactMapping:
    def test_sensitive_key_fully_redacted(self) -> None:
        out = redact_mapping({"api_key": "sk-abcdef1234567890"})
        assert out["api_key"] == "***"

    def test_every_sensitive_marker_is_redacted(self) -> None:
        for key in (
            "password",
            "mt5_password",
            "secret",
            "token",
            "api_key",
            "apikey",
            "authorization",
            "credential",
            "cookie",
            "database_url",
            "connection_string",
        ):
            out = redact_mapping({key: "whatever-value"})
            assert out[key] == "***", key

    def test_safe_keys_pass_through(self) -> None:
        out = redact_mapping({"symbol": "EURUSD", "count": 3, "ok": True})
        assert out == {"symbol": "EURUSD", "count": 3, "ok": True}

    def test_nested_dict_redacted_by_key(self) -> None:
        out = redact_mapping(
            {"config": {"nested_password": "leak", "host": "db.local"}}
        )
        assert out["config"]["nested_password"] == "***"
        assert out["config"]["host"] == "db.local"

    def test_url_credentials_redacted_in_strings(self) -> None:
        out = redact_mapping(
            {"detail": "connect to postgresql://ta_user:hunter2@db:5432/ta"}
        )
        assert "hunter2" not in out["detail"]
        assert "postgresql://" in out["detail"]

    def test_bearer_value_redacted_in_strings(self) -> None:
        out = redact_mapping({"detail": "sent Authorization: Bearer abcdef123456"})
        assert "abcdef123456" not in out["detail"]

    def test_sk_prefixed_key_redacted_in_strings(self) -> None:
        out = redact_mapping({"detail": "key sk-abcdefghijklmnop"})
        assert "sk-abcdefghijklmnop" not in out["detail"]

    def test_ordinary_prose_untouched(self) -> None:
        text = "Backtest EURUSD M1 completed with 42 trades."
        out = redact_mapping({"detail": text})
        assert out["detail"] == text

    def test_original_dict_not_mutated(self) -> None:
        original = {"password": "hunter2", "symbol": "EURUSD"}
        redact_mapping(original)
        assert original["password"] == "hunter2"


class TestFormatters:
    def test_json_formatter_redacts_sensitive_extra(self) -> None:
        line = _format_extra(
            {"password": "hunter2", "symbol": "EURUSD"}, JsonFormatter()
        )
        assert "hunter2" not in line
        assert '"password": "***"' in line
        assert '"symbol": "EURUSD"' in line

    def test_console_formatter_redacts_sensitive_extra(self) -> None:
        line = _format_extra({"api_key": "sk-abcdef1234567890"}, ConsoleFormatter())
        assert "sk-abcdef1234567890" not in line
        assert "***" in line

    def test_json_formatter_output_remains_valid_json(self) -> None:
        import json

        line = _format_extra(
            {"password": "x", "nested": {"token": "y"}, "list": ["a", "b"]},
            JsonFormatter(),
        )
        payload = json.loads(line)
        assert payload["password"] == "***"
        assert payload["nested"]["token"] == "***"
        assert payload["list"] == ["a", "b"]

    def test_non_json_serializable_value_still_formatted(self) -> None:
        class Odd:
            def __repr__(self) -> str:  # pragma: no cover - trivial
                return "Odd()"

        line = _format_extra({"obj": Odd()}, JsonFormatter())
        assert "Odd()" in line


class TestRedactText:
    def test_redact_text_direct(self) -> None:
        assert "hunter2" not in redact_text("postgresql://u:hunter2@h/db")
        assert redact_text("no secrets here") == "no secrets here"
