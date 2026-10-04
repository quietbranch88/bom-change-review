# Lesson 7: apply human decisions to the workflow

Historical checkpoint: 2026-09-27. The implementation applied Zoe's three explicit text decisions without a model request or additional API cost.

## More than a note

[supplemental_review.py](../supplemental_review.py) applies source-bound decision JSON to a pending draft and saves a new replayable snapshot:

`pending_review + explicit decisions → source/item validation → new text_reviewed snapshot`.

The pending original remains unchanged. The new record retains the draft, its fingerprint, reviewer declaration, decision reference, application time, and each before/action/after event. Reads reconstruct the reviewed draft rather than disguising edited text as the model's original answer.

| Target | Action | Result, translated into English |
|---|---|---|
| permission | accept | PCB changes allowed |
| constraints/0 | accept | Connector positions must not move |
| open_questions/0 | correct | Costs must first be confirmed by a supervisor |

Original-language source and decision files are preserved. Reviewing the phrase “supervisor confirmation” does not mean the supervisor has confirmed costs. The question remains unresolved; the original follow-up does not complete automatically, and when changes may begin remains unspecified.

## Human versus program responsibilities

A person specifies acceptance/correction, reviewer, and decision reference. The program checks the complete pending fingerprint, requires exactly one decision per existing item, validates replacement structure/quotes, and records history. It does not decide meaning.

Only `accept` and `correct` are supported; all existing items must be reviewed before a `text_reviewed` output is written. No partial-review UI, rejection, item addition/deletion, or engineering sign-off is implemented here.

`pending_sha256` is `local_review.fingerprint` over canonical JSON, not a byte-level file SHA256. In this historical example the prefixes were e18c… versus 71b1…. Old decisions cannot apply to changed drafts.

| Output | Meaning |
|---|---|
| `state=text_reviewed` | Decisions recorded for existing draft items |
| `review_scope=existing_draft_items_only` | Does not establish extraction completeness |
| `semantic_review=human_decisions_recorded` | Human decisions recorded, not infallible truth |
| `constraint_completeness=unverified` | Missing constraints may still exist |
| `unresolved_questions` | Reviewed wording, unresolved real-world matters |
| `automatic_transition_allowed=false` | No automatic original-requirement change |
| `engineering_suitability=not_evaluated` | No PCB or replacement approval |

## Local usage

These historical ignored files are absent from a fresh clone. The application already ran; do not overwrite its result. For a new case, create your own pending draft, explicit fingerprint-bound decisions, and new destination.

Historical application command:

```powershell
python supplemental_review.py apply --pending output/openrouter-supplement-20260927/pending.json --decisions output/openrouter-supplement-20260927/human-decisions-1.json --out output/openrouter-supplement-20260927/text-reviewed-1.json
```

Read-only inspection:

```powershell
python supplemental_review.py show --record output/openrouter-supplement-20260927/text-reviewed-1.json --pending output/openrouter-supplement-20260927/pending.json
```

A different current pending or updated case source produces `stale` and a null proposal while retaining history. Unparsable source corruption produces a fixed error rather than a valid historical result.

## Guards and limits

Missing/duplicate/unknown targets, wrong fingerprints, nonexistent quotes, stale sources, and forged `accept` after-values are refused. Existing outputs cannot be overwritten; reviewed snapshots cannot be reapplied as pending.

Self-declared reviewers and fingerprints are not login, authorization, or signatures. A file owner can forge all records. Exclusive writing prevents overwriting one destination, not cross-directory deduplication or distributed transactions. Interrupted files are not automatically removed or retried. At this checkpoint there was no Neo4j, MCP, EDA, or real engineering approval integration.

Historical verification: 12 new / 164 total tests passed; two isolated mutations were detected and restored; actual apply → file → fresh-process show succeeded. This proves local text-review behavior, not model quality, authenticated identity, or hardware suitability. Detailed records remain local.
