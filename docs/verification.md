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

A successful recovery during initial-runtime verification ran on 2026-10-04 from 00:06:34 to 00:08:50 UTC: readiness=31.419 seconds, all 24 real-service tests and the real CLI passed, and the test container was stopped. Post-stop state was exit=137 and OOMKilled=false; graceful shutdown was not verified. One successful restart does not prove fresh-container reliability or erase previous failures.

## Security and dependencies

Bandit 1.9.4 scanned public runtime modules and verification scripts: zero high, zero medium, and five low warnings. These were subprocess import/call cautions. Fixed argv, default shell=False, and recovery-container name restrictions were reviewed; warnings were not treated as confirmed injection vulnerabilities or described as a warning-free scan.

pip-audit 2.10.1 found no known advisory among the 29 locked packages. detect-secrets 1.5.0 scanned the public index with no-verify: fifteen candidates were twelve source/fixture/program SHA-256 fingerprints, two explicitly synthetic passwords, and one scanner-description string. Each was reviewed as a non-credential; candidates were not submitted for external verification. These dispositions apply to that scanner run, not to unidentified findings from another scanner. Docker image advisory scanning remained blocked by unavailable Docker Scout login. Python dependency checks do not substitute for image scanning.

## Not implemented or not verified

Real-model planning, HTTP OAuth, cross-process capacity, and Agent dollar-budget controls are not implemented. The public demo makes no paid inference call. Synthetic permissions do not prove login or tenant isolation. Controlled tool delay is not model performance or a production p95. Cancellation covers cooperative async work, not immediate termination of blocking SDK or remote work.

JSON and SQLite are not atomically committed together, and crashed syncing workers are not automatically reclaimed. There was no shared-database migration, production deployment, EDA simulation, hardware measurement, or replacement approval.

## MCP contract slice: current local and real-boundary verification

The isolated feature branch is based on English main 0aa2ec5. This is a separate runtime change, not part of the earlier language-only PR. The [current receipt](mcp-contracts-verification.json) fingerprints eight source/test files; the initial receipt remains historical evidence.

- Full suite: 299 discovered, 275 passed, 24 opt-in real-service tests skipped, 50.027 seconds. Eight contract tests and four mocked recovery-harness tests passed separately. Mocked recovery checks prove CLI/report behavior, not database readiness.
- Six isolated guard mutations each passed at baseline, failed with a relevant assertion when the production guard was removed, and passed after restoration. Tests and expected values were unchanged within each trial.
- A fresh stdlib-only offline CLI run passed: explicit sync retry became synced, old snapshots remained unchanged, six tasks completed and four returned busy, peak active=2, active afterward=0. The graph and planner on this path are synthetic.
- Earlier contract recovery attempts failed before suites. The 02:02:29-02:10:03 UTC attempt exceeded the fixed 420-second readiness wait and stopped its container. Those reports remain preserved; increasing a wait limit is not a startup fix.
- Explicit recovery with a recorded 900-second readiness limit ran from 02:13:59 to 02:21:08 UTC. Readiness took 307.146 seconds. All 24 separately enabled real-service tests passed: four sync recovery tests, twelve Neo4j tests, eight official SDK-to-stdio MCP-to-Neo4j tests. Their durations were 17.818, 13.287 and 38.363 seconds; these are single-run suite times, not latency percentiles.
- MCP negotiated protocol 2026-07-28. Tests exercised discovery and output schemas, all seven stages and legacy data, invalid inputs, unknown-tool protocol errors, absence, rejected credentials, unavailable DB, instruction-like evidence returned as data, and complete durable-state fingerprints unchanged by read calls. The scripted controller and public CLI also traversed real MCP and Neo4j; there was no real model request.
- The real graph-storage demo confirmed failed-after-commit state, explicit retry/readback recovery and old snapshots unchanged. Its concurrency path still uses controlled fixture delays and synthetic identities; it does not establish concurrent real-model/MCP capacity.
- Cleanup acknowledged stop; independent post-inspection was exited, OOMKilled=false, exit=137. Graceful shutdown and fresh-container reliability remain unverified. No container or volume was deleted.
- Bandit 1.9.4 scanned 28 runtime/script files: zero high/medium, five reviewed low subprocess warnings. pip-audit 2.10.1 found no known advisory among 29 unchanged locked packages. The no-verify secret scan includes new files and the receipt; source fingerprints and synthetic credentials are reviewed separately from the three unidentified GitGuardian alerts, which remain unresolved without exact incident locations. Docker image advisory scanning remains blocked.

The [MCP contract](mcp-contracts.md), safe error classification and handler-only timing are implemented and verified on this local isolated path. Real-model planning, Agent dollar reservations, bounded MCP waiters, global capacity and authenticated remote access remain unfinished. No paid inference, main merge or deployment occurred for this slice.
