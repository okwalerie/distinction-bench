# adr 0001: sealed release bundle is the publication authority

- status: accepted
- date: 2026-08-08

## context

the original benchmark could generate cases at task construction time, analyse loose
inspect logs, and render a site from live registries. later bundle validation still
trusted two parallel paths: mutable bundle-local registry copies and optional,
unlinked transcripts. either path could silently redefine an experiment or its raw
response while leaving self-asserted trial rows internally plausible.

## decision

one versioned release bundle is the only publication interface. the immutable
experiment authority is the exact `suites/v1.json` and packaged protocol-registry git blobs
at the source commit recorded in its `AuthorityManifest`. bundle-local copies are
evidence: validation checks their byte sha256 and git blob identity against that tree,
never against the current working tree. a self-contained copy can check the recorded
digests; sealing and repository-aware validation additionally check `git show` at the
recorded commit.

each `RunManifest` contains canonical sha256 authority digests for both registries,
the exact selected forms and cells, the selected protocol, endpoint-catalog evidence,
and the provider-neutral execution spec. this projection is part of the sole run-id
function and is reconstructed during planning, admission, and validation.

each provider call has exactly one `AttemptEvidence` row. its canonical digest covers
the completion, request id, resolved model, provider, endpoint, usage, cost, latency,
status, and a deterministic redacted raw transcript projection. `CallRecord` links to
that digest and the completed `TrialRecord` links to the final attempt. admission
requires exact evidence/call/trial/ledger closure and recomputes all run aggregates.
derived profiles and site files remain reproducible consumers. sealing lists and
hashes every file and makes the directory immutable by convention and validation.

the `release_bundle` module exposes only `create_working`, `open`, `admit_run`,
`validate`, and `seal`. renderer registries, inspect logs, and site code are inputs or
adapters, never alternative authorities.

## consequences

- public results can be traced to exact stimulus, prompt, provider, and response
  hashes.
- changing any checked-in registry, selected cell set, protocol, endpoint catalog, or
  execution specification creates a different run identity.
- redaction cannot remove or rewrite accounting and completion identity fields; it is
  confined to the declared raw-evidence projection.
- a renderer or analysis edit cannot revise an existing sealed release.
- working runs remain mutable until admitted; incomplete or provenance-deficient runs
  cannot enter a sealed bundle.
- pilot-v0 data needs an explicit importer and cannot be relabelled as v1.
