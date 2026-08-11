# static publication

the canonical public host is `https://distinction.valeriekim.ca`. the generated
site includes a matching `CNAME` file. configure that hostname as a custom
domain for github pages, then point dns at the pages target shown by github.

cloudflare should remain dns-only while the pages custom-domain certificate is
being issued. after https works, proxying is optional. redirect the apex or any
old gallery hostname to the canonical host with a permanent redirect, retaining
the request path and query. do not place authentication or analytics in front
of the human pilot: it is deliberately a local-only static workflow.

## publication boundary

the complete sealed bundle is the local audit authority. it contains the raw
execution, accounting, provider, and provenance records needed for independent
review, but it is not a public web artifact and is never uploaded by the pages
workflow.

the public artifact is the deterministic `site/` projection produced by
`lofsite.build.build_site` from the validated, read-only publication view. the
builder has one output contract: explanatory pages, all 29 dialect detail pages,
the frozen spatial images those pages use, and these exact public downloads:

- `suite.json`
- `protocols.json`
- `human-trial.schema.json`
- `profiles.parquet`
- `effects.parquet`

the output verifier requires exact path membership, byte-identical public
downloads, all frozen spatial hashes, resolvable local links, no symlinks or
unexpected directories, no private execution identifiers or distribution
links, and a clean publication secret scan. site generation fails closed when
any condition is not met.

the results page publishes only recomputed aggregate profiles, controlled
effects, accuracy matrices, and aggregate resource use. individual model
exchanges, execution identifiers, provider envelopes, routing/catalog details,
and accounting events remain in the local authority.

## immutable reissue

an older sealed sample is reissued, never edited in place. from a clean reviewed
commit, the operator supplies its immutable directory, matching external
archive, absent sibling target, and complete state directories:

```text
python -m dbench reissue-sealed-release \
  --source-release <sealed-directory> \
  --source-archive <sealed-archive> \
  --target-release <absent-sibling-directory> \
  --state-root <complete-state-directory>
```

this command authenticates the old checksums, typed tree, archive, authority,
and evidence/accounting core. it performs no planning, execution, provider
request, credential load, or network access. `dbench prepare` builds and verifies
the sanitized site in the new sibling; ordinary validation and sealing retain
the complete sibling locally. only its reviewed `site/` subtree crosses the
public boundary.

## site-only pages snapshot

github pages deploys only from the orphan `gh-pages` branch. that branch contains:

- `site/`
- `.github/workflows/pages.yml`
- `SHA256SUMS`, covering every regular file below `site/`
- `provenance.json`, containing only the release id, sealed-manifest sha256,
  renderer commit, and build timestamp

the push-triggered workflow verifies the branch path closure, provenance shape,
inventory closure, and every digest before uploading only `site/`. it does not
download releases, install the benchmark, receive credentials, or regenerate
the site. construct the snapshot in an isolated temporary worktree, run the
same source-side public-site verifier before copying it, generate the sorted
inventory, and inspect the staged diff before pushing.

the sample tag may identify the local authority commit, but its github release
has no evidence assets. the draft feature pull request remains unmerged during
site deployment.
