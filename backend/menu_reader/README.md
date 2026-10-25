# Isolated Full Menu Reader MVP

This experiment reads **saved photographs** with installed Windows OCR, structures simple menu
layouts, retains evidence and answers a limited set of text questions locally. It does not open
PathOn, use Claude's live modules, send audio, guide a hand, order anything or modify provider
configuration. The real iPhone menu experience is still future integration.

## Start one demonstration

From `VisionOS/backend`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-menu-reader.ps1
```

This generates clearly **SIMULATED** menu artwork, runs REAL Windows OCR, then opens a typed
question session. It does not call Gemini. Examples:

```text
What sections are there?
What mains do they have?
What is the cheapest one?
Does Garden Pasta contain peanuts?
Does Peanut Noodles contain peanuts?
Read everything
Help me communicate with the cashier
quit
```

Use your own photo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start-menu-reader.ps1 -ImagePath "C:\photos\menu.jpg"
```

For multiple photos of the SAME menu, or an explicitly partial capture:

```powershell
python -m menu_reader.cli "page1.jpg" "page2.jpg" --coverage partial --out .runtime/my-menu --interactive
```

Exact duplicate entries merge with all source-page IDs retained. Different prices/descriptions
do not merge. Different visible titles create a scope warning; `confirm same menu` records the
user's explicit clarification before comparing their prices. No operation declares the scan
complete. Pages with no visible title cannot be proven to belong to the same restaurant.

## Dependencies and output

Pillow is already used by Reflex. `psutil` is optional resource monitoring. If needed:

```powershell
python -m pip install -r requirements-menu-reader.txt
```

The Windows adapter uses Windows PowerShell 5.1, .NET/WinRT and the installed en-US OCR language
pack. No OCR models were installed. It worked outside the Codex sandbox on this host; sandbox
WinRT file access was denied. Microsoft's documented desktop OCR deployment support requires
MSIX/package identity: this PowerShell route is an experimental host adapter, not a supported
deployment guarantee. Missing languages/permissions produce an explicit error; no cloud fallback.
For other platforms, use `--ocr-json` to parse saved evidence or the separate Swift Vision proof.

`.runtime/menu-demo/` contains:

- `captures/*upright.png`: original resolution after EXIF correction.
- `captures/*tile*.png`: overlapping <=2400px tiles, preserving resolution for large photographs.
- `*-ocr.json`: exact words, upright pixel boxes, null independent word confidence, source IDs,
  orientation/dimensions, tile offsets and measured timing/resources.
- `menu.json`: sections, priced entries, descriptions/printed notes, source IDs, unresolved rows,
  coverage warnings and `complete=false`.
- `readout.txt`: chunkable text for the existing speech scheduler. No audio is played by this tool.

Each question returns text/status/source IDs. Price comparisons are PROVISIONAL while OCR facts
are unreviewed; `review <item-id>` explicitly confirms a particular name/price pair. Missing or
mixed currencies prevent comparison. Allergen answers quote only associated captured text,
otherwise abstain and suggest asking staff; a printed claim never confirms current preparation
or cross-contact. Prices, descriptions and ingredients are never filled from background knowledge.

Reading cursor commands: `next`, `pause`, `resume`, `skip`, `stop`. This tests text pagination,
not microphone barge-in. Order communication is a draft until `confirm order`; even then the
tool only returns text for a future explicitly authorized speech dispatcher. It never contacts
staff, places orders or spends money.

## Evaluation

```powershell
python -m unittest discover -s tests/menu_reader -v
python -m menu_reader.evaluate --out .runtime/menu-eval
```

The latter runs actual Windows OCR on seven generated images: printed menu, two-column menu,
kiosk, elevator, small text, partial capture, similar names with different prices. The report
records normalized character error, token precision/recall, exact price pairing, missing/unexpected
pairs, section grouping, first-readable/structured timings, sampled child RSS/CPU, and grounded QA.
The artwork is controlled and SIMULATED; neither optical camera conditions nor live users are tested.

One opt-in matched full-image Gemini benchmark is provided separately:

```powershell
python -m menu_reader.gemini_baseline .runtime/menu-eval/images/printed_menu.png --truth .runtime/menu-eval/truth.json --allow-api
```

It uses existing environment/.env credentials and model selection without modifying them, and
makes one paid request. Without `--allow-api`, no request occurs. This implementation run could
not execute it because key/model configuration was unavailable to the process; no cloud timing
or accuracy result is claimed. It uses its OWN full-text prompt, not the control-only prompt.

## Honest limits and native next step

This is a conservative layout baseline, not a universal menu-understanding model. Same-row
prices and simple one/two-column layouts work in the controlled set. Detached price columns,
multiple prices on one line, curved/stylized text, unusual currencies and unreadable tokens stay
unresolved. Similar-name controls are not silently collapsed. Arbitrary semantic questions are
not implemented; unsupported questions receive an explicit response. Missing OCR detections
cannot be identified reliably from the returned text alone.

Use native Vision on owner-supplied full-resolution iPhone snapshots next; see
`ios/Packages/Targets/Prototypes/MenuOCRProof.swift` and its README. The proof is **uncompiled,
unwired and untested** on this Windows host. It preserves orientation/frame metadata, exact text
and actual line-candidate confidence. The Python parser accepts its JSON shape; no native app
code or shared contract was changed.

Reuse R1's camera/audio scheduler, the existing authenticated text service boundary and R2's
menu/task state. Store historical menu evidence separately from fresh physical guidance evidence.
Add validated complex layout parsing/Gemini text assistance, multi-page coverage, production
speech routing and reading interruption before claiming an everyday native menu assistant.
