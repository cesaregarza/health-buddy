Use when: The owner asks whether Health Buddy is connected or what was logged recently.
Grant: {"grants":["records:read"],"sourceIds":[],"readSources":["manual"],"readKinds":["body-mass"]}
Tools: sync_status, get_context

Use the existing owner-selected policy; never edit it or request wider grants
as part of this read. The header describes the minimum manual body-mass read.
`profile` and `weight` are context scopes, not policy record kinds. Restricted
profile fields remain unavailable under the current policy.

1. After the router's session checks, call `sync_status` with `{}`.
2. If the current tool is available, call `get_context` with this request:

```json health-buddy:get_context
{"scopes":["profile","weight"],"days":7,"limit":20}
```

Read `result.data.state` and `result.data.truncated` from the status reply.
For each entry in `result.data.sources`, read `availability`, `freshness`
and `missingness`. The context reply contains
`result.data.text`, not a records array; use its supplied values, units, dates
and limitations. This covers profile/weight in the requested window, not every
record kind or a complete activity history.

Return exactly these two lines, replacing placeholders with observed evidence:

```text
Health Buddy: <connection state>; stale=<yes/no/unknown>; truncated=<yes/no/unknown>.
Latest records (profile/weight, 7 days): <latest supplied values, units and dates, or the missingness/restriction reason>.
```

A successful reply establishes that tool's connection; a missing tool or failed
call needs its exact error code and makes the affected data unavailable. Do not
claim both calls succeeded when only one did. Mark stale=yes when the status
state or source freshness says stale; use no only when freshness is explicitly
current/fresh, otherwise unknown. Mark truncated=yes if the status flag or
context limitation says so; an absent flag is unknown, never assumed false.
If the calls disagree or show different revisions, retain that limitation.
Do not invent latest dates, turn no returned rows into zero, or describe
restricted, disabled, unavailable or stale data as a complete recent history.
