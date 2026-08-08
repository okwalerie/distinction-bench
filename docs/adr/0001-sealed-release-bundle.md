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
experiment authority is the exact packaged
`src/lofbench/registries/suites-v1.json` and
`src/lofbench/registries/protocols-v1.json` git blobs at the source commit recorded
in its `AuthorityManifest`. bundle-local copies are
evidence: validation checks their byte sha256 and git blob identity against that tree,
never against the current working tree. a self-contained copy can check the recorded
digests; sealing and repository-aware validation additionally check `git show` at the
recorded commit.

each `RunManifest` contains canonical sha256 authority digests for both registries,
the exact selected forms and cells, the selected protocol, endpoint-catalog evidence,
and the provider-neutral execution spec. this projection is part of the sole run-id
function and is reconstructed during planning, admission, and validation.

each provider call has one append-only `AttemptEvidence` revision chain. every row retains an immutable,
deterministically labelled `ProviderEvidenceEnvelope`: the exact raw chat response,
every exact raw generation-accounting lookup response (including failed polls), http
status and headers, raw bytes and parse outcome, and local request/response timestamps.
one pure projector derives completion, request id, resolved model, provider, endpoint,
usage, cost, latency, status, and error. orchestration consumes only that projection;
admission reruns it and compares every call/trial field. incomplete accounting remains
`accounting_unknown`: no zero-cost settlement is invented, the conservative reservation
stays outstanding, and resume performs only bounded generation-accounting gets. a
successful lookup appends a digest-linked evidence revision and call revision without
resending inference. the direct openrouter adapter
uses the provider's chat-completions endpoint rather than treating an opaque inspect
sample dump as raw authority. missing generation evidence fails closed.

`CallRecord` links the envelope digest and the completed `TrialRecord` links the final
attempt. trial error semantics are replayed from that evidence. admission requires
exact evidence/call/trial/ledger closure and recomputes all run aggregates. derived
profiles and site files remain reproducible consumers. sealing lists and hashes every
file and makes the directory immutable by convention and validation.

the provider-neutral `release_bundle` module's mutation interface is `create_working`, `admit_run`,
`materialize_stimuli`, and `seal`; `open`, `validate`, and `validate_planned_run`
enforce the same authority before operations. dbench injects the openrouter projector
and sample-release policy through `dbench.publication.open_release`; generic core does
not interpret provider catalogs or sample protocol ids. publication consumers use
`publication`, a validated read-only view containing the frozen suite/protocols,
admitted records, and recomputed metrics. `lofsite.build_site` accepts only that view.
renderer registries, provider adapters, inspect logs, cli policy, and site code are
inputs or consumers, never alternative authorities.

## consequences

- public results can be traced to exact stimulus, prompt, provider, and response
  hashes.
- changing any checked-in registry, selected cell set, protocol, endpoint catalog, or
  execution specification creates a different run identity.
- raw provider response bytes are retained exactly (base64 encoded with a verified text
  and json-parse projection); secrets belong to
  request headers and never enter the envelope.
- a renderer or analysis edit cannot revise an existing sealed release.
- working runs remain mutable until admitted; incomplete or provenance-deficient runs
  cannot enter a sealed bundle.
- pilot-v0 data needs an explicit importer and cannot be relabelled as v1.
