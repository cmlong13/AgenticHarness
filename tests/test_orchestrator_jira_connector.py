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


# ---------------------------------------------------------------------------
# jira/ skill pack: create-ticket / edit-ticket / field reference
# ---------------------------------------------------------------------------


class ScriptedTransport:
    """Returns the scripted HttpResults in order and records every call, including the
    write body -- the same seam FakeTransport uses, extended for POST/PUT."""

    def __init__(self, *results: jc.HttpResult):
        self.results = list(results)
        self.calls: list[dict] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None = None) -> jc.HttpResult:
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body is not None else None})
        if not self.results:
            raise AssertionError(f"unexpected extra HTTP call: {method} {url}")
        return self.results.pop(0)

    @property
    def methods(self) -> list[str]:
        return [c["method"] for c in self.calls]


def _ok_issue(key="PROJ-7", *, summary="Add a health check", description="Line one\nLine two",
              issue_type="Task", project_key="PROJ") -> jc.HttpResult:
    doc = jc.description_to_adf(description)
    return jc.HttpResult(200, json.dumps({"key": key, "fields": {
        "summary": summary, "description": doc, "issuetype": {"name": issue_type},
        "status": {"name": "To Do"}, "project": {"key": project_key},
    }}))


AUTH = {"authorized": True, "authorization_source": "user instruction: 'create a ticket for the health check'",
        "expected_base_url": "https://example.atlassian.net"}
CREATE_OK = {"project_key": "PROJ", "issue_type": "Task",
             "fields": {"summary": "Add a health check", "description": "Line one\n\nLine two"}}


def _create(transport=None, credentials=CREDS, **overrides):
    kwargs = {**AUTH, **CREATE_OK, **overrides}
    return jc.create_issue(credentials=credentials, transport=transport, **kwargs)


def _edit(transport=None, credentials=CREDS, **overrides):
    kwargs = {**AUTH, "issue_key": "PROJ-7", "fields": {"summary": "Add a health check"}, **overrides}
    return jc.edit_issue(credentials=credentials, transport=transport, **kwargs)


def _never_called():
    return ScriptedTransport()  # any call raises AssertionError


class TestCreateIssueAuthorization:
    @pytest.mark.parametrize("authorized", [False, None, "true", "True", 1, "yes", [True], {"v": True}])
    def test_anything_but_boolean_true_is_refused_before_any_call(self, authorized) -> None:
        transport = _never_called()
        result = _create(transport, authorized=authorized)
        assert result["status"] == "not_authorized"
        assert transport.calls == []

    @pytest.mark.parametrize("source", [None, "", "   ", 5, "x" * 501, "line\x00break"])
    def test_missing_or_malformed_authorization_source_is_refused(self, source) -> None:
        transport = _never_called()
        result = _create(transport, authorization_source=source)
        assert result["status"] == "not_authorized"
        assert transport.calls == []

    def test_authorization_is_checked_before_validation_and_credentials(self) -> None:
        # Even an invalid project and no connector report not_authorized first: an
        # unauthorized request learns nothing about the environment.
        result = _create(None, credentials=None, authorized=False, project_key="bad")
        assert result["status"] == "not_authorized"


