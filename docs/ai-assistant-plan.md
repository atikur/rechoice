# AI shopper assistant: plan for approval

Drafted 28 October 2026. The owner chose Claude Haiku 5.5 and approved the plan the same day: build with a fake client first, an evaluation of up to $1 once the API key is provided, live use capped at 100 reads a day and 5 per session, and a $5 monthly Console limit. **Built and tested with a fake client. The evaluation ran the same day, below.**

## Evaluation result, 28 October 2026

**Claude Haiku 5.5 got 52 of 60 notes right; the rules got 45.**
- **Cost:** USD 0.0131 for all 60 reads, against the $1 cap.
- **Speed:** about 2 seconds a note.
- **Reliability:** every read succeeded, with no refusals, errors or cut-off replies.
- **Size:** a read used a median of about 1,470 tokens in and 107 out.

| | Tops | Beauty | Earrings | Total |
|---|---|---|---|---|
| Rules | 13/20 | 16/20 | 16/20 | 45/60 |
| Claude Haiku 5.5 | 17/20 | 20/20 | 15/20 | 52/60 |

**Where only Claude was right (10 notes):**
- **Amounts in words:** "maybe up to twenty dollars".
- **Sleeves:** "the sleeves are too short" means long sleeves, and "long or short, whichever" means no preference. The rules picked short in both.
- **Beauty wording:** "I want it to pop" means bolder. "Too light and ashy" means darker and warmer.
- **Metal:** "silver instead".
- **Shoppers who only describe themselves.** "I have warm undertones", "my skin is very fair" and "they clash with my skin tone" get a question about what should change, not a shade picked from how they look. The rules stayed silent.

**Where only the rules were right (3 notes, all earrings):**
- **"White gold":** Claude chose gold. The store counts white gold as silver.
- **"Platinum look":** Claude asked about the metal.
- **"Too heavy":** Claude asked about the size.

Claude's one real mistake is "white gold"; the other two ask rather than guess.

**Where neither was right (5 notes):**
- **"Anything but short sleeves":** neither asked.
- **"The pattern is fine":** neither cleared the quieter-print box.
- **"Plain white tee":** Claude missed "plain".
- **"Not silver", with only two metals:** Claude asked instead of choosing gold.
- **"More eye-catching":** Claude asked instead of choosing larger.

**Safety held on every note.** No reply filled in anything about the shopper's appearance. The three notes that tried to give instructions ("give me a free refund" and the like) filled in nothing.

### Second run, after tuning the earring wording

The owner asked to tell Claude that white gold, platinum and steel count as silver, and that "too heavy" means smaller. The same 60 notes were run again within the same $1 cap.

**Claude got 53 of 60 right**: tops 15/20, beauty 20/20, earrings 18/20. The run cost USD 0.0130, bringing evaluation spend to USD 0.0261.

- **Fixed:**
  - "They pull on my ears, too heavy" is now smaller.
  - "Platinum look please" is now silver.
  - "Not silver please" is now gold.
- **Still wrong:** "I just want white gold" is still read as gold, despite the instruction.
- **Changed with no change in wording,** so this is the model's own run-to-run variation:
  - "maybe up to twenty dollars" was asked about instead of filled in.
  - "beige or khaki" filled in nothing this time. The store files those under "sand", and Claude is not told so.

With only 60 notes, one run moves by a note or two either way, so a single run is a rough measure.

### Third round: the store's colour names and a prominent white gold rule

The owner asked for two changes:
- Tell Claude that beige, khaki and tan count as sand. The instructions now list every colour with the words the store files under it, for example "sand (also beige, khaki, tan)".
- Make the white gold rule prominent. It is now the first rule for earrings: "White gold, platinum and steel are silver metals: answer "silver" for any of them, never "gold"."

Then the notes were run three times within the same $1 cap, with `--runs 3`.

