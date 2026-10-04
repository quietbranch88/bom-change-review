# Evaluate requirement extraction before comparing cheap models

This is offline preparation: 10 public discussions, 60 extraction fields, and a planned 60 requests—two candidate models × three repetitions × 10 cases. The batch has not run. This preparation read no API key, called no OpenRouter endpoint, and produced no model comparison. Later single-case smoke tests are separate evidence.

This is not replacement approval. First ask whether a model separates what the requester wants, what is unspecified, and what other participants merely suggest.

## Data flow

```text
Public discussion → AI paraphrases / role blocks → inputs.json → prompt → model JSON (batch not run)
                               │                                             │
                               └→ reference draft → human review             └→ validation / comparison
```

- [inputs.json](inputs.json): provenance, retrieval method, locations, short paraphrases, and field contracts. Only role blocks, definitions, and output structure enter the prompt.
- [references.json](references.json): AI-organized, not yet human-reviewed drafts—not engineer-certified gold answers.
- [offline_eval.py](../offline_eval.py): builds prompts, checks structure, plans requests, and grades saved answers. It has no network runner.
- Detailed verification records are local, not public artifacts.

Prompt generation does not read references; tests check that. This is not filesystem isolation: an Agent with directory access could still read answers. A future trial should send serialized prompts without file tools.

## Ten development cases

These are Traditional Chinese paraphrases of real public questions, not verbatim documents. Cross-domain examples test extraction, not supported engineering comparators.

| Case / source | Error to detect |
|---|---|
| [E01: LT8301ESS replacement](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/1222180/lm5155-replace-lt8301ess) | LM5155 metadata is not the original part; 1.5 A is not automatically continuous |
| [E02: LM5155 shortage](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/1093801/lm5155-possible-alternatives-to-replace-lm5155) | Separate requester candidates from support suggestions; historical shortage is not current stock |
| [E03: TXB0104 replacement](https://e2e.ti.com/support/logic-group/logic/f/logic-forum/1464630/txb0104-q1-txb0104-replacement-material) | Signal level is not power output; explicit no-PCB-change means false |
| [E04: LPDDR4 compatibility](https://community.nxp.com/t5/i-MX-Processors/IMX-8M-plus-quad-lpddr-compatibility/m-p/1998106) | Distinguish original memory, candidate, and processor; do not replace die with rank |
| [E05: MC68332 replacement](https://community.nxp.com/t5/8-bit-Microcontrollers/MC68332-Replacement-amp-25MHz-gt-16MHz-Compatibility/m-p/2329442) | Desired unchanged firmware is not verified compatibility; distinguish MHz and kHz |
| [E06: SN74LVC1G125DBV inquiry](https://e2e.ti.com/support/logic-group/logic/f/logic-forum/1282284/sn74lvc1g125-sn74lvc1g125dbv) | Do not silently correct the question using a reply's full part number |
| [E07: historical lifecycle](https://e2e.ti.com/support/amplifiers-group/amplifiers/f/amplifiers-forum/1276292/tlc3702-case-576614-technical-data-inquiry) | Old “Active” status is not verified current lifecycle |
| [E08: iMX6Dual to DualPlus](https://community.nxp.com/t5/i-MX-Processors/Is-it-simply-possible-to-replace-an-i-MX6Dual-by-an-i/td-p/962278) | Compatibility questions are not proof; asking about PCB risk does not forbid changes |
| [E09: LM5164 pin compatibility](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/988665/lm5164-q1-pin-to-pin-compatible) | Include the requester's later 500 mA minimum; EV context does not imply AEC-Q100 |
| [E10: AP7362-33SP-13 replacement](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/994420/alternative-to-ap7362-33sp-13) | Do not infer requested output voltage from “33” in the name |

Retrieved 2026-09-25. E02/E09 pages timed out and used official-page search-index content; the other eight pages were read directly. Full page snapshots/images were not retained; E03's attachment was not interpreted. Only necessary paraphrases remain, without participant names, email addresses, or private BOMs. Original fixture language is retained to preserve evaluation identity.

## E01 example

Q1 names LT8301ESS. Q2 requests input 14–30 V, output 5 V / 1.5 A / 7.5 W, flyback, and AEC-Q100. M1 metadata and R1 support reply are distractors.

Excerpt only—the actual response must contain every case field:

```json
{
  "input_max": {"value": 30, "unit": "V", "evidence_ids": ["Q2"]},
  "continuous_current": {"value": null, "unit": null, "evidence_ids": []}
}
```

30 V is explicit; continuous-current meaning is not. Reviewers must check the source and paraphrase for lost conditions, not just JSON appearance.

## Grading contract and limitations

1. Structure: all and only required fields; correct types/units; no duplicate JSON keys, NaN, extra fields, reply/metadata evidence, or fabricated evidence for unknowns. Unknown scalars use null; unspecified candidates use [].
2. Values: exact reference comparison. 14 equals 14.0; true does not equal 1. Preserve part suffixes/case; array order does not matter.
3. Evidence: exact minimal reference sets, scored separately from values. A legal ID does not prove semantic support.

`all_fields_match=true` means this draft rubric matches, not replacement safety or model certification. Invalid structure receives a format failure, not partial semantic grading.

Exact evidence matching may reject another reasonable set. E07 currently requires Q2, but a reviewer may accept Q1's supply question. Preserve such responses, adjudicate independently, version the rubric, and regrade every model. Do not selectively relax one model's score. Call the metric reference-match rate, not accuracy.

These cases participated in prompt/grader development. AI helped create both paraphrases and references, so common omissions are possible. Feeding references back proves a solvable grading path, not model ability. Human review and independent held-out cases are required. This set does not establish PDF/table/long-English extraction, injection resistance, or real engineering judgment.

## Local commands

Python 3.11+; the historical verification used 3.13.14:

```powershell
python offline_eval.py check
python offline_eval.py prompt --case E01
python offline_eval.py plan
python offline_eval.py grade --case E01 --response path/to/model-response.json
python -m unittest discover -s tests -v
```

Replace the response placeholder with your saved complete answer. If needed, substitute `uv run --no-project --python 3.13 python`. The tool prints reports; it does not overwrite references or create human-review records.

- `check` checks reference structure, not truth.
- `prompt` prints input, not inference.
- `plan` lists `mistralai/ministral-3b-2512` and `google/gemini-3.1-flash-lite`, three repetitions per case, 60 requests, all `not_run`. Costs, latency, and providers are null. Matching prompt hashes do not prove endpoint/schema support.
- `grade`: full match exits 0; mismatch 1; input/JSON/dataset error 2. Historical CLI checks used actual subprocesses.

A future runner needs explicit budget authorization and actual provider/configuration/token/cost/retry/failure records. Sixty planned requests alone would not establish stable quality. Extraction and engineering review remain separate.
