# static publication

The canonical public host is `https://distinction.valeriekim.ca`. The generated
site includes a matching `CNAME` file. Configure that hostname as a custom
domain for GitHub Pages, then point DNS at the Pages target shown by GitHub.

Cloudflare should remain DNS-only while the Pages custom-domain certificate is
being issued. After HTTPS works, proxying is optional. Redirect the apex or any
old gallery hostname to the canonical host with a permanent redirect, retaining
the request path and query. Do not place an authentication or analytics worker
in front of the human pilot: it is deliberately a local-only static workflow.

Pages deployment accepts only a release asset named
`distinction-bench-<tag>.tar.gz`. Its extracted root must be a sealed release
bundle. The workflow validates the bundle checksums before building; a working,
edited, incomplete, or unlisted artifact is rejected.

site generation accepts only the validated, read-only publication view returned by
`dbench.publication.open_release(...).publication()`.
