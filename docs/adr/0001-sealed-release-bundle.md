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

openrouter model identity has two distinct fields. `requested_model_id` is the exact
public alias sent to chat completions and expected in the chat response.
`resolved_model_id` is the immutable `canonical_slug` returned for that exact alias by
the authenticated `/api/v1/models/user` catalog and expected as the generation
accounting `model`/provider permaslug. planning retains the complete authenticated
user-model row plus timestamped retrieval metadata, raw-response sha256, and byte
length. the endpoint and zdr intersection remains an exact join on requested alias,
endpoint tag, and provider name. missing, duplicate, empty, or mismatched canonical
catalog identity fails before planning; alias and permaslug are never required to be
the same string.

each provider call has one append-only `AttemptEvidence` revision chain. every row retains an immutable,
deterministically labelled `ProviderEvidenceEnvelope`: the exact raw chat response,
every exact raw generation-accounting lookup response (including failed polls), http
status, raw bytes and parse outcome, local request/response timestamps, and only
strictly canonical `content-type` and `x-generation-id` response metadata. generation
ids use a bounded visible-ascii token grammar; content types use an exact known-media
allowlist. values containing credential markers, unsafe punctuation, whitespace, or
control characters are discarded before persistence and rejected by publication
validation. the generation id is an accounting lookup key when an error body has no
id. authorization, cookie, proxy-authentication, api-key, and unknown response headers
are never retained.
one pure projector derives completion, request id, resolved model, provider, endpoint,
usage, cost, latency, status, and error. orchestration consumes only that projection;
admission reruns it and compares every call/trial field. incomplete accounting remains
`accounting_unknown`: no zero-cost settlement is invented, the conservative reservation
stays outstanding, and resume performs only bounded generation-accounting gets. a
successful lookup appends a digest-linked evidence revision and call revision without
resending inference. the direct openrouter adapter
uses the provider's chat-completions endpoint rather than treating an opaque inspect
sample dump as raw authority. missing generation evidence fails closed.

run execution is an idempotent reconcile-before-act state machine. reservation,
request-start intent, evidence, call, settlement, trial, and run-manifest writes are
separate durable transitions. each start or resume replays request intents, evidence
revision chains, effective calls, ledger, and trials; it completes every derivable
transition before reserving another attempt. after reservation, a `RequestStartedRecord`
content-binds the deterministic call id and exact provider-neutral request intent and
is fsynced immediately before the executor is invoked. a reservation without this
record is safe to execute. a request-start record without evidence is an ambiguous
provider outcome: the run is quarantined, its reservation stays outstanding, and
resume never invokes that call again. this trades possible incompletion for at-most-one
provider post per call id across process-kill windows. later durable append boundaries
converge by replay without another inference. provider errors settle the exact
generation-reported cost and tokens but never create a trial; if that accounting cannot
be proven, the reservation remains outstanding.

one advisory linux file lock makes each run a single-writer module. execution acquires
the lock nonblockingly before reconciliation and holds it across reservation,
request-start, provider execution, evidence, settlement, trial, and the final atomic run
manifest. another executor receives an explicit already-running error and cannot inspect
partial append state or post. status readers may read the last atomically replaced run
manifest while execution continues. different run locks do not partition accounting:
every reservation and settlement still crosses the release-wide exclusive ledger lock,
so global and cohort caps serialize across runs.

durable jsonl appends retry interrupted and short writes, fsync the complete file, and
fsync its parent directory when the file is first created. lock-file creation follows
the same file-then-parent ordering. atomic json manifests fsync their complete temporary
payload, replace the destination, then fsync the parent directory. these guarantees
assume a healthy local filesystem implementing the usual linux `fsync`, atomic-rename,
and advisory-lock semantics.

`CallRecord` links the envelope digest and the completed `TrialRecord` links the final
attempt. trial error semantics are replayed from that evidence. admission requires
exact request-start/evidence/call/trial/ledger closure and recomputes all run aggregates. derived
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

the live `v1.0.0-sample.1` predecessor created before this identity rule is handled by
one explicit working-state salvage operation, not by accepting its legacy identity in
normal validators. the operation requires the exact unsealed four-run predecessor and
the bounded canonical-identity repair lineage with no unrelated source or registry
changes, proves the sole retained request, evidence
revision chain, legacy accounting-unknown call, and unsettled reservation, then rekeys
all four run/trial/call/request/ledger identities under current authority. it preserves
every provider-evidence envelope byte-for-byte, reprojects and scores the first
completion, settles its exact retained cost, records old/new ids and digests in a
migration audit, and keeps the predecessor state as a recoverable directory backup.
the migrated sample therefore has one of twenty attempts complete and nineteen—not
twenty—remaining. partially swapped or forged state fails closed under ordinary
repository, release, run, and ledger identities.

every state-root writer takes one stable lifecycle flock outside the replaceable
state directory. `plan` takes it before checking or creating release/state paths and
holds it through every release, run, and cost-sheet write. `run`, `resume`, and
`probe` hold it from state/release preflight
through provider execution and the final run manifest; salvage holds the same lock
through authenticated catalog selection, predecessor validation, reconciliation, and
publication. narrower locks are always acquired root → run → ledger. before its first
rename, salvage fsyncs the exact predecessor release bytes and migration audit into a
recovery directory and records a fsynced phase journal. a later invocation either
rolls every noncommitted phase back to the verified predecessor or finalizes a
verified committed phase before reading credentials or querying the catalog. the
migration additionally requires the exact tracked live
identity event—id, actor, type, body, and body digest—from the current source commit;
an arbitrary or missing event id has no authority.
each migration rename fsyncs every distinct source and destination parent directory,
including rollback renames across the recovery directory boundary. committed recovery
first loads both predecessor and candidate `AuthorityManifest` values, binds their
canonical digests through the journal and audit, and calls `verify_authority_copies`
against the frozen registry bytes and each recorded git tree when the repository is
available. only then may it parse registry content. every old/new `RunAuthority` is
rederived from those verified bytes, selected cells and protocol, catalog, and
execution spec before recovery re-closes the exact protocol-matched positional maps,
and the one-attempt/nineteen-remaining state before removing the journal. retained raw
evidence is reprojected with the current projector through the same reconciliation
derivation used by ordinary execution; recovery requires exact request, call, scored
trial, reserve/settle pair, and run aggregates rather than trusting recorded hashes or
set membership. the verified bundled `protocols.json` bytes are parsed once into the
`ProtocolSpec` values supplied to frozen-task construction, staging, orchestration,
scoring, and committed recovery. the running source tree's module-global protocol
registry is not an authority for migration or recovery.

## consequences

- public results can be traced to exact stimulus, prompt, provider, and response
  hashes.
- changing any checked-in registry, selected cell set, protocol, endpoint catalog, or
  execution specification creates a different run identity.
- changing either the requested public alias or its authenticated canonical slug
  creates a different run identity.
- raw provider response bytes are retained exactly (base64 encoded with a verified text
  and json-parse projection); secrets belong to
  request headers and never enter the envelope.
- a renderer or analysis edit cannot revise an existing sealed release.
- working runs remain mutable until admitted; incomplete or provenance-deficient runs
  cannot enter a sealed bundle.
- pilot-v0 data needs an explicit importer and cannot be relabelled as v1.
