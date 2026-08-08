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

the renderer registry is a development interface. `suites/v1.json` freezes the
public form and dialect identities. a sealed release bundle then admits exact runs
against that suite. analysis and the public site consume the bundle and must not
reach around it to live registries or loose logs.

pilot-v0 import code retains historical field names such as `canonical`; new public
interfaces use the vocabulary above.
