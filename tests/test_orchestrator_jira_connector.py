"""Tests for harness/orchestrator/jira_connector.py -- deterministic Jira/Atlassian
issue resolution.

Mirrors tests/test_orchestrator_github.py's own split: a `FakeTransport` injects an
`HttpResult` at the exact seam `resolve_issue`/`fetch_issue` accept (per the module's
own docstring) -- no real Jira instance is ever contacted here. `DEFAULT_TRANSPORT`
(the real `urllib.request` path) is exercised only for its narrow, network-free
behavior (a connection failure against an unroutable host), never against a real Jira
Cloud instance -- there is no live Jira connector configured in this environment (see
the module's own docstring for how that was confirmed).
"""
from __future__ import annotations

import json

import pytest

from harness.orchestrator import jira_connector as jc


class FakeTransport:
    """A controlled fake HTTP adapter -- the exact seam the module's own docstring
    names. Records every call it received for assertions."""

    def __init__(self, result: jc.HttpResult):
        self.result = result
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method: str, url: str, headers: dict) -> jc.HttpResult:
        self.calls.append((method, url, headers))
        return self.result


CREDS = jc.JiraCredentials(base_url="https://example.atlassian.net", email="a@example.invalid", api_token="tok")


def _issue_payload(key: str = "PROJ-123", project_key: str = "PROJ", **overrides: object) -> dict:
    payload = {
        "key": key,
        "fields": {
            "summary": "Fix the flaky login test",
            "description": {
                "type": "doc",
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "The login test is flaky."}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "Acceptance Criteria"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "- Test passes 10x in a row"}]},
                    {"type": "paragraph", "content": [{"type": "text", "text": "- No retries needed"}]},
                ],
            },
            "issuetype": {"name": "Bug"},
            "status": {"name": "To Do"},
            "project": {"key": project_key},
        },
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Issue-key shape
# ---------------------------------------------------------------------------


class TestIssueKeyShape:
    @pytest.mark.parametrize("key", ["PROJ-1", "PROJ-123", "AB2-9", "P234567890-999999999"])
    def test_valid_shapes_accepted(self, key: str) -> None:
        assert jc.is_valid_issue_key(key)

    @pytest.mark.parametrize(
        "key",
        [
            "proj-123",  # lowercase -- case-sensitive on purpose
            "PROJ123",  # no hyphen
            "-123",  # no project prefix
            "PROJ-",  # no number
            "PROJ-0",  # leading zero not allowed by the "no leading zero" rule... actually 0 itself
            "PROJ-01",  # leading zero
            "1PROJ-1",  # must start with a letter
            "",
            "PROJ--1",
            "P" * 11 + "-1",  # project key too long (11 chars, max 10)
        ],
    )
    def test_invalid_shapes_rejected(self, key: str) -> None:
        assert not jc.is_valid_issue_key(key)

    def test_non_string_rejected(self) -> None:
        assert not jc.is_valid_issue_key(123)
        assert not jc.is_valid_issue_key(None)

    def test_normalize_uppercases_and_strips_only(self) -> None:
        assert jc.normalize_issue_key("  proj-123  ") == "PROJ-123"
        assert jc.normalize_issue_key("Proj-123") == "PROJ-123"

    def test_normalize_never_reformats(self) -> None:
        # normalize_issue_key must never insert/remove characters beyond strip+uppercase.
        assert jc.normalize_issue_key("pr oj-123") == "PR OJ-123"

    def test_project_key_from_issue_key(self) -> None:
        assert jc.project_key_from_issue_key("PROJ-123") == "PROJ"
        assert jc.project_key_from_issue_key("AB2-9") == "AB2"


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class TestCredentialsFromEnv:
    def test_all_three_present_returns_credentials(self) -> None:
        creds = jc.JiraCredentials.from_env(
            {"JIRA_BASE_URL": "https://x.atlassian.net/", "JIRA_EMAIL": "a@b.invalid", "JIRA_API_TOKEN": "tok"}
        )
        assert creds is not None
        assert creds.base_url == "https://x.atlassian.net"  # trailing slash stripped
        assert creds.email == "a@b.invalid"
        assert creds.api_token == "tok"

    @pytest.mark.parametrize("missing", ["JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"])
    def test_any_missing_variable_returns_none(self, missing: str) -> None:
        env = {"JIRA_BASE_URL": "https://x.atlassian.net", "JIRA_EMAIL": "a@b.invalid", "JIRA_API_TOKEN": "tok"}
        del env[missing]
        assert jc.JiraCredentials.from_env(env) is None

    def test_blank_variable_returns_none(self) -> None:
        env = {"JIRA_BASE_URL": "  ", "JIRA_EMAIL": "a@b.invalid", "JIRA_API_TOKEN": "tok"}
        assert jc.JiraCredentials.from_env(env) is None

    def test_empty_env_returns_none(self) -> None:
        assert jc.JiraCredentials.from_env({}) is None

    def test_never_raises_on_missing_env(self) -> None:
        # A missing-credentials state is a legitimate, expected connector_unavailable
        # outcome -- from_env must never raise for it.
        jc.JiraCredentials.from_env({})  # no exception


