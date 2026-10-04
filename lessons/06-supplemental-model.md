# Lesson 6: separate a model adapter from a verified model run

[Lesson 7](07-human-decisions.md) later applies three explicit user decisions to a new `text_reviewed` snapshot. The original pending model draft remains immutable.

## Latest historical smoke: 2026-09-27, 12:43 Taipei

The user created/copied a replacement key; AI updated only the project-specific Windows User setting. The old process still held the previous key and its read-only check returned 401. A child process loaded the replacement setting, passed key/live-price preflight, and made the single authorized model request.

Reported cost: US$0.0000624, 404 input tokens, 220 output tokens, no retry. This is provider-reported usage, not a final invoice audit.

The unchanged original-language fixture translates as:

> PCB changes are allowed, but connector positions must not move; costs must first be confirmed by a supervisor.

The model proposed permission, fixed connector positions, and a cost question, with original-language quotes. Source/format/quote checks passed; a separate process read the saved `model_output` draft.

The model changed “confirmation” to “approval” and questioned whether the costs concerned PCB changes. Those interpretations required review, not automatic acceptance. The original follow-up stayed `waiting_for_confirmation`; requirement fingerprints stayed unchanged. State remained `pending_review`, semantic review unverified, and `automatic_transition_allowed=false`. No human or engineering approval had occurred at that checkpoint.

Local evidence is in `output/openrouter-supplement-20260927/`: claim, response, pending, result. One smoke proves this integration instance, not stable model quality. The authorization is used; do not run it again.

## Adapter responsibilities

[supplemental_model.py](../supplemental_model.py) reads a captured source. Only its text, source ID/hash, and extraction rules enter the prompt—not the demo draft, template, expected answers, or E01 review.

Source → model request → format/cost/quote validation → existing intake validation → separate pending snapshot.

The fixed configuration is `mistralai/ministral-3b-2512`, `mistral/zdr`, temperature 0, maximum 2,048 tokens, strict JSON schema, no tools/plugins/fallback. `model_output` remains a provenance declaration, not authentication or approval. Semantic correctness and constraint completeness remain unverified.

Official contracts: [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs) describes `response_format/json_schema` and `require_parameters`; endpoint support still needs preflight. [Limits](https://openrouter.ai/docs/api/reference/limits) describes key metadata. Documentation is not proof of a successful run.

## Inspect without inference

```powershell
python supplemental_model.py plan
```

Unlike the earlier E01 planner, this default command is fully offline: no key loading or service call. The one-shot entrypoint binds an authorized source fingerprint and fixed output directory; it is not an arbitrary paid-text tool. A fresh clone lacks the ignored source and returns a fixed error. Do not edit authorization fingerprints to enable a different input.

`check` loads project credentials for read-only endpoint/key preflight; `run` can submit one paid inference. Successful planning alone proves neither connectivity nor permission to spend.

## Earlier blocked checkpoint: distinguish two causes

The initial 2026-09-27 authorization allowed one request, US$0.01, no retry. Only a key GET occurred; it returned 401 at 04:17:27 UTC. No inference POST, paid claim, credential change, or budget increase occurred then. Only approval/key-check files existed.

1. Authentication: 401 was consistent with the historical September 26 expiration, but could not distinguish expiration, revocation, or another invalid credential.
2. Conservative budget: historical prices yielded `(131072 + 2048) × 0.0000001 × 1.10 = US$0.0146432`, exceeding US$0.01. This was a policy bound, not an invoice or live-price confirmation.

A US$0.10 key limit did not override a US$0.01 trial limit. No automatic increase or shared-key fallback was permitted.

Zoe subsequently authorized the same trial at US$0.02, still one request/no retry, and replacement credentials. Fourteen targeted / 152 total tests passed, including a failing old budget boundary and passing corrected boundary. The historical bound could now fit, but live preflight was still mandatory. Credential creation remained a user action. The later real smoke above supersedes the initial block; it does not erase that history.

## Verification scope

At the initial implementation checkpoint, 13 new / 151 total local tests passed. Fixture responses exercised real file writes and fresh-process readback. A deliberately lower fake price tested the US$0.01 success branch; a historical-price case asserted zero POST. Neither was a live quote.

Checks covered repeat attempts, invalid key, changed source, unknown/excess cost, refusal/truncation, wrong model, invalid quotes, and failed local saves. A non-overwritable claim is written before POST and retained after failure. Budget-guard removal and incorrect origin labeling were detected by targeted mutations in isolated copies, then restored.

HTTP errors use bounded reads, safe public summaries, and local DPAPI archives; no raw exception or automatic retry. The initial blocked checkpoint sent no source text. The later authorized smoke sent simulated text through OpenRouter to the selected provider.

The real smoke establishes one schema-compatible adapter run. It does not establish repeated quality, complete constraint extraction, authenticated review, or hardware suitability. Those boundaries need independent evidence.
