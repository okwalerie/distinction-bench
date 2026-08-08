# distinction-bench domain context

distinction-bench measures whether a model gives the same correct Laws of Form
normal value when the same containment tree is rendered through different dialects.

## ubiquitous language

- **abstract form**: the containment tree under test.
- **reference transcription**: a neutral parentheses serialization of that tree. it
  is an engineering interchange format, not the benchmark's privileged condition.
- **normal value**: `marked` or `unmarked`, obtained by reducing a ground form.
- **dialect**: a deterministic convention for rendering a containment tree.
- **stimulus**: one abstract form rendered in one dialect.
- **protocol**: the instructions, response schema, and scorer rules for a trial.
- **trial**: one model response to one stimulus.
- **run**: a declared collection of trials under one model configuration and
  execution surface.
- **release**: an admitted, checksummed collection of a suite, protocols, runs, and
  analyses.

## boundaries

the renderer registry is a development interface. the exact git blobs at the
release's recorded source commit for the packaged `registries/suites-v1.json` and
`registries/protocols-v1.json` freeze
the experiment definitions. copied registry files in a release are evidence copies,
never a second authority. every run identity content-binds those registry bytes, its
selected forms and cells, its selected protocol, and its endpoint/execution evidence.

a sealed release bundle remains the sole publication interface. it admits one typed,
digest-linked request-start intent and attempt-evidence record for every provider call;
calls link to trials, and request intents, evidence, calls, trials, the release ledger,
and derived aggregates must close exactly.
analysis and the public site consume this validated projection and must not reach
around it to mutable registries, self-asserted rows, or loose logs.

pilot-v0 import code retains historical field names such as `canonical`; new public
interfaces use the vocabulary above.
