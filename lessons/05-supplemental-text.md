# Lesson 5: preserve supplementary text before interpreting it

This lesson makes no LLM request. AI-authored example drafts are marked `demo_not_model_output`, not real provider responses or human approval. [Lesson 6](06-supplemental-model.md) later adds a model adapter: it was initially blocked by credentials/budget and subsequently succeeded under new authorization. Existing capture/attach-demo behavior is unchanged.

## One sentence, three distinct claims

The original-language [simulated reply](../examples/supplemental-text/reply.txt) is retained unchanged. English translation:

> PCB changes are allowed, but connector positions must not move; costs must first be confirmed by a supervisor.

| Proposed interpretation | Source clause, translated | Not established |
|---|---|---|
| PCB changes allowed | PCB changes are allowed | Unlimited changes to package, pins, or placement |
| Connector positions fixed | Connector positions must not move | A candidate can satisfy the constraint |
| Costs await confirmation | Costs must first be confirmed by a supervisor | The supervisor has agreed |

Preserve the complete original, including newlines. Separate permission, constraints, and unresolved questions; none is engineering approval.

## Three operations

1. `capture` reads a waiting follow-up snapshot and UTF-8 text. It saves case, SIM source ID, retrieval time, original text, fingerprint, and follow-up snapshot, with `draft=null`.
2. `attach-demo` validates a separately supplied JSON draft: source ID/hash, classifications, structure, and whether quotes exist in the original. Success still creates only `pending_review`.
3. `show` rereads the source, draft, warnings, and unchanged follow-up state. It updates neither the PCB flow nor E01.

`synthetic=true` is an operator declaration, not a detector. Use only simulated text here, not private customer information.

## Inspect the historical local result

```powershell
python supplemental_intake.py show --record output/supplemental-text-20260927/pending.json
```

That ignored file is not in Git. The full text and two annotations are preserved. `value_proposal=true` is only a draft; `followup_state_unchanged=waiting_for_confirmation` means no workflow release.

## Reproduce

First create an asked/waiting `r1.json` with [lesson 4](04-pcb-followup.md). Substitute your path in a fresh checkout.

```powershell
New-Item -ItemType Directory output/text-first-run
python supplemental_intake.py capture --followup output/pcb-followup-20260925/r1.json --text-file examples/supplemental-text/reply.txt --source-id SIM-TEXT-1 --synthetic --out output/text-first-run/source.json
```

Save a separate copy of the [draft template](../examples/supplemental-text/draft-template.json) as `output/text-first-run/draft-input.json`. Replace `REPLACE_WITH_CAPTURED_SOURCE_SHA256` with the captured hash. Do not put a run-specific hash back into the shared template.

```powershell
python supplemental_intake.py attach-demo --record output/text-first-run/source.json --draft-file output/text-first-run/draft-input.json --out output/text-first-run/pending.json
python supplemental_intake.py show --record output/text-first-run/pending.json
```

A nonexistent quote is refused without modifying the capture. Existing destinations are refused. New draft branches are possible, but there is no canonical latest-version registry.

## Structural checks are not understanding

| Check | Capability |
|---|---|
| Types, fields, source ID/hash | Refuse contract violations |
| Quote exists in source | Refuse nonexistent quotations |
| Declared conditional/question/unknown | Keep the proposal null |
| Actual sentence meaning | Not automatically verified |
| All constraints extracted | No completeness guarantee |
| Update the main workflow | Not implemented here |

Counterexample, English translation: “Are PCB changes allowed? Connector positions must not move.” A draft mislabeled `allowed` with `constraints=[]` may pass structure and quote checks. It remains pending with `semantic_review` and `constraint_completeness` unverified. Passing validation does not mean understanding.

Text is not executed as tool instructions, but this is not evidence of LLM prompt-injection defense: no model runs in this lesson.

## Storage and privacy

- Text limit: 16 KiB; JSON input/output limit: 256 KiB, measured in UTF-8 bytes. Reject overflow rather than truncating and pretending it is complete.
- Local JSON is plaintext. Git ignore is not encryption or access control.
- No third-party sharing, upload, paid request, or automatic cleanup occurs. Retention is operator-controlled; this lesson deleted no records.
- Fingerprints detect inconsistency, not identity or malicious tampering. There is no cross-capture SIM deduplication or anonymization.
- Real-source import policy, model extraction, approved workflow advancement, and engineering validation were outside this lesson. Later implementations must supply their own evidence.

[Implementation](../supplemental_intake.py) · [Tests](../tests/test_supplemental_intake.py). Verification records remain local.
