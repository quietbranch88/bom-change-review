# Verification record and limitations

The public demo verified on 2026-10-04 uses synthetic review receipts and wiring declarations with public-source specifications. These are development fixtures, not a real BOM, hardware sign-off, or independent model evaluation.

## Executed results for the initial public runtime

These results describe runtime revision 2ce816b162e1a766411d2a470b3a1238b8d37281. The subsequent article-removal commit c13c488 changed no runtime code. English documentation changes do not rerun or expand the historical verification claim.

- Windows, Python 3.13.14; clean public files exported from the Git index, without private .spec files or existing output.
- `python -m unittest discover -s tests -v`: 287 discovered, 263 passed, 24 opt-in real-service tests skipped, 56.968 seconds. This is not 287 real-service tests.
- Separate isolated real-engine runs passed: four SQLite-to-Neo4j recovery tests, twelve Neo4j tests, and eight official SDK-to-stdio MCP-to-Neo4j tests. The latest suite durations were 11.764, 11.777, and 35.496 seconds.
- Neo4j Community 5.26.29, digest-pinned image, 2 CPU/2 GiB limit, 512 MiB heap/256 MiB page cache; MCP SDK 2.2.0, negotiated protocol 2026-07-28. This is a local synthetic environment, not production.
- Real-mode public CLI readback confirmed: after an injected lost acknowledgment the ledger was failed while graph data existed; explicit retry became synced; old snapshots remained unchanged. Tests also compared complete nodes and relationships across retry.
- `python -S system_demo.py --simulate-review --out output/fresh-demo` passed in a clean directory without site-packages or keys. Ten synthetic identities yielded six completed and four busy tasks, peak active=2, and zero active/queued/outstanding work afterward.
- The [guard-mutation harness](../scripts/verify_demo_guards.py) produced relevant AssertionErrors for all three production mutations. Baseline and restored tests passed; expected values were not changed.
- The original [architecture SVG](architecture.svg) was inspected in Chrome for text, boundaries, and arrows. Mermaid node and relationship names were compared with the implementation. English rendering verification is separate from the historical screenshot.

Program fingerprints and security summaries are in the [machine-readable receipt](verification.json). Raw local reports are not published because they can contain operator records and local paths.

## English documentation checks: 2026-10-04

The documentation-only branch was checked separately: 24 tracked Markdown/SVG files contain no Han text; 59 local links and one translated heading anchor resolve; the SVG parses as XML; the exact diff contains only documentation and diagram changes. Runtime, dependency locks, tests, source fixtures, and generated receipts are unchanged.

Chrome rendered the local English SVG. All 34 text labels fit the viewBox and their applicable boxes with padding; visual inspection checked labels, arrows, and implemented/pending boundaries. This verifies the local static diagram, not GitHub's remote rendering or an application flow. No runtime, database, or paid-model test was rerun for this language-only change.

## Preserved failures

The first new-container startup exceeded 420 seconds before reaching any suite. A stop timeout also exposed a harness defect that prevented saving its failed report. A failing test drove a correction so cleanup failure still preserves the report; cold-start reliability was not declared fixed.

Later explicit restarts of the same isolated test container included incomplete attempts. They also exposed a JSON-null versus Neo4j `properties(n)` mismatch that incorrectly marked a valid unknown case as sync failure. A fixed-oracle fail-to-pass test corrected projection normalization without weakening complete comparison. Another fail-to-pass fix corrected CLI creation when a fresh checkout lacked an output parent directory.

The latest successful explicit recovery ran on 2026-10-04 from 00:06:34 to 00:08:50 UTC: readiness=31.419 seconds, all 24 real-service tests and the real CLI passed, and the test container was stopped. Post-stop state was exit=137 and OOMKilled=false; graceful shutdown was not verified. One successful restart does not prove fresh-container reliability or erase previous failures.

## Security and dependencies

Bandit 1.9.4 scanned public runtime modules and verification scripts: zero high, zero medium, and five low warnings. These were subprocess import/call cautions. Fixed argv, default shell=False, and recovery-container name restrictions were reviewed; warnings were not treated as confirmed injection vulnerabilities or described as a warning-free scan.

pip-audit 2.10.1 found no known advisory among the 29 locked packages. detect-secrets 1.5.0 scanned the public index with no-verify: fifteen candidates were twelve source/fixture/program SHA-256 fingerprints, two explicitly synthetic passwords, and one scanner-description string. Each was reviewed as a non-credential; candidates were not submitted for external verification. These dispositions apply to that scanner run, not to unidentified findings from another scanner. Docker image advisory scanning remained blocked by unavailable Docker Scout login. Python dependency checks do not substitute for image scanning.

## Not implemented or not verified

Real-model planning, HTTP OAuth, cross-process capacity, and Agent dollar-budget controls are not implemented. The public demo makes no paid inference call. Synthetic permissions do not prove login or tenant isolation. Controlled tool delay is not model performance or a production p95. Cancellation covers cooperative async work, not immediate termination of blocking SDK or remote work.

JSON and SQLite are not atomically committed together, and crashed syncing workers are not automatically reclaimed. There was no shared-database migration, production deployment, EDA simulation, hardware measurement, or replacement approval.
