# Build requirements from a real question: TI-1222180

This is a manually checkable example, not a completed automatic extractor. AI helped read the source and organize a reference draft. Domain-engineer review and independent model-quality evaluation remain pending.

## Read before extracting

1. Read the [question excerpt](../examples/ti-1222180/source.md) without consulting the forum reply.
2. Identify the original part, requirements, and unknown conditions.
3. Compare with the [reference draft](../examples/ti-1222180/request.reference-draft.json). Extracted requirements point to Q1–Q6; unknown fields have no fabricated evidence.

| Situation | Correct handling | Incorrect handling |
|---|---|---|
| Page metadata names LM5155 | Identify LT8301ESS from the question | Treat the product tag as the original part |
| The question gives an input range | Preserve both bounds and unit: 14–30 V | Keep only 30 V |
| AEC-Q100 is requested | Store a requirement to verify for the exact candidate | Assume a vendor recommendation proves qualification |
| A reply suggests a candidate | Keep reply and question separate | Backfill it as the requester's candidate |
| PCB changes are not discussed | Mark the permission unknown | Assume PCB changes are forbidden |
| 1.5 A is not identified as peak or continuous | Preserve the value and missing qualifier | Label it maximum continuous load |

Associating current and power with the output requires contextual interpretation, so it remains pending review. Consistent arithmetic does not prove candidate suitability.

## Why store it this way?

- Separate excerpts from normalized values so extraction can be checked again.
- Separate requirements from component facts. Requested 14–30 V is not a datasheet specification.
- Separate questions from replies to prevent answer leakage.
- Separate missing information from negative requirements. Silence about isolation, temperature, or PCB changes does not mean they are unnecessary.

## Acceptance for a future extractor

Supply only the question body, not the reference JSON. Check exact part identity, values, units, evidence locations, and unknowns. Returning everything as unknown is not acceptable; borrowing candidates from replies is also not acceptable. This development example cannot establish accuracy on new data.

Later, check candidate facts against manufacturer documents. Do not create an unconditional replacement relationship. Neo4j is not required for this first step.

## Historical evidence and limits: 2026-09-25

The public TI forum page was read in that session; only short excerpts and paraphrases are retained. AI checked six source locations; human review remained pending. Local PowerShell checks exited 0: JSON parsing, five content assertions, four files present with no trailing whitespace, and resolvable local Markdown links. `git diff --check` exited 0; direct file checks supplied evidence for then-untracked files.

At that checkpoint, `codex/lesson-01` had no commit and had not been published. No independent LLM extraction, database, RAG, MCP, EDA, or hardware test ran. These are historical observations, not the current publication state.