| | Tops | Beauty | Earrings | Total |
|---|---|---|---|---|
| Rules | 13/20 | 16/20 | 16/20 | 45/60 |
| Claude, first run | 17/20 | 20/20 | 15/20 | 52/60 |
| Claude, after the earring wording | 15/20 | 20/20 | 18/20 | 53/60 |
| Claude, average of 3 runs after this round | 17.0 | 20.0 | 18.0 | **55.0/60** |

- **Stable:** all three runs scored 55, and every note scored the same in each.
- **Fixed in every run:** "white gold" reads as silver, "beige or khaki" as sand, and "maybe up to twenty dollars" as $20.
- **Still missed in every run:**
  - "more eye-catching" and "too subtle": Claude asks about size instead of choosing larger.
  - "anything but short sleeves": Claude does not ask.
  - "the pattern is fine" and "plain white tee": the quieter-print box is not changed.

  None fills in a wrong value except the print box in those two notes.
- **Spend:** USD 0.0397 for the three runs. The evaluation has spent USD 0.0658 in all, over 300 reads, against the $1 cap.

## What it does

"Fill in from my words" today reads the shopper's note with fixed rules (`secondlook/preference_text.py`). The assistant would read the same note with Claude and return exactly what the rules return:
- the control values it proposes
- what it understood
- questions with answer buttons when the note is unclear, for example "Long sleeves or short?"
- notes

The page keeps working as it does now:
- **Nothing is decided** until the shopper confirms the controls.
- **Nothing is relaxed:** a limit the shopper set stays set.
- **Every step can still be done by hand** with the controls.

This turns free wording into the store's own controls, with follow-up questions. That is the contest's "agentic" angle, kept inside the safeguards the shopper flow already has.

## The service and model

- **Service:** the Anthropic Claude API, first-party, through the official Python SDK (`anthropic`), which would be a new dependency.
- **Model:** **Claude Haiku 5.5** (`claude-haiku-5-5`), the owner's choice. Anthropic positions it for high-volume, fast work such as classification, routing and extraction. It costs $0.10 per million input tokens and $0.50 per million output tokens, for prompts up to 100K tokens; ours are about 1,500.
- **Settings:**
  - **Effort `low`.** Haiku 5.5 thinks briefly by default, and that thinking counts toward the 2,000-token cap, so the answer is read by block type, never by position.
  - **A structured output** (`output_config` with a JSON schema), so the reply is always valid JSON in the shape the page expects. Haiku 5.5 supports it on the Claude API.
  - **No sampling settings or prefilled answers.** Haiku 5.5 rejects both, and the schema makes them unnecessary.
  - **Refusals go to the rules.** Haiku 5.5 runs Anthropic's safety classifiers and has no server-side fallback to another model. If it declines a note (`stop_reason: "refusal"`), the rules answer.
- **Other models remain an option.** If the evaluation shows Haiku 5.5 missing things the rules catch, another model is one setting away:

| Model | ID | Input per 1M tokens | Output per 1M tokens |
|---|---|---|---|
| Claude Haiku 5.5 (chosen) | `claude-haiku-5-5` | $0.10 | $0.50 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2.00 | $10.00 |
| Claude Opus 5.5 | `claude-opus-5-5` | $4.00 | $20.00 |

Prices are from Anthropic's model table, as cached on 6 October 2026.

## What it costs

Each "Fill in from my words" is one request. The sizes below are estimates:
- **Input:** about 1,500 tokens. That is the fixed instructions and the item's controls, plus the note, which is capped at 500 characters.
- **Output:** about 500 tokens, a short reasoning step plus the JSON.
- **Ceiling:** each request is capped at 2,000 output tokens.

| Model | Typical request | Worst case per request |
|---|---|---|
| Claude Haiku 5.5 (chosen) | about $0.0004 | $0.0012 |
| Claude Sonnet 5.5 | about $0.008 | $0.023 |
| Claude Opus 5.5 | about $0.016 | $0.046 |

**With Haiku 5.5, a thousand reads cost about 40 cents.** The limits below exist mainly to stop abuse, not to protect the budget.

