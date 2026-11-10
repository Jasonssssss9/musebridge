# Phone test steps

A repeatable check of the visitor flow on a real phone. Run the UI scenarios against the mock API (`npm run dev`), then repeat the applicable journey against Go and Python using `npm run dev:live`. Compose packaging is still planned; the services can currently be started separately.

## Setup

1. Put the phone and the computer on the same Wi-Fi. Guest and campus networks often block device-to-device traffic; a phone hotspot works if they do.
2. In `web/`, run `npm install` once, then `npm run dev`.
3. Note the "Network" address Vite prints, for example `http://192.168.1.20:8080`.
4. On the computer, open `<network address>/qr`. Print the page or leave it on screen.
5. On the phone, type `<network address>` into the browser first. If the computer loads it and the phone does not, the problem is the network or the computer's firewall, not the app.

For live mode, start Python and Go using the [setup guide](../docs/getting-started.md), set Go's `PUBLIC_BASE_URL` to the exact network origin and run `npm run dev:live` instead. The Mock API controls in steps 11–13 are available only in mock mode. Record live dependency failures separately using the integration plan; changing a mock setting does not change the real services.

## Checks

| # | Step | Expected |
| --- | --- | --- |
| 1 | Scan the QR for The Bedroom | The Bedroom page opens: title, Vincent van Gogh, 1889 |
| 2 | Scan the QR for Woman at Her Toilette | That work's page opens |
| 3 | On The Bedroom, tap "What material is this artwork made of?" | Answer with the "Demo response — fixed test answer" label and one source, excerpt "Oil on canvas" |
| 4 | Tap "Open original source" | The museum record opens in a new tab; going back keeps the conversation |
| 5 | Type `What about the brushwork?` | Answer about thick, broken brushwork, citing The Bedroom's description |
| 6 | Ask the brushwork example on Woman at Her Toilette | A different answer, citing that work's own description |
| 7 | On The Bedroom, type `How many hours did Van Gogh actually spend painting the Chicago version?` | "Not enough evidence", no sources |
| 8 | Type `Why did the artist choose this material?` | "Outside this demo's examples", no sources |
| 9 | Switch the language to 简体中文 and tap a suggested question | Interface and answer in Chinese; the source excerpt stays in English |
| 10 | Refresh the page | The same conversation is still there |
| 11 | Mock API pill → "503 retrieval_unavailable", then ask a question | Red failure box, question kept, "Try again" shown |
| 12 | Refresh, set the pill back to "Normal replies", tap "Try again" | The answer appears once |
| 13 | Mock API pill → "Slow reply", ask, then open the other artwork while waiting | The late answer never shows on the other artwork; it is in the first one when you go back |
| 14 | Open `<network address>/a/nope` | "This artwork link is not available" with a link to the demo list |

Typed questions must match the examples exactly. If a typed question with an apostrophe lands in "Outside this demo's examples", the phone keyboard inserted a curly apostrophe; use the suggested-question buttons instead.

## Record for the handoff

- Date, and who ran it
- Phone model, OS version and browser
- Network used, and the address encoded in the QR codes
- API mode: mock, or full stack with fixture answers and the real retriever
- Any failed step, with its number and what appeared instead
