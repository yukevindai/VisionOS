# Real photographed-menu evaluation sources

Collected October 24, 2026 for the isolated menu-reader follow-up. No model/OCR output was used
to invent ground truth. Three genuine photographed menus were downloaded from Wikimedia Commons;
their source pages identify photographer Andy Li (Commons user Onthewings), camera metadata and
CC0 public-domain dedication. These are historical photographed menus, not current prices or a
live Reflex camera test. No active tracking, ALL OBJECTS, hand, guidance, depth or audio files changed.

## Sources and local originals

| ID | Photo | Scope/use | Primary source |
| --- | --- | --- | --- |
| `fatto` | Fatto a Mano Pizzeria main menu, October 18, 2022 | Printed two-column menu with italic small descriptions, table/background, perspective and visible hand. Original raw 4032×3024, EXIF 6; upright 3024×4032. | [Commons description, author, CC0 and camera metadata](https://commons.wikimedia.org/wiki/File:Fatto_a_Mano_Pizzeria_main_menu_(Oct_2022).jpg) |
| `mange` | Mange Tout brunch menu, April 6, 2023 | Printed mixed French/English menu with low-contrast fine descriptions, variants and bare-number prices. Original raw 4032×3024, EXIF 6; upright 3024×4032. | [Commons description, author, CC0 and camera metadata](https://commons.wikimedia.org/wiki/File:Menu_(Brunch)_-_Mange_Tout.jpg) |
| `garden` | The Garden Café menu, May 21, 2023 | Outdoor handwritten multi-column blackboard, small text, faded/erased prices. Original/upright 4032×3024, EXIF 1. Useful failure/missing-text case, not a price-pair benchmark. | [Commons description, author, CC0 and camera metadata](https://commons.wikimedia.org/wiki/File:Menu_of_The_Garden_Caf%C3%A9_2023-05-21.jpg) |

The source pages all state [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/). Attribution
is retained despite the dedication: **photographs by Andy Li / Onthewings, via Wikimedia Commons**.
The originals are in ignored `backend/.runtime/menu-real/images/{fatto,mange,garden}.jpg`.
`backend/.runtime/menu-real/sources.json` retains primary page/original download URLs, original
filenames, SHA-256 digests, author/license, raw and EXIF-upright dimensions. No photo is relabeled
as an iPhone capture or newly photographed restaurant visit.

## Manually inspected truth subset

`backend/.runtime/menu-real/truth.json` uses top-level `photos[]`. Each photo has `items[]` and
`regions[]`; boxes are `[x,y,width,height]` in **EXIF-upright original image pixels**, not preview
pixels or raw sensor orientation. Entries were visually transcribed from the actual images and
then checked in an independently generated crop contact sheet. Typography/diacritics and literal
decimal strings are retained; normalization of whitespace/case can be reported separately.

- Fatto: 12 named PIZZA rows with literal prices, plus 3 exact row/description text regions.
- Mange Tout: 13 brunch item/variant rows with literal prices, plus 3 exact row/description regions.
- Garden Café: 2 readable handwritten text regions. **No item-price ground truth** is claimed:
  prices are faint/erased and not independently legible. This is intentional abstention.

The printed menus use bare numeric prices, including integer `7`, single-decimal `8.5`, and
variant `11.5`; the photographed page does not print a currency symbol. Ground truth stores
`currency: null`. Do not infer GBP just because the restaurant is in England. A comparison can
say "printed price 8.5, currency not shown", or require the user to confirm currency before a
monetary cheapest-item claim. Tags such as `(v)` and `(vg)` and punctuation such as the separator
dash are retained in `rawLine`; named-item fields separately identify the printed dish/variant.

This is **partial annotation**, not a complete-menu inventory. Score annotated crops or annotated
named rows only. Legitimate unannotated menu items must not be counted as invented predictions.
Missing annotated text can be measured; detecting all missed text in arbitrary photos remains
unproven. Names/prices that cannot be independently read must be excluded with a reason, not
filled from OCR, restaurant websites, menu databases or guessed ingredients.

`images/inspection-regions.png` is a local contact sheet for checking annotation crop placement;
it is not an additional photographed source or evaluation input. Crops only support inspection.

## Suitable end-to-end example

Fatto is a useful real-photo demonstration: identify the PIZZA section, list named pizzas and
literal prices, ask the printed price of MARGHERITA (`8.5`), and produce a grounded speech-ready
response with currency explicitly unshown. It can expose parser gaps rather than hide them:
two columns, unprefixed integer/single-decimal prices, tags and long italic descriptions all
occur naturally. Mange Tout adds variants and lower-contrast text. Garden Café tests refusal
and missing text under much harder handwriting/contrast conditions.
