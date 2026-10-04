# Lesson 10: connect evidence and gaps in Neo4j

On 2026-09-28, the first local import/query slice ran against Neo4j Community 5.26.29. No model call, cloud deployment, or replacement approval occurred.

## What the graph adds

JSON describes one case. A graph connects cases, candidates, specifications, and document versions so queries can trace sources or identify cases potentially affected by a document.

The actual projection contained 10 nodes and 13 relationships—not 10 hardware components: one case snapshot, one requirement, two components, three specifications, one document version, two gaps. No components were invented to meet a target count.

```text
E01 case snapshot
  ├─ ABOUT_ORIGINAL → LT8301ESS (manufacturer not guessed)
  ├─ HAS_REQUIREMENT → board input: 14–30 V
  ├─ CONSIDERS → LM5155
  │               └─ HAS_SPEC → three conditional specifications
  │                                └─ EXTRACTED_FROM → SNVSB75E / Rev E / PDF hash
  └─ HAS_GAP → fact review missing / application conditions missing
                  └─ BLOCKS → input-range assessment
```

These are traceability relationships, not an LT8301ESS-to-LM5155 replacement conclusion.

## Who converts JSON to a graph?

1. `local_review` replays decisions to obtain accepted part identity and input bounds.
2. `range_assessment` recomputes the saved, still-unknown result and checks source currency.
3. [neo4j_graph.py](../neo4j_graph.py) deterministically maps fields and IDs; no additional LLM is needed.
4. A parameterized Cypher statement writes all nodes/relationships in one transaction.
5. A separate process queries three specifications, two gaps, and pending status.

This first slice supports pending facts with no application context. Reviewed candidates, other source manufacturers, or unsupported condition structures are refused, not guessed. JSON is retained as source; the graph is a historical projection, not remote-document synchronization.

## What was read back?

| Fact ID | Range | Classification / condition | Review |
|---|---|---|---|
| F-BIAS-INTERNAL | 3.5–45 V | recommended operating; internal VCC regulator; Tj −40–125°C | pending |
| F-BIAS-TIED | 2.97–16 V | recommended operating; VCC tied to BIAS; same Tj bounds | pending |
| F-BIAS-ABS | −0.3–50 V | absolute maximum; BIAS relative to AGND | pending |

This verifies import/readback of saved drafts, not another datasheet review. Absolute maximum is not normal-operation evidence. The actual case retains two gaps and unassessed engineering checks.

Local ignored files under `output/bom-neo4j-test-6b3ea866a351/` contain import/readback/verification reports. Import and readback matched, and input hashes stayed unchanged. They are not public GitHub evidence artifacts.

## Why bind specification versions?

A component may later gain new facts. Traversing case → component → every specification would mix new facts into old cases.

The `CONSIDERS` relationship retains the case's `facts_sha256`; queries filter that batch:

```cypher
MATCH (c:CaseSnapshot {id: $snapshot_id})-[cc:CONSIDERS]->(p:Component)
MATCH (p)-[:HAS_SPEC]->(s:Specification)
WHERE s.facts_sha256 = cc.facts_sha256
RETURN p.model, s.fact_id, s.min, s.max, s.review_status
ORDER BY s.id
```

Reachable evidence is not necessarily applicable evidence.

## Reproduction and resource limits

Without starting a database:

```powershell
python neo4j_graph.py plan
```

Defaults depend on historical local files. Other checkouts must provide their own requirements/facts/assessment paths.

With Docker running, port 18747 available, and the pinned image present:

```powershell
python scripts/verify_neo4j.py --run-isolated --demo
```

Image: `neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e`. If absent, obtain `neo4j:5.26.29-community` and verify its digest; do not use floating latest.

Each run creates an isolated container bound to `127.0.0.1:18747`, limited to 2 CPU / 2 GiB RAM, heap 512m, page cache 256m. These are settings/limits, not measured minimum requirements or capacity guarantees. Random per-run Basic-auth credentials are passed locally, excluded from reports, and unrelated to OpenRouter. Docker administrators can inspect container settings; this is not a secret vault.

The harness stops only its own container; it does not stop Docker Desktop or delete containers/volumes. Repeated runs accumulate resources; cleanup requires a separate decision. At that checkpoint no Neo4j service or browser UI was left running.

No model/cloud bill was incurred, but local disk, memory, and download traffic were used. Docker Scout required login, so its image-advisory scan was incomplete—not passed.

## Evidence boundaries

- 13 mapper/transport/offline CLI tests passed. Mocked transport proves local handling only.
- Seven real-Neo4j tests passed: separate CLI readback, idempotent reimport, rollback, uniqueness, old/new version isolation, injection text treated as data, and wrong-password denial.
- Default suite: 196 discovered, 189 passed, seven live tests skipped. Those seven were executed separately in a new container, not counted as passing skips.
- This does not establish high availability, performance, multi-tenant authorization, GraphRAG quality, hardware applicability, or production readiness.

Detailed design and verification records remain local.
