# adr 0001: sealed release bundle is the publication authority

- status: accepted
- date: 2026-08-08

## context

the original benchmark could generate cases at task construction time, analyse loose
inspect logs, and render a site from live registries. those parallel paths could
silently disagree about which forms, dialects, prompts, or model configurations a
number described.

## decision

one versioned release bundle is the only publication interface. its suite and
protocols declare what was tested; admitted run and trial records declare how it was
tested; derived profiles and site files are reproducible consumers. sealing lists
and hashes every file and makes the directory immutable by convention and validation.

the `release_bundle` module exposes only `create_working`, `open`, `admit_run`,
`validate`, and `seal`. renderer registries, inspect logs, and site code are inputs or
adapters, never alternative authorities.

## consequences

- public results can be traced to exact stimulus, prompt, provider, and response
  hashes.
- a renderer or analysis edit cannot revise an existing sealed release.
- working runs remain mutable until admitted; incomplete or provenance-deficient runs
  cannot enter a sealed bundle.
- pilot-v0 data needs an explicit importer and cannot be relabelled as v1.