class TestCreateIssueValidation:
    @pytest.mark.parametrize("project_key", ["proj", "P", "PROJECT1234X", "PR-OJ", "1PROJ", "", None, "--help", "PROJ "])
    def test_malformed_project_key_rejected_before_any_call(self, project_key) -> None:
        transport = _never_called()
        assert _create(transport, project_key=project_key)["status"] == "invalid_input"
        assert transport.calls == []

    @pytest.mark.parametrize("issue_type", ["Epic", "Sub-task", "task", "Improvement", "", None, 3])
    def test_unsupported_issue_type_rejected(self, issue_type) -> None:
        transport = _never_called()
        assert _create(transport, issue_type=issue_type)["status"] == "unsupported_issue_type"
        assert transport.calls == []

    @pytest.mark.parametrize("field", ["customfield_10010", "assignee", "labels", "priority", "status", "comment", "sumary"])
    def test_unsupported_field_rejected(self, field) -> None:
        transport = _never_called()
        result = _create(transport, fields={"summary": "x", field: "y"})
        assert result["status"] == "unsupported_field"
        assert field in result["reason"]
        assert transport.calls == []

    def test_custom_field_refusal_says_ids_are_never_guessed(self) -> None:
        reason = _create(_never_called(), fields={"summary": "x", "customfield_10016": 3})["reason"]
        assert "never guessed" in reason and "FIELD_REFERENCE.md" in reason

    @pytest.mark.parametrize("fields", [
        {}, None, "summary", {"description": "no summary"}, {"summary": ""}, {"summary": "   "},
        {"summary": "two\nlines"}, {"summary": "x" * 256}, {"summary": 5}, {"summary": "ok", "description": 5},
        {"summary": "ok", "description": "bad\x07bell"}, {"summary": "ok", "description": "x" * 32_001},
    ])
    def test_malformed_fields_rejected(self, fields) -> None:
        transport = _never_called()
        assert _create(transport, fields=fields)["status"] == "invalid_input"
        assert transport.calls == []

    def test_connector_unavailable_is_a_refusal_not_success(self) -> None:
        result = _create(_never_called(), credentials=None)
        assert result["status"] == "connector_unavailable"
        assert result["mutation"] is None and result["issue_key"] is None

    @pytest.mark.parametrize("expected", [None, "", "https://other.atlassian.net", "ftp://example.atlassian.net",
                                          "https://user@example.atlassian.net", "https://example.atlassian.net?x=1"])
    def test_wrong_or_missing_expected_instance_refused(self, expected) -> None:
        transport = _never_called()
        assert _create(transport, expected_base_url=expected)["status"] == "wrong_instance"
        assert transport.calls == []

    def test_expected_instance_match_ignores_case_and_trailing_slash(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"key": "PROJ-7"}'), _ok_issue())
        assert _create(transport, expected_base_url="HTTPS://Example.Atlassian.net/")["status"] == "created"


class TestCreateIssueRequestAndVerification:
    def test_successful_create_sends_exact_request_then_rereads(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"id": "1", "key": "PROJ-7"}'), _ok_issue())
        result = _create(transport)
        assert result["status"] == "created"
        assert result["issue_key"] == "PROJ-7"
        assert transport.methods == ["POST", "GET"]
        post = transport.calls[0]
        assert post["url"] == "https://example.atlassian.net/rest/api/3/issue"
        assert post["headers"]["Content-Type"] == "application/json"
        assert post["body"] == {"fields": {
            "project": {"key": "PROJ"}, "issuetype": {"name": "Task"}, "summary": "Add a health check",
            "description": jc.description_to_adf("Line one\nLine two"),
        }}
        assert transport.calls[1]["url"].startswith("https://example.atlassian.net/rest/api/3/issue/PROJ-7?")
        assert result["verification"]["verified"] is True
        assert result["verification"]["mismatches"] == []

    def test_only_supplied_fields_are_sent(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"key": "PROJ-7"}'), _ok_issue(description=""))
        result = _create(transport, fields={"summary": "Add a health check"})
        assert result["status"] == "created"
        assert set(transport.calls[0]["body"]["fields"]) == {"project", "issuetype", "summary"}

    def test_result_never_contains_credentials(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"key": "PROJ-7"}'), _ok_issue())
        dumped = json.dumps(_create(transport))
        assert "Authorization" not in dumped and "Basic " not in dumped and '"tok"' not in dumped

    @pytest.mark.parametrize("http_result,status", [
        (jc.HttpResult(400, '{"errorMessages": [], "errors": {"issuetype": "invalid"}}'), "rejected"),
        (jc.HttpResult(401, ""), "unauthorized"),
        (jc.HttpResult(403, ""), "unauthorized"),
        (jc.HttpResult(404, ""), "not_found"),
        (jc.HttpResult(500, ""), "indeterminate"),
        (jc.HttpResult(-1, "", reason="timed out"), "indeterminate"),
        (jc.HttpResult(302, ""), "invalid_response"),
        (jc.HttpResult(200, '{"key": "PROJ-7"}'), "invalid_response"),
    ])
    def test_failures_propagate_with_classification_and_no_reread(self, http_result, status) -> None:
        transport = ScriptedTransport(http_result)
        result = _create(transport)
        assert result["status"] == status
        assert transport.methods == ["POST"]  # never retried, never re-read on a failed write
        assert result["verification"] is None

    def test_rejected_reason_carries_jiras_own_message(self) -> None:
        body = '{"errorMessages": ["Project PROJ does not allow Story"], "errors": {}}'
        result = _create(ScriptedTransport(jc.HttpResult(400, body)))
        assert "does not allow Story" in result["reason"]

    @pytest.mark.parametrize("body", ["not json", "{}", '{"key": "garbage"}', '{"key": 7}'])
    def test_201_without_a_valid_key_is_unverified(self, body) -> None:
        result = _create(ScriptedTransport(jc.HttpResult(201, body)))
        assert result["status"] == "created_unverified"

    def test_201_with_key_in_another_project_is_unverified(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"key": "OTHER-1"}'))
        result = _create(transport)
        assert result["status"] == "created_unverified"
        assert transport.methods == ["POST"]

    def test_reread_mismatch_is_unverified_not_created(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"key": "PROJ-7"}'), _ok_issue(summary="Something else"))
        result = _create(transport)
        assert result["status"] == "created_unverified"
        assert [m["field"] for m in result["verification"]["mismatches"]] == ["summary"]

    def test_reread_issue_type_mismatch_is_unverified(self) -> None:
        transport = ScriptedTransport(jc.HttpResult(201, '{"key": "PROJ-7"}'), _ok_issue(issue_type="Bug"))
        assert _create(transport)["status"] == "created_unverified"

    @pytest.mark.parametrize("reread", [jc.HttpResult(404, ""), jc.HttpResult(-1, ""), jc.HttpResult(200, "oops")])
    def test_failed_reread_is_unverified_never_created(self, reread) -> None:
        result = _create(ScriptedTransport(jc.HttpResult(201, '{"key": "PROJ-7"}'), reread))
        assert result["status"] == "created_unverified"
        assert result["verification"]["verified"] is False

    def test_every_status_is_declared(self) -> None:
        for status in ("created", "created_unverified", "not_authorized", "indeterminate", "rejected", "wrong_instance"):
            assert status in jc.CREATE_STATUSES


