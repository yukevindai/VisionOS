# Kiosk screen steps (R3 → R2)

Status: proposal from R3, additive and optional. Owner: R3. Consumer: R2 (`TaskEngine` via `R3TargetMapping`).

R2's task engine already stops at payment, reports `KIOSK_RESET` and detects screen changes. It needs
three facts the R3 `ScreenModel` did not carry. R3 now adds them to every vision read. All fields are
optional, so older readers ignore them (Swift `Codable` skips unknown keys).

| Field | Type | Meaning |
|---|---|---|
| `screenKind` | `welcome` · `menu` · `itemOptions` · `upsell` · `cart` · `payment` · `confirmation` · `other` · `unknown` | Which kiosk step the screen shows. `unknown` when Gemini gave no valid kind or the read failed. |
| `isWelcomeScreen` | bool | `screenKind == "welcome"`. Lets R2 report `KIOSK_RESET` when it reappears. |
| `isPaymentScreen` | bool (existing) | Now also `true` when `screenKind == "payment"`. Either signal stops the task (S7). |
| `orderTotal` | string, optional | The total exactly as printed. Never computed or rounded. Absent if no total is visible. |
| `screenSignature` | `scr-` + 16 hex, optional | Same value for re-reads of the same screen (camera moves, prices and boxes are ignored). Different when the step or the clearly read labels change. Absent for partial or failed reads. |

## Example

```json
{ "frameId": "f-42", "panelType": "orderingKiosk", "availability": "available",
  "screenKind": "cart", "isWelcomeScreen": false, "isPaymentScreen": false,
  "orderTotal": "$7.48", "screenSignature": "scr-3f2a9c1d0b7e4a55", "controls": [ … ] }
```

## Proposed mapping in `R3TargetMapping.swift` (R2's file, not changed by R3)

| `TaskScreen` | From |
|---|---|
| `id` | `screenSignature` when present, else `frameId` (current behaviour) |
| `isWelcomeScreen` | `isWelcomeScreen` |
| `isPaymentScreen` | `isPaymentScreen` |
| `total` | `orderTotal` |
| `title` | `screenKind` |

## Limits

- These are model readings, not proof. They never confirm a tap, payment or success (S4, S6).
- A missing `screenSignature` means "cannot tell"; it must not be treated as "same screen".
- Fixture for R2 tests: `fixtures/SIMULATED_kiosk_screen_sequence.json` (welcome → menu → itemOptions →
  upsell → cart → payment → reset). SIMULATED; not from a real kiosk or the Gemini API.
