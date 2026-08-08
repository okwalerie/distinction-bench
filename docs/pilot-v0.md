# pilot-v0 archive note

all evaluation logs, notebooks, site artifacts, and result tables created before the
public v1 suite are exploratory `pilot-v0` evidence. they are motivation, not a
baseline comparable with v1.

the former suite registry is retained by git at commit
`37a488f3db5ab06c1a044085bd3f8e235f68023e`; its blob id is
`47c7f3acdde2d8eee787d26a502026bdaf6c0845`. it held 120 generated forms and
3,480 cells. 109 of the 120 forms exceeded their declared maximum depth because the
generator could add a terminal mark after its depth budget reached zero. public v1
replaces that artifact rather than modifying or comparing its results.

current code resolves the frozen public registry through
`lofbench.suites.DEFAULT_SUITE_REGISTRY`, which points at packaged authority data.

historical dialect strings and `canonical`-named fields remain unchanged in imported
pilot records so their provenance is not rewritten.
