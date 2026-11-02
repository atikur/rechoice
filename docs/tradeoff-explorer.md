# Trade-off explorer

An empty shortlist should help a shopper understand their choices without pressuring them to give
up every preference. For appearance-related top exchanges, Rechoice lets the shopper name one limit
they are willing to revisit: maximum extra spend, required sleeves or quieter catalog print.

The explorer reuses the ordinary eligibility rules. It preserves all other confirmed limits, current
stock, exchange policy, known pricing and fees. Colour and neckline remain soft ranking preferences,
as in the existing shortlist. It makes no image-generation or assistant call.

## Try the fictional cases

1. Open the demo order, choose Midnight Floral Blouse and “I don't like how it looks”. Keep quieter
   print and long sleeves, set maximum extra spend to $0, and confirm. No top qualifies.
2. Choose **Maximum extra spend**, then **Explore this limit**. The minimum feasible budget is $2;
   Ribbed Turtleneck qualifies with the other limits preserved. This minimum is calculated across
   current catalog prices and fees, before applying soft preference ranking.
3. Choose **Review this change**. The controls show $2 and restore the other controls to their last
   confirmation, so unrelated unconfirmed edits cannot slip into the proposal. Saved preferences are
   still $0. Choose **Confirm preferences** to make the change and get a fresh shortlist.
4. Request recorded previews on Model A to compare the purchased blouse and the one replacement.
   These reuse the existing YouCam results and spend no units.

For a separate sleeve case, confirm a $1 budget with long sleeves and quieter print. Exploring
**Sleeves** offers short sleeves, keeping the $1 limit and quieter print. Exploring print alone
does not unlock a top in that case; the app says so rather than changing another constraint.

## API and invariants

`GET /api/sessions/<uuid>/tradeoffs?constraint=budget|sleeves|print` is browser-session scoped and
read-only. It requires confirmed preferences, an open session and an appearance-related top exchange.
It returns `POSSIBILITIES`, `NO_SINGLE_CHANGE`, or `MATCHES_EXIST` if the catalog now satisfies the
original controls. Plans include the changed limit, before/after values, preserved constraints,
current candidate details and `confirmed: false`. Only a budget plan claims a numerical minimum;
categorical sleeve/print changes have no cross-category “smallest change” claim.

Exploration does not write preferences, evidence events, preview jobs or quotes. Review fills the
browser's controls. The existing preferences PUT performs confirmation and increments its version.
The normal shortlist and quote paths independently recheck eligibility; a proposal cannot authorize
a quote or bypass a later stock change. An unavailable single-limit change is reported explicitly.

## Evidence and limits

Pure rule tests cover minimum budgets, fees, control ceilings, immutable inputs, deterministic results,
hard filters, categorical alternatives and the refusal to relax two limits. API tests cover ownership,
read-only behaviour, stale stock, confirmation and quote eligibility. Chrome tests cover the $0→$2
flow, recorded previews, unconfirmed edits, empty results, sleeve changes, mobile layout and axe
accessibility checks. The demo adds a separate no-match case after the staff workflow.

This is a demonstrated product capability, not an exclusive novelty claim. It does not establish
reduced returns, shopper willingness to compromise or merchant value. Independent shopper and
merchant feedback remains the next useful evidence for those claims. The frozen YouCam photo study
and its measurements are unchanged.
