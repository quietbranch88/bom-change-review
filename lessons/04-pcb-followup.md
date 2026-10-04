# Lesson 4: unknown does not mean never asked

This lesson tracks one field, `pcb_changes_allowed`, using local simulated replies. It sends no external message, calls no model, and does not change the original E01 review. [Lesson 5](05-supplemental-text.md) later adds text capture; this fixed-choice interface remains unchanged.

## Three outcomes

These are separate branches from the same asked/waiting snapshot, not three sequential replies from one requester.

| Simulated answer | Proposal | State |
|---|---|---|
| PCB changes allowed | true | `pending_review` |
| PCB changes forbidden | false | `pending_review`, not missing data |
| Unknown; needs confirmation | null | `waiting_for_confirmation`, do not ask again |

`reply --answer` accepts only `allow`, `deny`, or `unknown` with fixed text. It is not natural-language extraction.

```powershell
python pcb_followup.py show --record output/pcb-followup-20260925/deny.json
```

The historical output is ignored and local. `value_proposal=false` is the simulated proposal; `base_value_unchanged=null` and `base_field_status=needs_input` describe the unchanged original. The tool does not silently approve it.

## Reproduce in a new directory

If the historical review file is unavailable, create a demo review with [lesson 2](02-local-review.md) and substitute its path. Destinations must not already exist.

```powershell
New-Item -ItemType Directory output/pcb-first-run
python pcb_followup.py init --review output/openrouter-e01-20260925-fixed-04/review-2.json --out output/pcb-first-run/r0.json
python pcb_followup.py ask --record output/pcb-first-run/r0.json --out output/pcb-first-run/r1.json
python pcb_followup.py reply --record output/pcb-first-run/r1.json --answer unknown --source-id SIM-1 --out output/pcb-first-run/r2.json
python pcb_followup.py reply --record output/pcb-first-run/r2.json --answer deny --source-id SIM-2 --out output/pcb-first-run/r3.json
python pcb_followup.py show --record output/pcb-first-run/r3.json
```

Here the simulated requester first does not know, then says changes are forbidden. Both replies remain without another question. `ask` records a teaching action, not message delivery. Each command exits; this is not background polling.

## Keep sources and progress separate

The snapshot preserves the original review, fingerprints, event sequence/times, SIM source IDs, fixed text, and choices. It neither extends the extraction schema nor writes SIM citations into the original requirement.

- Asking twice or asking after a value exists is refused.
- Duplicate SIM IDs and existing destination files are refused.
- Allow followed by deny produces `conflict` with a null proposal, not last-write-wins.
- Deny followed by unknown retains false as a pending proposal; unknown is not a retraction.
- Changed case source/contract produces `stale` and refuses further writes; start a new flow rather than silently merging.

## Limits

The tool binds only the supplied review. It has no central latest-version registry, authenticated identity, signature, multi-user coordination, or global deduplication. Fingerprints are not authorization.

At this lesson's checkpoint, real supplementary-source import, LLM extraction, approved writeback, conflict adjudication, rules across all 11 fields, and automatic notifications were not implemented. Later lessons add explicitly scoped capabilities. A true proposal does not remove package, pin, or location restrictions; engineering suitability stays `not_evaluated`.

Local software checks cannot establish complete hardware rules or replacement suitability. Detailed session audits remain local.