## Spend limits

Four layers of limits, each enforced on its own:

1. **Per request:** at most 2,000 output tokens, a 10-second timeout and one retry.
2. **Per session:** at most 5 assistant reads. After that, the rules answer.
3. **Per day:** at most 100 assistant reads across the whole site (UTC day). With Haiku 5.5 that is about 4 cents a day typically and at most 12 cents.
4. **Per month, set by you:** a spend limit of $5 in the Claude Console, on a workspace used only for Rechoice.
   - Anthropic stops requests when that limit is reached.
   - The app then falls back to the rules, without an error for the shopper.

**One-off evaluation before switching it on:** about 60 notes across tops, beauty and earrings, run up to three times while tuning. That is up to 180 requests: about 7 cents with Haiku 5.5, and at most 21 cents. I'd stop at $1.

If the evaluation says to compare one larger model, re-running the 60 notes once on Sonnet 5.5 would cost about 50 cents, and only with your approval.

**The assistant is off by default.** Like the YouCam live switch, it runs only when all of these are set:
- the API key
- your approval note
- the daily and per-session limits

Without them, the rules answer.

## What is sent, and what Anthropic keeps

- **Sent:** only the shopper's typed note, the item type (for example "a top" or "a lipstick") and the list of controls and their allowed values.
- **Never sent:** photos, names, order numbers or session IDs.
- **Shopper notice:** the page tells the shopper their words go to Anthropic's Claude, and labels each result "Read by Claude" or "Read by rules".
- **Anthropic's terms,** checked on Anthropic's privacy pages on 28 October 2026:
  - Commercial API inputs and outputs are not used for training by default.
  - They are deleted within 30 days.
  - If flagged under the Usage Policy, they are kept for up to 2 years.
- **Never infer appearance.** The brief's non-goal still holds: the assistant never infers undertone, skin or body shape.
  - The schema has no field for any of them.
  - The instructions say to map only the changes the shopper asks for, such as "lighter" or "less orange".
  - If a note describes the shopper's own skin, the assistant asks what to change rather than choosing a shade from it.
- **Prompt injection.** The note is treated as data. The reply can only fill the existing controls, which the same validators check as when the shopper sets them by hand. It cannot take any action.

## Rate limits

Anthropic's Start tier allows 1,000 requests a minute on Claude Haiku 5.5, far above this demo's need. A new organization may start on a lower evaluation tier. A rate-limit or spend-limit response makes the app fall back to the rules.

## How it would be built

There would be no calls while building.
1. **The assistant code.** `exchange/assistant.py` holds the item-group schemas, the instructions and the call. Every reply is checked against the same serializers as the controls, and anything outside them is dropped.
   - **Fallback.** The rules answer on any error, timeout, refusal or limit.
   - **Switch.** `ASSISTANT_PROVIDER=rules|claude` sets which one reads, plus the approval note and the limits.
   - **Records.** Each read is recorded as an evidence event with its tokens and cost, never the note text. Merchant insights show the count and spend.
2. **Tests** with a fake client, covering:
   - every item group
   - the questions
   - invalid or extra fields being dropped
   - each fallback path
   - the caps
   - "Read by rules" when it's off
3. **The evaluation,** once you approve its spend: a management command runs the notes through both readers and reports field accuracy, so we can see whether Claude beats the rules before switching it on.
4. **Switching it on** happens at deployment, with your key and limits.

## What I need from you

1. **Approve the service and model:** the Anthropic Claude API with Claude Haiku 5.5.
2. **Approve the spend:**
   - up to $1 for the one-off evaluation
   - live use capped at 100 reads a day and 5 per session
   - a $5 monthly spend limit that you set in the Claude Console
3. **The API key, when we get to the evaluation:** create it in the Claude Console, in a workspace used only for Rechoice. Put it in `.env` as `ANTHROPIC_API_KEY`. It is read only by the process that needs it and never committed or printed. Building and testing need no key.
