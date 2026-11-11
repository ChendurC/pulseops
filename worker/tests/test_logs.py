import json
import logging

from pulseops_worker.logs import JsonFormatter, TextFormatter


def make_record(**extra):
    record = logging.LogRecord(
        "pulseops.worker", logging.INFO, __file__, 1, "job finished", (), None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_log_line_contains_searchable_context_fields():
    line = JsonFormatter().format(
        make_record(job_id=7, correlation_id="abc-123", status="COMPLETED")
    )
    payload = json.loads(line)  # must be valid JSON, one object per line
    assert payload["message"] == "job finished"
    assert payload["job_id"] == 7
    assert payload["correlation_id"] == "abc-123"
    assert payload["status"] == "COMPLETED"
    assert payload["service.name"] == "pulseops-worker"
    assert payload["log.level"] == "INFO"


def test_json_log_omits_fields_that_were_not_provided():
    payload = json.loads(JsonFormatter().format(make_record()))
    assert "job_id" not in payload and "correlation_id" not in payload


def test_console_format_shows_context_as_key_value_pairs():
    text = TextFormatter("%(message)s").format(make_record(job_id=7, status="FAILED"))
    assert text == "job finished job_id=7 status=FAILED"
