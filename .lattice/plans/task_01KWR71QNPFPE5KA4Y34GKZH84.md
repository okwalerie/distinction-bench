# DB-10: Suite-driven evaluation: make inspect tasks consume suites/v1.json

Discovered by the DB-8 implementer's dry runs. The frozen suite (suites/v1.json, 120 forms by 29 dialects, payload hashes, rerun gate) has no eval-time consumer. single_lof_task and composite_lof_task generate forms live from a seed, so a run today produces different forms than the frozen table (verified: live lof_001 differs from suite v1's lof_001) and stamps suite_version adhoc. Until fixed, every run is pilot-grade and the headline sensitivity score cannot claim suite v1.

Scope: add a suite parameter to the tasks (for example -T suite=v1 -T dialect=parens.noisy-v1) that loads the frozen id-to-form table and the named dialect spec through the suite loader (threading suite_version into DialectSpec, which already exists in src/lofbench/suites.py), verifies payload hashes before any model call (the rerun gate), and stamps true suite provenance into sample metadata. The dataset factory path must use the frozen forms, not the generator. Also decide and record the flagship core subset (a fixed form_id list per docs/measurement-design.md section 7) inside the suite file, which the DB-8 release skill needs.

The bench-release skill (DB-8) hard-refuses v1 releases until this lands.
