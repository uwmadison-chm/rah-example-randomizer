# This file is part of rah-example-randomizer, an example handler for rah.
# Copyright (c) 2026 Center for Healthy Minds
# Distributed under the MIT license; see LICENSE in the project root.

"""The randomize handler: pick a value for one record and import it to REDCap.

Each alert names one record in its TOML body; the handler rolls one of the
configured `random_values` for it and writes that value to `randomize_field`
through the REDCap API. The roll and the import are split by the claim store
(see `store`) so a retry re-sends the first attempt's value instead of picking
a new one.

Nothing here logs a record id, a subject, a body, or the chosen value: a
record id is participant-adjacent, so the log lines carry only the message's
internet id and what happened. The logger comes from rah's `get_logger`, so
those lines land in rah's own log output instead of an unconfigured
hierarchy nobody sees. REDCap's own error text can go into a
`PermanentError` message, but the API token never does.
"""

from __future__ import annotations

import csv
import io
import random
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
from redcap_alert_handler import (
    Context,
    HandlerError,
    Message,
    PermanentError,
    TransientError,
    get_logger,
)

from rah_example_randomizer import store

logger = get_logger(__name__)

REQUEST_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class _HandlerConfig:
    redcap_secrets_file: str
    record_id_field: str
    redcap_event_name: str | None
    randomize_field: str
    random_values: list[str]


@dataclass(frozen=True, slots=True)
class _Secrets:
    api_url: str
    api_token: str


def _build_client() -> httpx.Client:
    # The one place a client is built, so a test can monkeypatch it to a
    # client wired to httpx.MockTransport without touching the network.
    return httpx.Client(timeout=REQUEST_TIMEOUT)


def _require_str(config: Mapping[str, object], key: str) -> str:
    if key not in config:
        raise PermanentError(f"config is missing required key {key!r}")
    value = config[key]
    if not isinstance(value, str):
        raise PermanentError(f"config key {key!r} must be a string")
    return value


def _require_nonempty_str(config: Mapping[str, object], key: str) -> str:
    value = _require_str(config, key)
    if not value.strip():
        raise PermanentError(f"config key {key!r} must not be empty")
    return value


def _optional_str(config: Mapping[str, object], key: str) -> str | None:
    if key not in config:
        return None
    value = config[key]
    if not isinstance(value, str):
        raise PermanentError(f"config key {key!r} must be a string")
    return value


def _require_values(config: Mapping[str, object]) -> list[str]:
    if "random_values" not in config:
        raise PermanentError("config is missing required key 'random_values'")
    raw = config["random_values"]
    if not isinstance(raw, list) or not raw:
        raise PermanentError("config key 'random_values' must be a non-empty list of strings")
    values: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise PermanentError("config key 'random_values' must contain only non-empty strings")
        values.append(item)
    return values


def _read_config(config: Mapping[str, object]) -> _HandlerConfig:
    return _HandlerConfig(
        redcap_secrets_file=_require_str(config, "redcap_secrets_file"),
        record_id_field=_require_nonempty_str(config, "record_id_field"),
        redcap_event_name=_optional_str(config, "redcap_event_name"),
        randomize_field=_require_nonempty_str(config, "randomize_field"),
        random_values=_require_values(config),
    )


def _read_record_id(body_text: str | None) -> str:
    if body_text is None:
        raise PermanentError("message has no text body")
    try:
        data = tomllib.loads(body_text)
    except tomllib.TOMLDecodeError as e:
        raise PermanentError(f"message body is not valid TOML: {e}") from e
    record_id = data.get("record_id")
    if not isinstance(record_id, str):
        raise PermanentError("message body has no string 'record_id'")
    record_id = record_id.strip()
    if not record_id:
        raise PermanentError("message body 'record_id' is empty")
    return record_id


def _read_secrets(path_str: str) -> _Secrets:
    path = Path(path_str)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PermanentError(f"could not read secrets file {path_str!r}: {e}") from e
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise PermanentError(f"secrets file {path_str!r} is not valid TOML: {e}") from e
    api_url = data.get("redcap_api_url")
    if not isinstance(api_url, str) or not api_url.strip():
        raise PermanentError(f"secrets file {path_str!r} has no string 'redcap_api_url'")
    api_token = data.get("redcap_api_token")
    if not isinstance(api_token, str) or not api_token.strip():
        # Never put the token in an error; only its absence is reportable.
        raise PermanentError(f"secrets file {path_str!r} has no string 'redcap_api_token'")
    return _Secrets(api_url=api_url, api_token=api_token)


def _build_csv(config: _HandlerConfig, record_id: str, value: str) -> str:
    header = [config.record_id_field]
    row = [record_id]
    if config.redcap_event_name is not None:
        header.append("redcap_event_name")
        row.append(config.redcap_event_name)
    header.append(config.randomize_field)
    row.append(value)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerow(row)
    return buffer.getvalue()


def _redcap_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and "error" in payload:
        return str(payload["error"])
    return response.text[:500]


def _import_record(secrets: _Secrets, csv_data: str) -> None:
    form = {
        "token": secrets.api_token,
        "content": "record",
        "action": "import",
        "format": "csv",
        "type": "flat",
        "overwriteBehavior": "normal",
        "forceAutoNumber": "false",
        "returnContent": "count",
        "data": csv_data,
    }
    try:
        with _build_client() as client:
            response = client.post(secrets.api_url, data=form)
    except httpx.TimeoutException as e:
        raise TransientError(f"REDCap request timed out: {e}") from e
    except httpx.HTTPError as e:
        raise PermanentError(f"REDCap request failed: {e}") from e

    if response.status_code != 200:
        detail = _redcap_error_detail(response)
        raise PermanentError(f"REDCap returned HTTP {response.status_code}: {detail}")

    try:
        payload = response.json()
    except ValueError as e:
        raise PermanentError(f"REDCap response was not JSON: {response.text[:500]}") from e
    count = payload.get("count") if isinstance(payload, dict) else None
    if count != 1:
        raise PermanentError(f"REDCap imported {count!r} records, expected exactly 1")


def randomize(message: Message, context: Context) -> None:
    """Randomize the record named in the message and import the value to REDCap.

    Reads its route's config (see the README for the keys), pulls `record_id`
    from the TOML body, claims a value in the store, and imports it. A record
    the store already has marked complete returns without an import. A REDCap
    timeout raises `TransientError` so the message retries; every other failure
    raises `PermanentError`.
    """
    try:
        config = _read_config(context.config)
        record_id = _read_record_id(message.body_text)
        secrets = _read_secrets(config.redcap_secrets_file)

        db_path = context.state_dir / "randomizations.sqlite3"
        candidate = random.choice(config.random_values)
        claim = store.claim(db_path, record_id, candidate)
        if claim.completed:
            logger.info("randomize: %s already randomized, skipping", message.internet_message_id)
            return

        csv_data = _build_csv(config, record_id, claim.value)
        _import_record(secrets, csv_data)
        store.mark_completed(db_path, record_id)
        logger.info("randomize: randomized one record for %s", message.internet_message_id)
    except HandlerError:
        # TransientError and PermanentError are the handler's own vocabulary;
        # let them through untouched.
        raise
    except Exception as e:
        raise PermanentError(f"unexpected failure in randomize handler: {e}") from e