class TestBuildAuthHeader:
    def test_basic_auth_header_shape(self) -> None:
        headers = jc.build_auth_header(CREDS)
        assert headers["Accept"] == "application/json"
        assert headers["Authorization"].startswith("Basic ")

    def test_header_never_leaks_plaintext_token(self) -> None:
        headers = jc.build_auth_header(CREDS)
        assert CREDS.api_token not in headers["Authorization"]


# ---------------------------------------------------------------------------
# ADF -> text / acceptance-criteria extraction
# ---------------------------------------------------------------------------


class TestAdfToText:
    def test_none_returns_empty_string(self) -> None:
        assert jc.adf_to_text(None) == ""

    def test_plain_string_passthrough(self) -> None:
        assert jc.adf_to_text("already plain text") == "already plain text"

    def test_paragraph_and_text_nodes(self) -> None:
        node = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}]}
        assert jc.adf_to_text(node) == "hello"

    def test_multiple_paragraphs_newline_separated(self) -> None:
        node = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "first"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "second"}]},
            ],
        }
        assert jc.adf_to_text(node) == "first\nsecond"

    def test_unrecognized_node_type_walked_generically(self) -> None:
        node = {"type": "customBlock", "content": [{"type": "text", "text": "kept"}]}
        assert jc.adf_to_text(node) == "kept"

    def test_non_dict_non_string_returns_empty(self) -> None:
        assert jc.adf_to_text(42) == ""
        assert jc.adf_to_text([1, 2]) == ""


class TestExtractAcceptanceCriteria:
    def test_no_heading_returns_none(self) -> None:
        assert jc.extract_acceptance_criteria("just a description, no AC section") is None

    def test_empty_text_returns_none(self) -> None:
        assert jc.extract_acceptance_criteria("") is None

    def test_heading_with_bullets(self) -> None:
        text = "Some description.\n\nAcceptance Criteria\n- one\n- two\n"
        assert jc.extract_acceptance_criteria(text) == ["one", "two"]

    def test_heading_case_insensitive_with_colon(self) -> None:
        text = "acceptance criteria:\n1. first\n2. second"
        assert jc.extract_acceptance_criteria(text) == ["first", "second"]

    def test_stops_at_blank_line(self) -> None:
        text = "Acceptance Criteria\n- one\n\nunrelated trailing text"
        assert jc.extract_acceptance_criteria(text) == ["one"]

    def test_heading_with_no_following_lines_returns_none(self) -> None:
        # Never an empty list standing in for "none found" -- only None or non-empty.
        assert jc.extract_acceptance_criteria("Acceptance Criteria\n") is None


# ---------------------------------------------------------------------------
# Payload parsing / classification
# ---------------------------------------------------------------------------


class TestParseIssuePayload:
    def test_matching_key_resolves(self) -> None:
        issue = jc.parse_issue_payload("PROJ-123", _issue_payload(), base_url="https://example.atlassian.net")
        assert issue.issue_key == "PROJ-123"
        assert issue.project_key == "PROJ"
        assert issue.summary == "Fix the flaky login test"
        assert issue.acceptance_criteria == ["Test passes 10x in a row", "No retries needed"]
        assert issue.url == "https://example.atlassian.net/browse/PROJ-123"

    def test_mismatched_key_raises_identity_mismatch(self) -> None:
        with pytest.raises(jc.JiraError) as excinfo:
            jc.parse_issue_payload("PROJ-123", _issue_payload(key="PROJ-999"), base_url="https://x.atlassian.net")
        assert excinfo.value.code == "identity_mismatch"

    def test_non_dict_body_raises_invalid_response(self) -> None:
        with pytest.raises(jc.JiraError) as excinfo:
            jc.parse_issue_payload("PROJ-123", ["not", "a", "dict"], base_url="https://x.atlassian.net")
        assert excinfo.value.code == "invalid_response"

    def test_missing_key_field_raises_invalid_response(self) -> None:
        with pytest.raises(jc.JiraError) as excinfo:
            jc.parse_issue_payload("PROJ-123", {"fields": {}}, base_url="https://x.atlassian.net")
        assert excinfo.value.code == "invalid_response"

    def test_missing_fields_object_raises_invalid_response(self) -> None:
        with pytest.raises(jc.JiraError) as excinfo:
            jc.parse_issue_payload("PROJ-123", {"key": "PROJ-123"}, base_url="https://x.atlassian.net")
        assert excinfo.value.code == "invalid_response"

    def test_project_key_mismatch_raises_invalid_response(self) -> None:
        with pytest.raises(jc.JiraError) as excinfo:
            jc.parse_issue_payload(
                "PROJ-123", _issue_payload(project_key="OTHER"), base_url="https://x.atlassian.net"
            )
        assert excinfo.value.code == "invalid_response"

    def test_never_invents_summary_when_absent(self) -> None:
        payload = _issue_payload()
        del payload["fields"]["summary"]
        issue = jc.parse_issue_payload("PROJ-123", payload, base_url="https://x.atlassian.net")
        assert issue.summary == ""

    def test_string_description_passthrough(self) -> None:
        payload = _issue_payload()
        payload["fields"]["description"] = "plain-string description"
        issue = jc.parse_issue_payload("PROJ-123", payload, base_url="https://x.atlassian.net")
        assert issue.description == "plain-string description"


