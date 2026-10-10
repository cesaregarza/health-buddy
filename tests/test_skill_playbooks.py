"""Playbook examples and grants use canonical contracts; no client session."""

import json
import re
from pathlib import Path

import pytest

from health_buddy.connect_agent import BASE_MANAGED, MANAGED
from health_buddy.core.domain import decode, identifier, object_value
from health_buddy.core.loggers import FIELDS
from health_buddy.core.service_api import ServiceError
from health_buddy.mcp.schemas import validate
from health_buddy.security.actions import AGENT_GRANTS
from health_buddy.transport.limits import EnvelopeError
from health_buddy.transport.security import request_payload

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "src/health_buddy/integrations/codex/health-buddy"
GRANT_FIELDS = {"grants", "sourceIds", "readSources", "readKinds"}
EXAMPLE = re.compile(
    r"^[ ]{0,3}`{3}json health-buddy:([^\n]*)\n(.*?)^[ ]{0,3}`{3}[ \t]*$",
    re.M | re.S,
)


def test_router_names_every_owned_playbook_within_forty_lines():
    router = (SKILL / "SKILL.md").read_text()
    assert len(router.splitlines()) <= 40
    assert router.startswith("---\nname: health-buddy\n")
    playbooks = {
        path.relative_to(SKILL).as_posix()
        for path in (SKILL / "playbooks").glob("*.md")
    }
    assert playbooks
    linked = set(re.findall(r"\[[^\]]+\]\((playbooks/[^)]+)\)", router))
    assert linked == playbooks
    assert set(MANAGED) == set(BASE_MANAGED) | playbooks
    for name in playbooks:
        headers = (SKILL / name).read_text().splitlines()[:3]
        assert [line.split(":", 1)[0] for line in headers] == [
            "Use when",
            "Grant",
            "Tools",
        ]
    assert "docs/agent-guide.md" in router
    assert (ROOT / "docs/skills.md").is_file()
    for client in ("codex", "claude"):
        guide = (ROOT / f"docs/{client}-integration.md").read_text()
        assert "(skills.md)" in guide


def test_status_recipe_uses_actual_context_schema_and_two_line_output():
    status = (SKILL / "playbooks/status.md").read_text()
    headers = status.splitlines()[:3]
    assert json.loads(headers[1].removeprefix("Grant: ")) == {
        "grants": ["records:read"],
        "sourceIds": [],
        "readSources": ["manual"],
        "readKinds": ["body-mass"],
    }
    assert headers[2] == "Tools: sync_status, get_context"
    assert status.index("call `sync_status`") < status.index("call `get_context`")
    assert validate("sync_status", {}) == {}
    examples = re.findall(r"```json health-buddy:get_context\n(.*?)\n```", status, re.S)
    assert len(examples) == 1
    request = json.loads(examples[0])
    assert validate("get_context", request) == {
        "scopes": ["profile", "weight"],
        "days": 7,
        "limit": 20,
    }
    output = re.findall(r"```text\n(.*?)\n```", status, re.S)
    assert len(output) == 1
    lines = output[0].splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Health Buddy:")
    assert "stale=<yes/no/unknown>" in lines[0]
    assert "truncated=<yes/no/unknown>" in lines[0]
    assert lines[1].startswith("Latest records (profile/weight, 7 days):")
    assert "missingness/restriction reason" in lines[1]


def validate_examples(document):
    openings = re.findall(r"^[ ]{0,3}`{3}json health-buddy:[^\n]*$", document, re.M)
    examples = EXAMPLE.findall(document)
    assert len(examples) == len(openings), "Unclosed health-buddy example"
    for tool, body in examples:
        validate(tool, decode(body))
    return len(examples)


def grant_scope(value):
    scope = object_value(value, GRANT_FIELDS)
    request_payload(
        "grants.create", {"name": "Playbook contract", **scope, "readFields": None}
    )
    assert set(scope["grants"]) <= AGENT_GRANTS, "Unknown agent grant"
    for entries in scope.values():
        if entries is not None:
            for entry in entries:
                identifier(entry)
    return scope


def example_policy():
    guide = (ROOT / "docs/install-preflight.md").read_text()
    stage = guide.split("## Connect the owner's coding agent and view status\n", 1)[1]
    stage = stage.split("\n## ", 1)[0]
    examples = re.findall(
        r"""cat > "\$PRIVATE_CLIENT/policy.json" <<'EOF'\n(.*?)\nEOF""",
        stage,
        re.S,
    )
    assert len(examples) == 1
    policy = decode(examples[0])
    request_payload("grants.create", policy)
    return grant_scope({key: policy[key] for key in GRANT_FIELDS})


