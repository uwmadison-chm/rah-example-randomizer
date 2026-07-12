# This file is part of rah-example-randomizer, an example handler for rah.
# Copyright (c) 2026 Center for Healthy Minds
# Distributed under the MIT license; see LICENSE in the project root.

import logging
from datetime import UTC, datetime

import pytest

import rah_example_randomizer
from conftest import DELETE
from rah_example_randomizer.handler import randomize
from redcap_alert_handler.handlers import Message
from redcap_alert_handler.handlers.errors import PermanentError, TransientError
from redcap_alert_handler.handlers.loader import resolve_handler

RANDOM_VALUES = ["control", "treatment", "placebo", "waitlist"]


# The registration goes through real importlib.metadata, so this only passes
# once the package is installed into the venv with `uv sync --all-packages`.
def test_entry_point_resolves_to_the_same_callable():
    resolved = resolve_handler("rah-example-randomizer:randomize")

    assert resolved is rah_example_randomizer.randomize


def test_happy_path_posts_the_expected_form_and_completes(
    fake_redcap, make_config, make_context, make_message, read_stored_row
):
    context = make_context(make_config())
    message = make_message(record_id="R-1001")

    randomize(message, context)

    form = fake_redcap.last_form()
    assert form["content"] == "record"
    assert form["action"] == "import"
    assert form["format"] == "csv"
    assert form["token"] == "SECRET-TOKEN-DO-NOT-LOG"

    header, row = fake_redcap.last_csv_rows()
    assert header == ["study_id", "arm"]
    assert row[0] == "R-1001"
    assert row[1] in RANDOM_VALUES

    value, completed_at = read_stored_row(context.state_dir, "R-1001")
    assert value == row[1]
    assert completed_at is not None


def test_logs_under_rah_hierarchy_and_never_logs_the_record_id(
    fake_redcap, make_config, make_context, make_message, caplog
):
    context = make_context(make_config())

    with caplog.at_level(logging.INFO, logger="redcap_alert_handler"):
        randomize(make_message(record_id="R-1001"), context)

    ours = [
        record
        for record in caplog.records
        if record.name.startswith("redcap_alert_handler.handlers.rah_example_randomizer")
    ]
    assert ours
    assert all("R-1001" not in record.getMessage() for record in ours)


def test_configured_event_name_becomes_a_csv_column(
    fake_redcap, make_config, make_context, make_message
):
    context = make_context(make_config(redcap_event_name="baseline_arm_1"))
    message = make_message(record_id="R-1001")

    randomize(message, context)

    header, row = fake_redcap.last_csv_rows()
    assert header == ["study_id", "redcap_event_name", "arm"]
    assert row == ["R-1001", "baseline_arm_1", row[2]]
    assert row[2] in RANDOM_VALUES


def test_already_completed_record_makes_no_request(
    fake_redcap, make_config, make_context, make_message
):
    context = make_context(make_config())
    randomize(make_message(record_id="R-1001", message_id="<first@example.edu>"), context)
    assert fake_redcap.request_count == 1

    randomize(make_message(record_id="R-1001", message_id="<second@example.edu>"), context)

    assert fake_redcap.request_count == 1


def test_timeout_retry_imports_the_first_claimed_value(
    fake_redcap, make_config, make_context, make_message, read_stored_row
):
    context = make_context(make_config())
    message = make_message(record_id="R-1001")

    fake_redcap.raise_timeout()
    with pytest.raises(TransientError):
        randomize(message, context)

    claimed_value, completed_at = read_stored_row(context.state_dir, "R-1001")
    assert completed_at is None

    fake_redcap.respond_ok()
    randomize(message, context)

    header, row = fake_redcap.last_csv_rows()
    assert row[1] == claimed_value
    _, completed_at_after = read_stored_row(context.state_dir, "R-1001")
    assert completed_at_after is not None


def test_connect_error_is_permanent(fake_redcap, make_config, make_context, make_message):
    context = make_context(make_config())
    fake_redcap.raise_connect_error()

    with pytest.raises(PermanentError):
        randomize(make_message(), context)


def test_error_status_is_permanent_and_leaks_no_record_id(
    fake_redcap, make_config, make_context, make_message, read_stored_row
):
    context = make_context(make_config())
    fake_redcap.respond_error(400, "The value you provided is out of range.")

    with pytest.raises(PermanentError) as exc_info:
        randomize(make_message(record_id="R-SECRET-9999"), context)

    message = str(exc_info.value)
    assert "400" in message
    assert "out of range" in message
    assert "R-SECRET-9999" not in message

    _, completed_at = read_stored_row(context.state_dir, "R-SECRET-9999")
    assert completed_at is None


def test_wrong_count_is_permanent(fake_redcap, make_config, make_context, make_message):
    context = make_context(make_config())
    fake_redcap.respond_count(0)

    with pytest.raises(PermanentError):
        randomize(make_message(), context)


def test_non_toml_body_is_permanent_with_no_request(
    fake_redcap, make_config, make_context, make_message
):
    context = make_context(make_config())
    message = make_message(body_text="this is not = valid = toml")

    with pytest.raises(PermanentError):
        randomize(message, context)

    assert fake_redcap.request_count == 0


def test_body_missing_record_id_is_permanent(
    fake_redcap, make_config, make_context, make_message
):
    context = make_context(make_config())
    message = make_message(body_text='some_other_key = "value"\n')

    with pytest.raises(PermanentError):
        randomize(message, context)

    assert fake_redcap.request_count == 0


def test_none_body_is_permanent(fake_redcap, make_config, make_context):
    context = make_context(make_config())
    message = Message(
        internet_message_id="<msg-1@example.edu>",
        subject="REDCap alert",
        body_text=None,
        body_html=None,
        sender="redcap@example.edu",
        received_at=datetime(2026, 7, 12, 9, 30, tzinfo=UTC),
    )

    with pytest.raises(PermanentError):
        randomize(message, context)

    assert fake_redcap.request_count == 0


def test_missing_config_key_names_the_key(
    fake_redcap, make_config, make_context, make_message
):
    context = make_context(make_config(random_values=DELETE))

    with pytest.raises(PermanentError) as exc_info:
        randomize(make_message(), context)

    assert "random_values" in str(exc_info.value)


def test_missing_secrets_file_is_permanent_and_leaks_no_token(
    fake_redcap, make_config, make_context, make_message, tmp_path
):
    missing = tmp_path / "nope" / "secrets.toml"
    context = make_context(make_config(redcap_secrets_file=str(missing)))

    with pytest.raises(PermanentError) as exc_info:
        randomize(make_message(), context)

    assert "SECRET-TOKEN-DO-NOT-LOG" not in str(exc_info.value)


def test_secrets_file_missing_token_is_permanent(
    fake_redcap, make_config, make_context, make_message, write_secrets
):
    secrets_path = write_secrets({"redcap_api_url": "https://redcap.example.edu/api/"})
    context = make_context(make_config(secrets_path=secrets_path))

    with pytest.raises(PermanentError) as exc_info:
        randomize(make_message(), context)

    assert "redcap_api_token" in str(exc_info.value)


def test_empty_random_values_is_permanent(
    fake_redcap, make_config, make_context, make_message
):
    context = make_context(make_config(random_values=[]))

    with pytest.raises(PermanentError) as exc_info:
        randomize(make_message(), context)

    assert "random_values" in str(exc_info.value)
