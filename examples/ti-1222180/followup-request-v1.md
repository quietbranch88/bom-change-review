# LM5155 evidence request: what is missing, and what closes the gap?

Date: 2026-09-30. Offline draft: not sent, no actual reply received, no review changed.

A conditional graph projection was implemented later that day; that does not mean this request received real evidence or engineering confirmation. Historical limits below are labeled accordingly.

The narrow question is whether the LM5155 BIAS recommended voltage range covers 14–30 V under this case's actual wiring. It is not whether LM5155 directly replaces LT8301ESS.

> Decisions need evidence and rules. Unknowns need reasons; closing gaps needs actions and acceptance criteria.

## Work backward from the decision

Before comparing ranges, establish that the numbers were extracted correctly, refer to the same voltage node, and have applicable conditions. A board-input requirement cannot automatically be compared with a conditional BIAS specification.

This request follows the existing narrow comparator, not a complete replacement checklist or an engineer-approved universal rule.

| Gap | Why it blocks the check | Owner / requested evidence | Closure criterion |
|---|---|---|---|
| `fact_review_missing` | Values/conditions remain pending excerpts | Datasheet reviewer: exact version, values, units, class, conditions, footnote | Explicit acceptance bound to the checked draft; correct errors first, then review the new version |
| `application_conditions_missing` | BIAS mapping and applicable row are unknown | Circuit owner: wiring, design version, junction-temperature range, references | Mapping, VCC arrangement, and temperature select one applicable row; bind to the same case/version |

Correct extraction and applicability to a circuit are separate confirmations.

## Form for reviewers

Every blank means missing information, not agreement. This Markdown form is not an importable review receipt.

### A. Confirm candidate facts

- Candidate model: LM5155; full orderable part not confirmed.
- Draft: [candidate-facts-v1.json](candidate-facts-v1.json).
- Document: SNVSB75E, revision E; page 6 section 8.3 / footnote 2; page 5 section 8.1.
- Check both VCC-dependent BIAS ranges, junction-temperature conditions, and the distinction between absolute maximum and normal operation.
- Decision: ______ (accept / correct / unable to confirm).
- Corrections: fact ID, incorrect content, source location: ______.
- Reviewer, date, source/review reference: ______.

Completion requires explicit acceptance bound to the checked content, not “seen.” Current fingerprints check version consistency; names/references are declarations, not authentication or signatures.

### B. Confirm the actual design

1. Does the requested 14–30 V range directly describe BIAS voltage?
   - Answer: ______ (yes / no / unknown).
   - Design version, schematic page, nets/nodes: ______.
   - If no, describe the intermediate supply and known BIAS voltage: ______.
   - No is valid information. The current direct-mapping comparator keeps unknown rather than forcing yes.

2. How are VCC and BIAS connected?
   - Answer: ______ (internal VCC regulator / VCC tied to BIAS / other / unknown).
   - Wiring evidence for the same design version: ______.
   - Do not force other into a supported category; analyze separately if the rule does not cover it.

3. What is the junction-temperature range?
   - Minimum: ______ °C; maximum: ______ °C.
   - Evidence and operating conditions: ______ (identify analysis, simulation, or measurement).
   - Do not substitute ambient temperature. State unknown when unknown.

4. Who confirms which version?
   - Design reviewer, date, design document/version/reference: ______.
   - Bound requirement snapshot and candidate draft: ______.

Filled text is not automatically sufficient. Evidence must support wiring/conditions and match the case/specification versions. The program checks fields, binding, and numerical rules; it does not prove attachments establish circuit behavior.

## Intended reply-processing flow

The complete workflow was not implemented when this form was drafted:

1. Save the original reply/source and actual-versus-simulated identity. Without new evidence, do not ask a model to guess.
2. A person or LLM prepares a pending draft; an LLM cannot sign engineering confirmation.
3. A person checks text/attachments, accepting, correcting, or retaining unknowns.
4. Run the version-bound comparator and save a new result without overwriting history.
5. Project the new result into Neo4j and retain previous snapshots.

At the initial drafting checkpoint the importer supported only pending facts with no application context. Later conditional-projection work expanded that slice. It did not implement receipt collection or prove this form's real end-to-end reply flow.

## Three teaching branches, not actual replies

Assume correctly reviewed facts, direct input-to-BIAS mapping, applicable junction temperature, and matching versions:

- Internal VCC regulator: the draft's 3.5–45 V covers 14–30 V in this one check.
- VCC tied to BIAS: 2.97–16 V does not cover 14–30 V.
- Unknown wiring: keep unknown; do not select the wider range.

None is engineering approval. Output capability, qualification, pins, layout, and hardware verification remain unassessed.

## Sources and status

[Case source](source.md) is a public question, not a real imported BOM. The [pending fact draft](candidate-facts-v1.json) reuses prior extraction; this form did not reverify the PDF. [range_assessment.py](../../range_assessment.py) defines the current local review/version/wiring/temperature checks.

Gap actions come from versioned project rules, not datasheet prose. Neo4j is a rebuildable projection. The actual local unknown report still had both gaps when this form was added. No actual receipt, gap closure, reassessment, or database update occurred as part of this document.
