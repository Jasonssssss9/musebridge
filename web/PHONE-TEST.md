# Phone test steps

Use the Compose stack for the live phone walkthrough. The mock controls provide additional recovery scenarios during frontend development.

## Setup

1. Put the phone and the computer on the same Wi-Fi. Guest and campus networks often block device-to-device traffic; a phone hotspot works if they do.
2. Start the live stack with `PUBLIC_BASE_URL` set to the exact LAN origin, such as `http://192.168.1.20:8080`. Follow the [Compose setup guide](../docs/getting-started.md#start-the-full-local-stack-with-compose).
3. On the phone, open that LAN origin directly and check that the demo list loads.
4. On the computer, open `<network origin>/qr`. Print the page or leave it on a second screen.
5. Scan the codes with the phone camera and run the checks below.

For the optional mock checks, run `npm ci` and `npm run dev` from `web/`. Use `npm run dev:live` with the native Go and Python services if you are developing outside Compose.

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
| 8 | Type `What is the material listed by the museum?` | "Outside this demo's examples", no sources |
| 9 | Set Answer language to Chinese (Simplified) and submit a suggested question | The question and answer are in Chinese. The interface, fixture label, citation controls and source excerpt stay in English. Switch back to English for the rest of the demo. |
| 10 | Refresh the page | The same conversation is still there |
| 11 | In live mode, run `docker compose stop retrieval` and submit a supported question | The artwork stays visible. The technical failure keeps the question and offers "Try again" |
| 12 | Run `docker compose start retrieval`, wait for readiness, then tap "Try again" | The same submission completes once and its citation belongs to the current artwork |
| 13 | In mock mode, select "Slow reply", ask a question, then open the other artwork while waiting | The late answer stays in its original artwork's conversation |
| 14 | Open `<network address>/a/nope` | "This artwork link is not available" with a link to the demo list |

Typed questions must match the examples exactly. If a typed question with an apostrophe lands in "Outside this demo's examples", the phone keyboard inserted a curly apostrophe; use the suggested-question buttons instead.

## Record for the handoff

- Date, and who ran it
- Phone model, OS version and browser
- Network used, and the address encoded in the QR codes
- API mode: mock, or full stack with fixture answers and the real retriever
- Any failed step, with its number and what appeared instead
