# Repository and release responsibilities

| Repository | Output | Required immutable input | Gate |
| --- | --- | --- | --- |
| `cesaregarza/health-buddy` | Canonical operations/backend/dashboard, extensions, CLI, Compose, installer, shared tools, agent packages, site source | Accepted contract predecessor, later exact source/image/package artifacts | Synthetic tests; independent review; later host/agent/restore qualification |
| `cesaregarza/cesar-health-sync` | Existing read-only iPhone companion and negotiated pairing/sync | Compatible API/pairing/phone protocol; reuse existing PR #2 reliability work | Linux contract, macOS build, physical device and Apple gates are separate |
| `GarzAICluster` | Public static setup/docs/privacy site only | Real immutable site artifact, selected public hostname | GitOps review and authorized deployment; no private health stores |
| Private `workout-log` | Allowlisted source input and reversible personal migration source | Explicit safe source inventory and synthetic replacements | No private records/defaults/history publication; no writes to source during extraction |

This slice introduces no workflow file, runtime artifact, deployment, personal
migration or remote push. Repository initialization has clean history. Existing
personal deployment and source history are outside this repository.

## Implementation graph

This is dependency order, not permission to run every ready ticket concurrently.

| Ticket | Immediate predecessors | Work |
| --- | --- | --- |
| CES-1063 | none | Contract and fixtures (this slice) |
| CES-1064 | 1063 | Clean MIT allowlisted extraction |
| CES-1065 | 1064 | Portable config/useful empty dashboard; optional Jev |
| CES-1066 | 1065 | Canonical validated API and transaction semantics |
| CES-1067 | 1066 | Application authorization, pairing and revocation |
| CES-1085 | 1066, 1067 | Supported extensions and persistent personal workspace |
| CES-1068 | 1066, 1067, 1085 | ARM64/x86-64 runtime packaging |
| CES-1069 | 1068, 1085 | Operator status/doctor |
| CES-1070 | 1068, 1085 | Consistent encrypted backup/restore |
| CES-1071 | 1068, 1070, 1085 | Upgrade/customization preservation |
| CES-1072 | 1066, 1067, 1085 | Shared agent operations/tools |
| CES-1086 | 1068, 1085 | Discoverable development guide/scaffolds/tests |
| CES-1073, CES-1074 | 1072, 1086 | Codex and Claude Code integration |
| CES-1075 | 1067 | Generic phone pairing and receiver-scoped state |
| CES-1076 | 1070, 1075 | HealthKit recovery/replay/history |
| CES-1077 | 1069, 1071, 1073, 1074 | Resumable private-host installer |
| CES-1078 | 1063, 1064 | Product site and ongoing customization guides |
| CES-1079 | 1078 | Site GitOps (deployment separately authorized) |
| CES-1080 | 879, 1076, 1079 | External TestFlight operator gate |
| CES-1081 | 1070, 1071 | Reversible migration canary (cutover separate) |
| CES-1082 | 1075, 1076, 1077, 1079 | Independent security review |
| CES-1083 | 1076, 1077, 1079, 1081, 1082 | Real everyday/customization/recovery qualification |
| CES-1084 | 1080, 1083 | Outside pilot and public beta operator gate |

CES-879 remains **In Verification**. Source completion never substitutes for
signed physical-device acceptance. Inspect the current Linear dependency graph
before dispatch; this document is a frozen implementation contract, not a
claim that those tickets are complete.

## Verification and publication order

1. Prepare local isolated clean commits in dependency order. Later slices name
   their exact accepted predecessor; accepted source is not a release.
2. Run the checks in [AGENTS.md](../AGENTS.md#checks-and-publication) on
   each frozen commit. Receipts name exact SHA, commands, versions, outcomes,
   logs and unverified gates.
3. Review acceptance criteria and raw evidence. Fix and retest changed evidence.
   Stage PR title/body before remote publication.
4. Before any push or PR, inspect actual workflow triggers and repository
   rules. Drafts can trigger Actions. Do not disable unrelated workflows or
   invoke Apple/paid builds. Push, open or merge PRs only after coordinator
   review with operator approval; validate remote head/checks. Stage
   the new repository privately; public beta/source release qualification is a
   separate CES-1084 operator gate, not implied by MIT licensing.
5. After successful PR publication and required verification, add its URL and
   concise verification summary to Linear, then move completed implementation
   to In Review. Unpublished or partially verified work stays out of In Review.

## Source scope

This repository contains the **v1 contract** and a clean source extraction of
the dashboard, HealthKit ingest, manual operations and optional SleepIQ source.
The source bundle now supports a private local first run, manual logging,
dashboard and context packs with every optional integration disabled. Canonical
v1 operations now join UI, CLI and scoped extension clients behind one durable
coordinator. Owner sessions, scoped agent grants and upload-only device pairing
share a durable authorization authority. Reviewed personal metrics/views and
connector jobs live outside the replaceable source, with maintained synthetic
examples and durable event retries. Source packaging now includes immutable
input locks, a Docker archive/Compose runtime and controlled native architecture
qualification commands. Actual image/build receipts and operator release
qualification remain separate; installer and live-agent qualification remain
later tickets. Passing source checks does not establish release qualification.

## Activation and release gates

Publishing or merging source is distinct from activation. Runtime/installer/
agent/phone compatibility must be checked against real artifacts. Publish the
site only with verified release links; no placeholder digest, hostname or
version becomes a release input. Deployment, production migration/cutover,
signing spend, Apple distribution, invitations and external release remain
explicitly gated operator actions.

Install qualification includes clean x86-64 Linux and a physical ARM64 Pi,
reachability/preflight, protected static/read/write access, retries/conflicts,
optional-source and no-AI operation, full backup/restore and secure phone
recovery. Agent qualification includes fresh discovery, useful creation,
different-agent maintenance, upgrade and clean-host restore of user additions.
External iPhone users must install without Xcode or their own developer account;
Apple approval, hosted privacy URL and physical testing remain separate gates.