def assert_policy_contract(document, policy):
    headers = re.findall(r"^Grant:(.*)$", document, re.M)
    assert len(headers) == 1, "Expected one Grant JSON header"
    required = grant_scope(decode(headers[0]))
    missing = {}
    for key, needed in required.items():
        available = policy[key]
        # Null read visibility is unrestricted; write sources are always explicit.
        if available is None:
            continue
        if needed is None:
            missing[key] = None
        elif absent := [value for value in needed if value not in available]:
            missing[key] = absent
    exceptions = re.findall(r"^Add to policy:(.*)$", document, re.M)
    assert len(exceptions) <= 1, "Expected at most one Add to policy line"
    if not exceptions:
        assert not missing, f"Stage 7 policy missing: {missing}"
        return
    additions = object_value(decode(exceptions[0]), set(), GRANT_FIELDS)
    grant_scope({key: additions.get(key, []) for key in GRANT_FIELDS})
    assert missing, "Add to policy must describe an unsatisfied requirement"
    assert additions.keys() == missing.keys(), "Add to policy needs the missing scopes"
    for key, needed in missing.items():
        supplied = additions[key]
        assert supplied == needed or (
            supplied is not None and needed is not None and set(supplied) == set(needed)
        ), "Add to policy must name exactly the missing requirements"


def test_every_playbook_tool_example_matches_the_canonical_schema():
    assert sum(
        validate_examples(path.read_text())
        for path in sorted((SKILL / "playbooks").glob("*.md"))
    )


def test_every_playbook_grant_is_satisfied_or_has_an_explicit_policy_addition():
    policy = example_policy()
    playbooks = sorted((SKILL / "playbooks").glob("*.md"))
    assert playbooks
    for path in playbooks:
        assert_policy_contract(path.read_text(), policy)


def tool_example(tool, value):
    return f"```json health-buddy:{tool}\n{json.dumps(value)}\n```\n"


@pytest.mark.parametrize(
    "document",
    [
        tool_example("get_context", {"scopes": ["body-mass"]}),
        tool_example("get_context", {"scopes": ["weight"], "unknown": True}),
        tool_example("unknown_tool", {}),
        tool_example("", {}),
        tool_example("sync_status", []),
        "```json health-buddy:get_context\n{\n```\n",
        '```json health-buddy:get_context\n{"scopes":["weight"],"scopes":[]}\n```\n',
        "```json health-buddy:sync_status\n{}",
    ],
)
def test_bad_tool_examples_are_refused(document):
    with pytest.raises((AssertionError, ServiceError)):
        validate_examples(document)


WRITE_EXAMPLE = {
    "intentId": "synthetic-playbook",
    "identity": {
        key: "11111111-1111-4111-8111-111111111111"
        for key in ("installationId", "datasetId", "restoreEpoch")
    },
    "expectedRevision": 0,
}


@pytest.mark.parametrize(
    ("workout_changes", "set_changes"),
    [
        ({"schema_version": 2}, {}),
        ({"unknown": True}, {}),
        ({"sets": "not an array"}, {}),
        ({}, {"reps": 0}),
        ({}, {"load_basis": "invented"}),
        ({}, {"load_lb": -1}),
        ({}, {"unknown": True}),
    ],
)
def test_workout_examples_validate_the_nested_body(workout_changes, set_changes):
    workout = {
        "schema_version": 1,
        "session_id": "dashboard-11111111-1111-4111-8111-111111111111",
        "date": "2030-01-01",
        "workout_type": "strength",
        "status": "complete",
        "sets": [
            {
                "exercise": "Press",
                "equipment": "Dumbbell",
                "set_number": 1,
                "load_basis": "per_hand",
                "reps": 8,
            }
        ],
    }
    request = {**WRITE_EXAMPLE, "workout": workout}
    assert validate_examples(tool_example("record_workout", request)) == 1
    workout["sets"][0].update(set_changes)
    workout.update(workout_changes)
    with pytest.raises(ServiceError, match="invalid_request"):
        validate_examples(tool_example("record_workout", request))


TIME_FIELDS = {"measuredAtLocal": "2030-01-01T08:00:00Z", "timezone": "UTC"}
SESSION_FIELDS = {"sessionId": "synthetic-session", "date": "2030-01-01"}
LOGGER_FIELDS = {
    "measurement": {**TIME_FIELDS, "weightLb": 160},
    "intake": {
        "eventAtLocal": "2030-01-01T08:00:00Z",
        "timezone": "UTC",
        "itemName": "Synthetic meal",
        "status": "consumed",
        "category": "food",
        "source": "synthetic",
    },
    "blood-pressure": {
        **TIME_FIELDS,
        "systolic": 120,
        "diastolic": 80,
        "readingNumber": 1,
        "source": "synthetic",
    },
    "circumference": {
        **TIME_FIELDS,
        "site": "waist",
        "reading": [32],
        "measurementSite": "navel",
    },
    "workout-start": {**SESSION_FIELDS, "workoutType": "strength"},
    "workout-set": {
        **SESSION_FIELDS,
        "exercise": "Press",
        "equipment": "Dumbbell",
        "setNumber": 1,
        "loadBasis": "per_hand",
        "reps": 8,
    },
    "workout-cardio": {
        **SESSION_FIELDS,
        "activity": "Walk",
        "equipment": "Treadmill",
        "segmentNumber": 1,
    },
    "workout-finish": {"sessionId": "synthetic-session"},
}


