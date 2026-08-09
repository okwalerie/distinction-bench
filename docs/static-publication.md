# static publication

The canonical public host is `https://distinction.valeriekim.ca`. The generated
site includes a matching `CNAME` file. Configure that hostname as a custom
domain for GitHub Pages, then point DNS at the Pages target shown by GitHub.

Cloudflare should remain DNS-only while the Pages custom-domain certificate is
being issued. After HTTPS works, proxying is optional. Redirect the apex or any
old gallery hostname to the canonical host with a permanent redirect, retaining
the request path and query. Do not place an authentication or analytics worker
in front of the human pilot: it is deliberately a local-only static workflow.

Pages deployment accepts the paired release assets
`distinction-bench-<tag>.tar.gz` and
`distinction-bench-<tag>.release.json`. The archive's extracted root must be a sealed
release bundle, and the sidecar must be byte-identical to its root `release.json`.
The workflow checks both conditions and validates the bundle checksums before
building; a working, edited, incomplete, or unlisted artifact is rejected.
the download page embeds selected byte-identical evidence, including
`request-started.jsonl`, and links to the paired external distribution assets. the
final manifest and archive are not copied into the sealed tree because either would
introduce a checksum/content recursion.

site generation accepts only the validated, read-only publication view returned by
`dbench.publication.open_release(...).publication()`.
