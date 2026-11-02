# Claude writing from facts: plan and evaluation for approval

Drafted 28 October 2026 as tier 1, item 3. The owner approved the evaluation, up to USD 1, the same day, and it ran that day.

## Result, 28 October 2026

**Final run: Claude's text was usable in 28 of 28 scenarios. A blind judge found it accurate in 26 of 28 and more helpful than the rules' version in 23 of 28.** Four runs in all, with fixes between them; USD 0.48 spent of the USD 1 cap.

| Run | Usable | Judge: Claude more helpful | Judge: accurate, Claude / rules | What changed before it |
|---|---|---|---|---|
| 1 | 27 of 28 | 27 of 27 | 22 / 13 | — |
| 2 | 27 of 28 | 26 of 27 | 26 / 26 | Facts gained the store's standing notes and the decision in words; the person check allows a store phrase such as "Skin Veil"; the rules' summary no longer says every option meets a colour wish |
| 3 | 28 of 28 | 23 of 28 | 24 / 28 | The rules no longer single out one option when prices tie; the prompt forbids outside knowledge such as what a shade number means |
| 4 | 28 of 28 | 23 of 28 | 26 / 27 | The facts describe the item bought and the replacement; the staff question rule no longer suggests a colour or size |

**Final run by feature:**
- **Comparisons:** accurate 16 of 16, more helpful 14 of 16. The rules won the two where there was little to compare, for being shorter.
- **Staff messages:** accurate 10 of 12, more helpful 9 of 12.

**What the runs found:**
- **The guard works.** In run 2 Claude wrote that the facts don't say how a shade "will look on skin". The person check refused it, and the shopper would have seen the rules' text.
- **Most early "inaccuracies" were gaps in the facts, not invention.** The judge marked text down for things the store knows but hadn't put in the facts: the preview caveat, what "REVIEWED" means, the sleeves of the blouse bought. Each was fixed by adding the fact.
- **Two real errors remain in staff messages:** a decline said it was "for reasons of style", which was the shopper's reason for returning, and a question called a shade "cooler", which its own facts didn't say. Staff edit every message before sending. After the run, the shopper's reason was renamed `shopper_return_reason` in the facts so it can't be read as staff's; that change has not been re-run.
- **The judge is from the same model family, and judges blind.** It mislabelled which text was which at least once, so its counts are a guide, not a measurement.

**Recommendation:** switch both on with the assistant, on the same limits. Comparisons are ready. Staff messages are drafts that staff edit, and the page says so.

## What is built

- **"Help me compare"** on the shopper's options. Claude Haiku 5.5 writes two or three sentences on how the options differ against what the shopper asked for, and one line per option. The rules write a plainer version from the same facts when Claude is off, over a limit, or its reply fails the checks.
- **"Suggest a message"** for staff, for each decision: approved, needs clarification or declined. Staff edit it before sending; nothing is sent or decided by the suggestion.
- **Only facts go in:** the shopper's confirmed controls (never their own note), the item bought, each option's catalog details and price difference, and the checks run on the previews, such as an earring resized to its catalog length. Never a photo, a preview or anything about the person.
- **Every reply is checked before it is shown:** every option covered, no words about the person's skin, face, body, hair or age, no number that isn't in the facts ("$2" matches "USD 2.00"), plain text within length. A failed check falls back to the rules and is recorded with its reason.
- **One shared limit.** Reads, comparisons and staff messages all count against the owner's approved 100 calls a day and 5 a shopper session; staff messages count against the daily limit only. Merchant insights show each feature's calls and spend.

## The evaluation

`manage.py evaluate_advisor` builds 28 scenarios from the seeded catalog: 16 comparisons across tops, lipstick, foundation, nail polish, earrings, necklaces and hair colour, and 12 staff messages (four exchanges, three decisions each). One requested scenario has no options and is skipped.

- **Usable rate:** how many of Claude's replies pass the app's own checks.
- **Blind second opinion (`--judge`):** Claude Sonnet 5.5 sees the facts and the two texts as A and B, alternating which is Claude's, and says whether each is accurate to the facts and which is more helpful. The judge is from the same model family, which the report says.
- Every text goes in the private report for reading.

## What it spends

| Run | Calls | Typical | Worst case |
|---|---|---|---|
| Claude Haiku 5.5, 28 scenarios | 28 | USD 0.008 | USD 0.03 |
| Sonnet 5.5 judge, 28 comparisons | 28 | USD 0.19 | USD 0.68 |

**Approved cap: USD 1** for all advisor evaluation runs together, enforced by the command from recorded spend, so a second run to check a prompt change fits too. The key is read from `.env` into this one process and never printed.

## After the evaluation

If Claude's replies are usable and the judge finds them accurate, the features go live with the assistant, on the same switch and limits as "Fill in from my words". If not, the rules' versions stay, and the evaluation says why.
