# Lesson 8: reviewed requirements, pending candidate evidence

[Lesson 9](09-conditional-range.md) subsequently implements the comparator described here. Candidate-fact review and actual wiring/temperature evidence remain missing, so the actual case stays unknown.

Historical checkpoint: 2026-09-27. The existing local-review workflow recorded Zoe's acceptance of nine fields, without a new model request or runtime-code change.

## Requirement status

The local `output/requirements-review-20260927/review-11.json` records accepted extraction for original part, requester candidates, input bounds, output voltage/current/power, topology, and qualification requirement. This accepts source interpretation, not hardware capability.

Whether 1.5 A is continuous or peak remains unknown. PCB permission still needs input in that requirement snapshot. The separate simulated supplement—PCB changes allowed, connector positions fixed—was reviewed in its own snapshot. Do not ask the same question again without checking that evidence, and do not claim the records were automatically merged. There was no whole-case aggregator.

## A footnote that changes the decision

The [TI LM5155 datasheet](https://www.ti.com/lit/ds/symlink/lm5155.pdf), SNVSB75E revision E, August 2023, was downloaded and hashed in that session. Printed pages 5 and 6 were rendered and inspected, not merely searched.

| Location | Candidate fact | Required condition |
|---|---|---|
| Page 6, section 8.3, footnote 2 | BIAS recommended range 3.5–45 V | Internal VCC regulator |
| Page 6, section 8.3, footnote 2 | BIAS recommended range 2.97–16 V | VCC tied directly to BIAS |
| Page 5, section 8.1 | BIAS absolute maximum −0.3–50 V relative to AGND | Not evidence of normal operating compliance |

The first two facts also preserve the section's junction-temperature conditions in the [candidate draft](../examples/ti-1222180/candidate-facts-v1.json). AI organized these facts; human fact review remains pending. This was not an automatic PDF extractor.

## Conditions matter more than bare bounds

A 14–30 V board input does not establish what voltage reaches BIAS or how VCC is connected.

At this checkpoint, these were manual conditional deductions, not executed comparator results:

- If 14–30 V reaches BIAS, VCC uses the internal regulator, and applicability conditions hold, 3.5–45 V contains the requested range. This supports only one range check.
- If the same input reaches BIAS with VCC tied to it, 30 V exceeds that arrangement's recommended 16 V maximum.
- If wiring/node mapping is unknown, retain unknown; do not select the favorable row.

Store `property`, `specification_class`, `range`, `conditions`, and `locator` separately. A documented condition is not evidence the case uses it. Never substitute the 50 V absolute maximum for a recommended operating limit.

## Evidence and gaps at this checkpoint

Completed: nine extraction acceptance events, preserved originals, fresh-process readback, and three candidate-fact drafts with document version, pages, footnote, and PDF hash.

The TI forum fetch failed that day; requirements used previously saved paraphrases/excerpts and the recorded human decisions, not a newly rechecked complete forum page.

Still missing: human candidate-fact review, full orderable-part/qualification evidence, actual wiring, load capability, thermal/layout checks, and engineering validation. No new inference cost, overall replacement conclusion, MCP/Neo4j integration, or automatic comparison was demonstrated in this lesson.

The next slice turns the three conditions into a deterministic comparison: only applicable evidence supports a narrow result; missing wiring remains unknown; absolute maximum never proves normal operation.
