# Niche story decision packet

> **Positioning note (July 2026):** This is a dated acquisition-story decision packet. Focal's current public framing is the neutral, pocket studio OS for photographers; the wedding/F&B comparison below is not a current launch claim.

> **Status:** Kevin decision required · **Prepared:** 2026-07-17 · **Feeds:**
> Conductor T3 (reviewer-demo content) and T4 (store metadata + screenshots).
> T3 remains independently held by
> [issue #185](https://github.com/Ayyitskevin/mise/issues/185).

This decision selects Focal's **public acquisition story**. It does not remove
features or reverse the commercial-spine ADRs.

## Executive summary

**Recommendation: wedding-first**, with the commercial spine presented as an
advanced capability for wedding studios that also shoot brands. This matches the
current product statement, the warm native client/project/gallery/booking loop,
and the more complete existing demo state: the wedding preset already has an
accepted proposal, signed agreement, and sent retainer invoice.

Do **not** justify the recommendation by saying wedding has a proven larger
software market. Public data do not isolate wedding-photo studios from F&B-photo
studios, especially among owner-only businesses. Wedding-first wins on story
clarity and current direction; F&B-first wins on specialist product depth.

There is one condition. The tenant website templates still say food, restaurants,
Kevin, and Asheville, and the native owner app always exposes **Commercial**.
Before wedding positioning goes live, either exclude the tenant website builder
from the hosted-v1 promise or add a follow-up to make that surface
specialty-aware. Keep Commercial out of the first screenshot set; do not hide its
existence in product documentation.

## Evidence that constrains the choice

| Signal | Evidence | What it means |
|---|---|---|
| Photography category ceiling | 2023 Census data report **236,666 nonemployer** and **16,504 employer** photographic-services establishments: **253,170** combined. At Focal's $240 annual price, converting every establishment would be a mechanical ceiling of **$60.8M ARR**. | The category can support a focused micro-SaaS. This is not reachable TAM, a forecast, or a niche split. |
| Commercially active proxy | Among nonemployers, **39,121** report at least $50k in receipts and **73,093** report at least $25k. Adding the 16,504 employer establishments gives an all-specialty proxy of **55,625–89,597** operations. Illustrative 1% penetration is **556–896 studios** or **$134k–$215k ARR**. | More useful for planning than the full ceiling, but still not a wedding or F&B SAM; establishments also do not map perfectly to subscription accounts. |
| Solo-business fit | Census classifies **225,212** of the 236,666 nonemployers (**95.2%**) as sole proprietorships. BLS separately reports **151,200 photographer jobs** in 2024 and **66% self-employed**. | A pocket-sized OS for owner-operators fits how the category is structured. Jobs and establishments are different units, so the figures should not be added. |
| Segment signal | Of employer establishments, Census reports **11,483 portrait-studio** and **5,021 commercial**. The portrait class includes wedding and several non-wedding services; the much larger nonemployer population is not split at that detail. Of the commercial locations, **90.7% have fewer than five employees**. | The employer subset is numerically larger on the portrait side, but cannot resolve the niche decision or prove a wedding-studio SAM. Commercial also remains a credible pocket-sized wedge. |
| Client-demand proxies | CDC reports **2,041,926 marriages** in 2023. Census reports **714,532 employer food-service and drinking-place establishments** in 2023. | Both niches have abundant client demand. Marriages and restaurants are not photography studios, and neither measure says how many buy photography or software. |
| Product fit | The root [`README`](../README.md) says wedding-first, while [ADR 0025](adr/0025-b2b-invoicing-essentials.md) and [ADR 0026](adr/0026-decommission-albums-offers.md) deliberately deepen commercial/F&B workflows. The current [demo presets](../app/saas_demo.py) contain both stories. | Focal has a generic operating loop plus genuinely specialized commercial depth. Public copy must choose one lead without pretending the other capability vanished. |

**Reproduction.** In the 2023 U.S. Nonemployer Statistics file, select
`ST=00`, `NAICS=54192`, `LFO=-`, `RCPTOT_SIZE=001`. In the 2023 U.S. County
Business Patterns file, select `uscode=98`, `lfo=-`, and rows `54192/`,
`541921`, `541922`, or `722///`, using the `est` field. For the active
proxy, sum every published `LFO=-` receipt class from `122` upward (at least
$50k), or add `121` (at least $25k), then add `54192/ est`. The file publishes
`122,123,125,131,133` for this industry, no `141/1411` rows, and its published
size classes reconcile exactly to the `001` total. The sole-proprietor share is
`LFO=S, RCPTOT_SIZE=001 ESTAB` divided by the corresponding `LFO=-` total. The
commercial under-five share is `541922 n<5 / est = 4,555 / 5,021`. Sources were
retrieved 2026-07-17:
[Census NES](https://www.census.gov/data/datasets/2023/econ/nonemployer-statistics/2023-ns.html),
[Census CBP](https://www2.census.gov/programs-surveys/cbp/datasets/2023/),
[NES record layout](https://www2.census.gov/programs-surveys/nonemployer-statistics/technical-documentation/record-layouts/us-record-layout/us_record_layout_2017.txt),
[2022 NAICS definitions](https://www.census.gov/naics/?details=541&input=541&year=2022),
[BLS Photographers](https://www.bls.gov/ooh/media-and-communication/photographers.htm),
and [CDC marriage trends](https://www.cdc.gov/nchs/data/dvs/marriage-divorce/national-marriage-divorce-rates-00-23.pdf).

## What each choice changes

| Choice | Strongest case and fit debt | Store listing + marketing | T3 demo seed + T4 screenshots |
|---|---|---|---|
| **Wedding-first** (recommended) | Matches the stated repo direction, boutique design, native calendar/gallery/portal loop, and most complete current preset. Debt: commercial workflows and the F&B tenant site need honest secondary positioning or follow-up. | Lead with wedding CRM, retainers, timelines, proofing, booking, and delivery. Subtitle candidate: **“Wedding CRM & Galleries.”** Use one wedding story across the landing page, launch copy, and screenshots; describe commercial as “also shoots brands.” | Seed one couple, wedding date, and venue with accepted proposal, signed agreement, retainer invoice, upcoming booking, timeline tasks, populated proofing gallery, and client portal. Six shots: dashboard, pipeline, gallery, invoice, portal, calendar/reschedule. |
| **F&B-first** | Best fit with companies, PO/net terms, usage rights, AR chase, closeout, and the existing tenant site. Debt: contradicts the current repo identity and narrows the first-run story; the F&B preset is less complete today. | Lead with company CRM, content-day production, licensing, retainers, AR, and delivery closeout. Subtitle candidate: **“Commercial Photo Studio OS.”** Remove wedding-first claims rather than leaving a mixed lead. | Seed one restaurant/company and content day with accepted paperwork, usage-aware invoice, booking, tasks, deliverables, AR/closeout state, gallery, and portal. Replace one generic shot with Commercial/AR or closeout. |
| **Neutral solo studio** | Broadest promise and least niche commitment. Debt: weakest differentiation, understates the specialized code, and has no coherent neutral demo preset today. | Lead with CRM, galleries, booking, invoices, and portals. Subtitle candidate: **“CRM & Galleries for Studios.”** Remove F&B/wedding pairings; “neutral” cannot mean alternating between two niches. | Create one new brand-portrait story with a single project through every screen. Do not seed both existing presets and call that neutral. Use the same six functional shots as wedding-first with generic copy. |

Whichever option wins, the T3 replacement needs bookings, tasks, and actual
gallery assets, all with stable demo-owned identities. **Do not reuse
`bootstrap.ensure_public_showcase` for that convergence:** it can relabel unowned
ready assets and the first gallery, which violates T3's preserve-manual-data
boundary. [Issue #185](https://github.com/Ayyitskevin/mise/issues/185) keeps the
seed mechanism intentionally unspecified until Kevin approves the operator-only
identity and owned-record design.

The downstream marketing pass should reconcile the root `README`,
`templates/saas/{home,demo,pricing}.html`, `docs/{LAUNCH-KIT,BETA-LAUNCH}.md`,
the public-site templates, the OG-card source, and dual-demo copy tests. T4 owns
the final App Store worksheet and screenshot plan in
[`APP-STORE-SUBMISSION.md`](APP-STORE-SUBMISSION.md).

## Kevin decision record

Select exactly one; until then, T3/T4 content remains blocked.

- [ ] **Wedding-first** — Conductor/Codex recommendation
- [ ] **F&B-first**
- [ ] **Neutral solo studio**

**Decision date:** _pending_

**Decision note:** _pending_
