# MuseBridge web app

The mobile visitor interface: scan an artwork's QR code, open `/a/{slug}`, ask a question, and check the answer against the museum source. React and TypeScript, built with Vite. Owner: Jeremiah Lim (plan tasks A1–A7).

It implements the browser side of [the API contract](../docs/api-contract.md), revision `local-demo-v0.1`.

## Run it

Requires Node 20 or later.

```bash
cd web
npm install
npm run dev
```

Open <http://localhost:8080/a/aic-28560> or <http://localhost:8080/a/aic-11723>. The dev server also listens on your LAN address, so a phone on the same network can open `http://<your-ip>:8080/a/aic-28560`.

| Command | API used |
| --- | --- |
| `npm run dev` | **Mock mode.** An in-browser stand-in for the Go API (`src/api/mock.ts`). No backend needed. |
| `npm run dev:live` | **Live mode.** `/api` is proxied to the Go API at `http://127.0.0.1:8000` (override with `API_TARGET`). |
| `npm run build` | Type-checks and builds the live app into `dist/`. |

Live mode loads artwork, creates or restores real Go conversations, reads history and submits questions through Go to the Python retriever. Start both backend services using the [setup guide](../docs/getting-started.md). Go returns reviewed fixture answers with validated museum citations; all three outcomes retain the fixed-test-answer label. Browser and physical-phone acceptance still need to be recorded against the real stack. The Go API must be started with `PUBLIC_BASE_URL` set to the exact origin in the browser's address bar (for example `http://192.168.1.20:8080`), because it rejects POSTs from any other origin.

## QR cards and the phone test

`/qr` shows a printable card for each artwork. The codes are built from the address the page was opened at, so open it through your LAN address (Vite prints it as "Network" on startup), not `localhost`. The step-by-step phone check is in [PHONE-TEST.md](PHONE-TEST.md).

## Mock mode

Mock mode shows a "Mock API" pill in the top bar. Tap it to choose what the next question gets:

| Setting | What it exercises |
| --- | --- |
| Normal replies | The three outcomes: answered, not enough evidence, outside the examples |
| Slow reply (4 s) | Pending state; reload or switch artworks while waiting |
| 503 / 504 | Retryable failure: "Try again" resends the same UUID and payload |
| 502 | Non-retryable service fault: shows the request ID, offers "Edit question" only |
| Answer saved, reply lost | Lost success response: recovered from history, shown once |
| Server takes 15 s | The 12-second browser timeout, then polling history |
| Server restarted | `404 conversation_not_found`: new conversation, question kept unsent |

The mock keeps its "server" state in `localStorage` so it survives reloads like the real API does. "Reset mock server" clears it. Its answers copy the fixture text in contract section 6; it matches questions exactly as the contract says, so typed wording outside the listed examples returns "outside this demo's examples".

## Layout

| Path | Purpose |
| --- | --- |
| `src/api/types.ts` | Wire types and response validation |
| `src/api/client.ts` | The four API calls, 12 s timeout, error classes |
| `src/api/uuid.ts` | UUID v4 from `crypto.getRandomValues` (works on plain HTTP) |
| `src/api/mock.ts`, `mockData.ts` | Mock API and its copied fixtures (mock mode only) |
| `src/chat/useConversation.ts` | Conversation state: create/restore, submit, retry, reload recovery |
| `src/chat/drafts.ts` | The pending submission saved in `sessionStorage` |
| `src/qr.ts` | QR encoder for the demo cards (no extra package) |
| `src/i18n.ts` | English and Simplified Chinese interface text |
| `src/ui/` | Screens and components |

## Contract behaviors to keep when editing

- A retry reuses the original `client_message_id`, question and language. Editing a failed question creates a new UUID.
- Nothing is re-sent automatically. After a timeout or a `409`, the app reads history (polling once a second for at most 12 seconds) and then offers a manual action.
- A reply is rendered only on the artwork page that asked for it.
- Technical failures are never worded as the museum lacking information.
- Every answer, including the two fallback outcomes, carries the fixed-test-answer label.
- Only stored `https:` source and license URLs become links; answer and excerpt text is rendered as text.