@pytest.mark.parametrize("kind", sorted(FIELDS))
@pytest.mark.parametrize("failure", ["missing", "type", "unknown"])
def test_logger_examples_validate_each_kind_fields(kind, failure):
    fields = dict(LOGGER_FIELDS[kind])
    request = {**WRITE_EXAMPLE, "kind": kind, "sourceId": "manual", "fields": fields}
    assert validate_examples(tool_example("log_health", request)) == 1
    first = next(iter(fields))
    if failure == "missing":
        del fields[first]
    elif failure == "type":
        fields[first] = {"invalid": "nested object"}
    else:
        fields["unknownField"] = 1
    with pytest.raises(ServiceError, match="invalid_request"):
        validate_examples(tool_example("log_health", request))


@pytest.mark.parametrize("reading", [32, [], ["wide"], [-1], [True]])
def test_circumference_examples_validate_reading_items(reading):
    request = {
        **WRITE_EXAMPLE,
        "kind": "circumference",
        "sourceId": "manual",
        "fields": {**LOGGER_FIELDS["circumference"], "reading": reading},
    }
    with pytest.raises(ServiceError, match="invalid_request"):
        validate_examples(tool_example("log_health", request))


MINIMUM_GRANT = {
    "grants": ["records:read"],
    "sourceIds": [],
    "readSources": ["manual"],
    "readKinds": ["body-mass"],
}


def grant_header(value):
    return f"Use when: Synthetic contract fixture.\nGrant: {json.dumps(value)}\nTools:\n"


@pytest.mark.parametrize(
    ("key", "needed"),
    [
        ("grants", ["records:read", "records:write"]),
        ("sourceIds", ["manual"]),
        ("readSources", ["manual", "healthkit"]),
        ("readKinds", ["body-mass", "plan"]),
        ("readSources", None),
        ("readKinds", None),
    ],
)
def test_missing_policy_dimensions_require_an_exact_addition(key, needed):
    document = grant_header({**MINIMUM_GRANT, key: needed})
    with pytest.raises(AssertionError, match="Stage 7 policy missing"):
        assert_policy_contract(document, MINIMUM_GRANT)
    absent = (
        None
        if needed is None
        else [value for value in needed if value not in MINIMUM_GRANT[key]]
    )
    addition = f"Add to policy: {json.dumps({key: absent})}\n"
    assert_policy_contract(document + addition, MINIMUM_GRANT)
    with pytest.raises(AssertionError, match="missing scopes"):
        assert_policy_contract(document + "Add to policy: {}\n", MINIMUM_GRANT)
    wrong_addition = f"Add to policy: {json.dumps({key: []})}\n"
    with pytest.raises(AssertionError, match="exactly the missing"):
        assert_policy_contract(document + wrong_addition, MINIMUM_GRANT)


@pytest.mark.parametrize("key", ["readSources", "readKinds"])
def test_null_read_visibility_covers_finite_and_unrestricted_requirements(key):
    policy = {**MINIMUM_GRANT, key: None}
    assert_policy_contract(grant_header(MINIMUM_GRANT), policy)
    assert_policy_contract(grant_header(policy), policy)
    with pytest.raises(AssertionError, match="Stage 7 policy missing"):
        assert_policy_contract(grant_header(MINIMUM_GRANT), {**MINIMUM_GRANT, key: []})


@pytest.mark.parametrize(
    "value",
    [
        [],
        {},
        {**MINIMUM_GRANT, "readFields": None},
        {**MINIMUM_GRANT, "grants": ["operations:admin"]},
        {**MINIMUM_GRANT, "grants": None},
        {**MINIMUM_GRANT, "sourceIds": None},
        {**MINIMUM_GRANT, "readSources": "manual"},
        {**MINIMUM_GRANT, "readKinds": ["body-mass", "body-mass"]},
        {**MINIMUM_GRANT, "readKinds": [3]},
        {**MINIMUM_GRANT, "sourceIds": ["bad source"]},
    ],
)
def test_malformed_grant_headers_are_refused(value):
    with pytest.raises((AssertionError, ServiceError, EnvelopeError)):
        assert_policy_contract(grant_header(value), MINIMUM_GRANT)


@pytest.mark.parametrize(
    "suffix",
    [
        'Add to policy: {"readKinds":["body-mass"]}\n',
        "Add to policy: not JSON\n",
        "Add to policy: []\n",
        'Add to policy: {"unknown":[]}\n',
        'Add to policy: {"readKinds":[],"readKinds":[]}\n',
        "Add to policy: {}\nAdd to policy: {}\n",
        "Grant: {}\n",
    ],
)
def test_malformed_or_unnecessary_policy_exceptions_are_refused(suffix):
    with pytest.raises((AssertionError, ServiceError, EnvelopeError)):
        assert_policy_contract(grant_header(MINIMUM_GRANT) + suffix, MINIMUM_GRANT)