class TestEditIssue:
    @pytest.mark.parametrize("authorized", [False, "true", 1, None])
    def test_authorization_required_before_any_call(self, authorized) -> None:
        transport = _never_called()
        assert _edit(transport, authorized=authorized)["status"] == "not_authorized"
        assert transport.calls == []

    @pytest.mark.parametrize("key", ["PROJ", "PROJ-0", "-PROJ-1", "PROJ-1; rm", "", None, 12])
    def test_malformed_issue_key_rejected_before_any_call(self, key) -> None:
        transport = _never_called()
        assert _edit(transport, issue_key=key)["status"] == "invalid_issue_key"
        assert transport.calls == []

    @pytest.mark.parametrize("field", ["status", "comment", "customfield_10020", "assignee", "project", "issuetype"])
    def test_fields_outside_the_allowlist_rejected(self, field) -> None:
        transport = _never_called()
        assert _edit(transport, fields={field: "x"})["status"] == "unsupported_field"
        assert transport.calls == []

    def test_status_refusal_explains_transitions_are_not_performed(self) -> None:
        assert "transition" in _edit(_never_called(), fields={"status": "Done"})["reason"]

    @pytest.mark.parametrize("fields", [{}, None, {"summary": ""}, {"description": 3}])
    def test_empty_or_malformed_fields_rejected(self, fields) -> None:
        assert _edit(_never_called(), fields=fields)["status"] == "invalid_input"

    def test_connector_unavailable_and_wrong_instance_send_nothing(self) -> None:
        assert _edit(_never_called(), credentials=None)["status"] == "connector_unavailable"
        assert _edit(_never_called(), expected_base_url="https://other.example")["status"] == "wrong_instance"

    def test_successful_edit_prereads_sends_only_supplied_fields_then_rereads(self) -> None:
        transport = ScriptedTransport(_ok_issue(summary="Old"), jc.HttpResult(204, ""), _ok_issue())
        result = _edit(transport, issue_key="proj-7")
        assert result["status"] == "updated"
        assert result["issue_key"] == "PROJ-7"
        assert transport.methods == ["GET", "PUT", "GET"]
        put = transport.calls[1]
        assert put["url"] == "https://example.atlassian.net/rest/api/3/issue/PROJ-7"
        assert put["body"] == {"fields": {"summary": "Add a health check"}}
        assert result["pre_read"]["issue"]["summary"] == "Old"
        assert result["verification"]["expected"] == {"summary": "Add a health check"}

    def test_description_edit_is_verified_by_lines(self) -> None:
        transport = ScriptedTransport(_ok_issue(), jc.HttpResult(204, ""), _ok_issue(description="New\nText"))
        assert _edit(transport, fields={"description": "New\n\nText\n"})["status"] == "updated"

    @pytest.mark.parametrize("pre,status", [
        (jc.HttpResult(404, ""), "not_found"),
        (jc.HttpResult(403, ""), "unauthorized"),
        (_ok_issue(key="PROJ-8"), "identity_mismatch"),
    ])
    def test_failed_preread_sends_nothing(self, pre, status) -> None:
        transport = ScriptedTransport(pre)
        result = _edit(transport)
        assert result["status"] == status
        assert transport.methods == ["GET"]
        assert result["mutation"] is None

    @pytest.mark.parametrize("http_result,status", [
        (jc.HttpResult(400, '{"errors": {"summary": "too long"}}'), "rejected"),
        (jc.HttpResult(403, ""), "unauthorized"),
        (jc.HttpResult(503, ""), "indeterminate"),
        (jc.HttpResult(-1, ""), "indeterminate"),
    ])
    def test_write_failures_propagate_without_reread(self, http_result, status) -> None:
        transport = ScriptedTransport(_ok_issue(), http_result)
        result = _edit(transport)
        assert result["status"] == status
        assert transport.methods == ["GET", "PUT"]
        assert result["verification"] is None

    def test_accepted_edit_that_did_not_stick_is_unverified(self) -> None:
        transport = ScriptedTransport(_ok_issue(summary="Old"), jc.HttpResult(204, ""), _ok_issue(summary="Old"))
        result = _edit(transport)
        assert result["status"] == "updated_unverified"
        assert result["verification"]["mismatches"][0]["observed"] == "Old"

    def test_failed_reread_is_unverified(self) -> None:
        transport = ScriptedTransport(_ok_issue(), jc.HttpResult(204, ""), jc.HttpResult(-1, ""))
        assert _edit(transport)["status"] == "updated_unverified"

    def test_every_status_is_declared(self) -> None:
        for status in ("updated", "updated_unverified", "invalid_issue_key", "unsupported_field", "indeterminate"):
            assert status in jc.EDIT_STATUSES


