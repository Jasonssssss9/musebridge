# MuseBridge web app

The mobile visitor interface: scan an artwork's QR code, open `/a/{slug}`, ask a question, and check the answer against the museum source. React and TypeScript, built with Vite. Owner: Jeremiah Lim (role A in the [Agent plan](../docs/agent-plan.md)).

The browser can speak two public contracts:

| Contract | Document | Used by |
| --- | --- | --- |
| `local-demo-v0.1` (fixture answers) | [api-contract.md](../docs/api-contract.md) | Live builds by default, so the current Go API and Compose stack keep working |
| `agent-demo-v0.3` (model answers) | [agent-api-contract.md](../docs/agent-api-contract.md) | Mock mode always; live builds when started with `PUBLIC_CONTRACT=agent` |

Agent-contract status (M1, role A): the types, parsing, mock and rendering are done and checked against the mock only. Nothing here has run against a real Go agent endpoint or a real model yet; that is M2.

## Run it

Use the [Compose startup guide](../docs/getting-started.md#start-the-full-local-stack-with-compose) for the full local demo. The commands below run the frontend directly for development.

Requires Node 20 or later.

```bash
cd web
npm install
npm run dev
```

Open <http://localhost:8080/a/aic-28560> or <http://localhost:8080/a/aic-11723>. The dev server also listens on your LAN address, so a phone on the same network can open `http://<your-ip>:8080/a/aic-28560`.

| Command | API used |
| --- | --- |
| `npm run dev` | **Mock mode, agent contract.** An in-browser stand-in for the Go API (`src/api/mock.ts`). No backend needed. |
| `npm run dev:live` | **Live mode, fixture contract.** `/api` is proxied to the Go API at `http://127.0.0.1:8000` (override with `API_TARGET`). |
| `PUBLIC_CONTRACT=agent npm run dev:live` | **Live mode, agent contract.** For the Go API once it serves `agent-demo-v0.3`. |
| `npm run build` | Type-checks and builds the live app into `dist/` (fixture contract unless `PUBLIC_CONTRACT=agent`). |
| `npm test` | Client checks, including the shared public examples in `docs/contracts/agent-v2.examples.json`. |

Live mode loads artwork, creates or restores real Go conversations, reads history and submits questions through Go to the Python retriever. Start both backend services using the [setup guide](../docs/getting-started.md). Go returns reviewed fixture answers with validated museum citations; all three outcomes retain the fixed-test-answer label. Browser and physical-phone acceptance still need to be recorded against the real stack. The Go API must be started with `PUBLIC_BASE_URL` set to the exact origin in the browser's address bar (for example `http://192.168.1.20:8080`), because it rejects POSTs from any other origin.

The interface labels are English. The **Answer language** selector sits under the question box:

- **Agent contract:** Auto, English, Español, 简体中文 and 한국어. The server stores the preference per conversation. A new selection is sent once, as `language_override` on the next message; after that the page sends `null` and shows the server's stored value. Auto follows the language the visitor writes in.
- **Fixture contract:** English or Simplified Chinese, remembered in the browser and sent with every question.

Citations keep the museum's original text.

## What the agent contract changes on the page

| Area | Behavior |
| --- | --- |
| Answer statuses | `answered`, `partially_answered`, `clarification_needed`, `insufficient`, `out_of_scope` and `conversational`. The reply text is always shown in full; the status adds only a short note such as "Partly answered". |
| Label | Model answers show "AI guide response" and the language they were written in. Fixture answers keep the fixed-test-answer label. |
| Sources | A retrieval passage is shown as a quotation. A catalog fact (title, artist, date) is shown as a "Catalog record" with its field and value, never as a quotation. |
| Revisions | Every message carries the `expected_revision` the visitor was looking at. On `409 conversation_state_conflict` the page reloads history, puts the text back in the box and waits for the visitor to send it again. |
| Waiting | One 70-second budget covers the request and any recovery polling. After 8 seconds the page says answers can take up to a minute. |
| Unknown values | A reply with an unknown status, mode, contract version or malformed source is treated as a service error, not shown as an answer. |

## QR cards and the phone test

`/qr` shows a printable card for each artwork. The codes are built from the address the page was opened at, so open it through your LAN address (Vite prints it as "Network" on startup), not `localhost`. The step-by-step phone check is in [PHONE-TEST.md](PHONE-TEST.md).

## Mock mode

Mock mode shows a "Mock API" pill in the top bar. Tap it to choose what the next question gets:

| Setting | What it exercises |
| --- | --- |
| Normal replies | All six statuses, mixed answer-plus-clarification, catalog and passage sources, four languages |
| Slow reply (9 s) | Pending state and the long-wait message; reload or switch artworks while waiting |
| 503 / 504 | Retryable failure: "Try again" resends the same UUID, language choice and revision |
| 502 | Non-retryable service fault: shows the request ID, offers "Edit question" only |
| Answer saved, reply lost | Lost success response: recovered from history, shown once |
| Server takes 75 s | The 70-second browser wait, then the manual "Check again" action |
| Server restarted | `404 conversation_not_found`: new conversation, question kept unsent |
| Another tab answered first | `409 conversation_state_conflict`: history reloaded, text handed back, nothing resent |

The mock keeps its "server" state in `localStorage` so it survives reloads like the real API does. "Reset mock server" clears it. A line under the question box shows the conversation's revision and language settings.

The mock is a keyword script, not a model. It exists to show every reply shape, and its wording is sample text in four languages. It says nothing about real answer quality. Things to type:

| Message | Result |
| --- | --- |
| `Who made it, and when?` | Answered from two catalog records |
| `What is it made of, and what is that little object for?` | Partly answered: a sourced fact plus one clarifying question |
| `What is that thing for?` | Clarification needed |
| `How many hours did it take to paint?` | Not confirmed by the museum record |
| `What's the weather today?` | Outside this guide |
| `Thanks` | Conversational, no sources |
| `¿De qué material está hecha esta obra?` (with Auto) | Answer in Spanish |
| `Translate that into English` | The previous reply in English, without changing the saved preference |
| `Answer in Spanish from now on` | Saves Spanish as the preference |

## Layout

| Path | Purpose |
| --- | --- |
| `src/api/types.ts` | Wire types and response validation |
| `src/api/client.ts` | The four API calls for both contracts, wait budgets, error classes |
| `src/api/uuid.ts` | UUID v4 from `crypto.getRandomValues` (works on plain HTTP) |
| `src/api/mock.ts`, `mockData.ts` | Mock agent-contract API and its sample data (mock mode only) |
| `src/chat/useConversation.ts` | Conversation state: create/restore, submit, retry, reload recovery |
| `src/chat/drafts.ts` | The pending submission saved in `sessionStorage` |
| `src/qr.ts` | QR encoder for the demo cards (no extra package) |
| `src/i18n.ts` | English interface text, language labels, and suggested questions in four languages |
| `src/ui/` | Screens and components |

## Contract behaviors to keep when editing

- A retry reuses the original `client_message_id`, question, language choice and `expected_revision`. Editing a failed question creates a new UUID.
- Nothing is re-sent automatically. After a timeout or a `409`, the app reads history (polling once a second within the wait budget: 12 seconds for fixture, 70 for agent) and then offers a manual action.
- The page never rolls its conversation back to an older revision, and never resends a message against a newer one.
- A reply is rendered only on the artwork page that asked for it.
- Technical failures are never worded as the museum lacking information.
- Every fixture answer carries the fixed-test-answer label; every model answer carries the AI label.
- Only stored `https:` source and license URLs become links; answer and excerpt text is rendered as text.

## Open items for integration (M2)

- The Compose proxy (`nginx.conf`) still allows 15 seconds per API response. The agent contract needs 75 seconds there; the Vite dev proxy is already set to 75.
- The default Docker build speaks the fixture contract. Build with `PUBLIC_CONTRACT=agent` once Go serves `agent-demo-v0.3`.
- The label wording for model answers ("AI guide response") is a placeholder until the team agrees on one.
- There is no reset or new-visitor control; the contract defers it.