# ---------------------------------------------------------------------------
# resolve_issue -- the one real (or, in a test, fake) entry point
# ---------------------------------------------------------------------------


class TestResolveIssue:
    def test_invalid_key_shape_never_attempts_network_call(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=200, body="{}"))
        result = jc.resolve_issue("not-a-real-key", CREDS, transport)
        assert result.status == "invalid_issue_key"
        assert transport.calls == []

    def test_missing_credentials_never_attempts_network_call(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=200, body="{}"))
        result = jc.resolve_issue("PROJ-123", None, transport)
        assert result.status == "connector_unavailable"
        assert transport.calls == []

    def test_200_matching_key_resolves(self) -> None:
        body = json.dumps(_issue_payload())
        transport = FakeTransport(jc.HttpResult(status_code=200, body=body))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "resolved"
        assert result.issue is not None
        assert result.issue.issue_key == "PROJ-123"
        assert len(transport.calls) == 1
        method, url, headers = transport.calls[0]
        assert method == "GET"
        assert "PROJ-123" in url
        assert "Authorization" in headers

    def test_200_mismatched_key_classified_identity_mismatch(self) -> None:
        body = json.dumps(_issue_payload(key="PROJ-999"))
        transport = FakeTransport(jc.HttpResult(status_code=200, body=body))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "identity_mismatch"
        assert result.issue is None

    def test_404_classified_not_found(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=404, body="{}"))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "not_found"

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_401_403_classified_unauthorized(self, status_code: int) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=status_code, body="{}"))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "unauthorized"

    def test_unexpected_status_classified_invalid_response(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=500, body="server error"))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "invalid_response"

    def test_transport_failure_classified_invalid_response(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=-1, body="", reason="Name or service not known"))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "invalid_response"

    def test_malformed_json_body_classified_invalid_response(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=200, body="{not json"))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.status == "invalid_response"

    def test_raw_never_contains_credentials(self) -> None:
        body = json.dumps(_issue_payload())
        transport = FakeTransport(jc.HttpResult(status_code=200, body=body))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        raw_str = json.dumps(result.raw)
        assert CREDS.api_token not in raw_str
        assert "Authorization" not in raw_str

    def test_every_status_is_in_resolution_statuses(self) -> None:
        cases = [
            ("not-a-key", None),
            ("PROJ-123", None),
        ]
        for key, creds in cases:
            result = jc.resolve_issue(key, creds, FakeTransport(jc.HttpResult(status_code=200, body="{}")))
            assert result.status in jc.RESOLUTION_STATUSES

    def test_never_synthesizes_placeholder_issue_on_failure(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=404, body="{}"))
        result = jc.resolve_issue("PROJ-123", CREDS, transport)
        assert result.issue is None


class TestHttpResultAsDict:
    def test_short_body_not_truncated(self) -> None:
        result = jc.HttpResult(status_code=200, body="short")
        d = result.as_dict()
        assert d["body"] == "short"
        assert d["body_truncated"] is False

    def test_long_body_truncated(self) -> None:
        result = jc.HttpResult(status_code=200, body="x" * 5000)
        d = result.as_dict()
        assert len(d["body"]) == jc._MAX_RETAINED_BODY_CHARS
        assert d["body_truncated"] is True


class TestFetchIssue:
    def test_fetch_issue_requests_only_standard_fields(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=200, body="{}"))
        jc.fetch_issue("PROJ-123", CREDS, transport)
        _, url, _ = transport.calls[0]
        assert "customfield" not in url
        assert "fields=summary,description,issuetype,status,project" in url


class TestDefaultTransport:
    def test_default_transport_used_when_none_supplied(self) -> None:
        # Never actually contacts a real Jira instance in this test: a loopback port
        # nothing listens on refuses the connection immediately (fast, deterministic),
        # unlike an unroutable/blackholed address that would wait out the real timeout.
        # Exercises the real DEFAULT_TRANSPORT path only for its own connection-failure
        # classification.
        creds = jc.JiraCredentials(base_url="http://127.0.0.1:1", email="a@b.invalid", api_token="tok")
        result = jc.resolve_issue("PROJ-1", creds)
        assert result.status == "invalid_response"
        assert result.raw["response"]["status_code"] == -1