class TestReadIsReadOnly:
    def test_resolve_issue_sends_only_one_get(self) -> None:
        transport = ScriptedTransport(_ok_issue())
        assert jc.resolve_issue("PROJ-7", CREDS, transport).status == "resolved"
        assert transport.methods == ["GET"]
        assert transport.calls[0]["body"] is None

    def test_read_fields_are_the_requested_fields(self) -> None:
        transport = FakeTransport(jc.HttpResult(status_code=200, body="{}"))
        jc.fetch_issue("PROJ-1", CREDS, transport)
        assert transport.calls[0][1].endswith("?fields=" + ",".join(jc.READ_FIELDS))


class TestFieldReference:
    """FIELD_REFERENCE.md must document exactly what jira_connector implements."""

    from pathlib import Path as _Path

    DOC = _Path(__file__).resolve().parents[1] / ".claude" / "skills" / "jira" / "FIELD_REFERENCE.md"
    SOURCE = _Path(__file__).resolve().parents[1] / "harness" / "orchestrator" / "jira_connector.py"

    def _section_first_column(self, heading: str) -> list[str]:
        import re

        text = self.DOC.read_text(encoding="utf-8")
        start = text.index(f"## {heading}\n")
        end = text.find("\n## ", start + 1)
        section = text[start: end if end != -1 else len(text)]
        return re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE)

    def test_file_exists_and_is_linked_from_skill(self) -> None:
        assert self.DOC.exists()
        skill = (self.DOC.parent / "SKILL.md").read_text(encoding="utf-8")
        assert "FIELD_REFERENCE.md" in skill

    def test_read_fields_match(self) -> None:
        assert self._section_first_column("Read-ticket fields") == list(jc.READ_FIELDS)

    def test_create_fields_match(self) -> None:
        documented = self._section_first_column("Create-ticket fields")
        assert documented == ["project_key", "issue_type", *jc.CREATE_FIELDS]

    def test_edit_fields_match(self) -> None:
        assert self._section_first_column("Edit-ticket fields") == list(jc.EDIT_FIELDS)

    def test_issue_types_match(self) -> None:
        assert self._section_first_column("Supported issue types") == list(jc.SUPPORTED_ISSUE_TYPES)

    def test_unsupported_standard_fields_match(self) -> None:
        documented = self._section_first_column("Unsupported standard fields")
        assert sorted(documented) == sorted(jc._UNSUPPORTED_FIELD_REASONS)

    def test_no_custom_field_ids_configured_or_fabricated(self) -> None:
        import re

        assert jc.CUSTOM_FIELD_IDS == {}
        doc = self.DOC.read_text(encoding="utf-8")
        assert "No custom field ids are configured" in doc
        assert "never guess" in doc
        assert re.search(r"customfield_\d", doc) is None
        assert re.search(r"customfield_\d", self.SOURCE.read_text(encoding="utf-8")) is None

    def test_safe_custom_field_procedure_documented(self) -> None:
        doc = self.DOC.read_text(encoding="utf-8")
        assert "## Adding a custom field safely" in doc
        assert "Never take" in doc and "CUSTOM_FIELD_IDS" in doc
