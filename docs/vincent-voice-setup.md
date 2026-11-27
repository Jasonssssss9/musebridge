# Vincent voice: setup and status (A)

This is A's implementation of [Talk with Vincent](vincent-contract.md) and its [voice contract](vincent-voice.md). It covers the mode-aware Go conversation API, the private Whisper worker, the two Go media routes, server-side speech synthesis and the browser voice controller that B's page calls. It also explains how to start everything locally and run the iPhone check.

## Status

| Part | State |
| --- | --- |
| Vincent conversations in Go (`agent-demo-v0.4`) | Implemented. Create, restore, history and submit carry `conversation_mode` and `reference_pack_version`. Guide, Vincent and the old v0.3 history are separate conversations. The artwork cards list `conversation_modes`. |
| Go → Agent v3 (`agent-run-v3`) | Implemented. Every v0.4 conversation, guide or Vincent, runs on `/internal/v3/agent/runs` with its frozen scope. Go checks the returned scope and language. It also checks every source against the pinned gallery release and the [Vincent reference pack](../knowledge/data/reference-packs/vincent-irises-v1.json) before it saves the turn. |
| Whisper worker (`whisper_worker/`) | Implemented. Tested with a stand-in recognizer. Not yet run with the real faster-whisper model. |
| Go media routes (`api/internal/voice`) | Implemented. Transcription, speech, the error table, coalescing, the cache and the busy limits are tested. They now serve real Vincent conversations. |
| Browser voice controller (`web/src/voice`) | Implemented. The two reported bugs are fixed: under StrictMode the first Record tap opens the microphone once, and the most recent Play or Stop always wins. A recording is bound to its message ID and revision when the visitor taps Record. |
| v0.4 browser client and device-check page | `web/src/voice/vincentClient.ts` and `web/voice-check.html`. A uses them to test on the phone before B's page exists. |
| Vincent page and portrait | B. The page uses the controller and client below. |
| Real-device acceptance | **Not done yet.** It needs the real Whisper model, C's live v3 Agent with Qwen, a real OpenAI key and an iPhone. Use the record at the end of this page. |

A browser run checked the whole chain: Chromium with a fake microphone, the device-check page, Go and stand-in Whisper, Agent and TTS services. The run covered two voice turns in the same conversation, Stop, replay, recording again and restoring after a reload. This shows the pieces connect. It does not replace the iPhone test.

The Go code was compiled and tested with Go 1.24 instead of the project's 1.27. Schema checks ran through Python's `jsonschema` instead of the pinned Go validator, because module downloads were unavailable. Before merging, run `go test -race ./...` in `api/`, `npm test` in `web/` and `python3 scripts/generate_agent_contracts.py --check` with the project toolchain.

## What changed for each teammate

**B (page).** Create the Vincent conversation with `{"contract_version":"agent-demo-v0.4","artwork_id":"getty-103jnh","conversation_mode":"vincent"}`, or call `createVincentClient().open()`. Show Talk with Vincent only when the artwork card's `conversation_modes` includes `"vincent"`. The existing chat client still speaks v0.3 and rejects v0.4 conversations. Either extend it or use `vincentClient.ts` on the Vincent page.

**C (Agent).** Go sends `agent-run-v3` to `POST /internal/v3/agent/runs` for every v0.4 conversation, including guide. The Agent therefore needs `MUSEBRIDGE_ENABLE_VINCENT=1`; `run-gallery.sh` sets it when `VINCENT_ENABLED=true`. Go rejects a result in these cases: its scope differs from the run's, a Vincent answer is not in English, or a source is not an exact pinned record. A pack record cited in guide mode, or a cached record left out without the v3 eviction reason, is also rejected. All of these give `502 agent_invalid_response` and no new revision.

**Contracts.** `scripts/generate_agent_contracts.py` now also applies `agent-v3.patch.json`. It embeds the effective v3 schema and a copy of the reference pack, checked against its pinned SHA-256, in `api/internal/agentcontract/`. The Go types gain v3-only fields as `Optional`. Python `models.py` is unchanged.

## Start voice locally

1. **Install the worker.** From the repository root:

   ```bash
   python3 -m venv .venv-whisper
   .venv-whisper/bin/pip install -r whisper_worker/requirements.txt
   ```

   The first start downloads the `small.en` model, about 500 MB. Set `WHISPER_MODEL=base.en` for a smaller and faster model that is less accurate.

2. **Open an HTTPS entry.** Safari grants the microphone only on a trusted HTTPS page. Point a tunnel at the frontend port:

   ```bash
   cloudflared tunnel --url http://localhost:8080
   ```

   Copy the `https://…trycloudflare.com` address it prints. The address changes each time the tunnel restarts.

3. **Start the gallery with Vincent.** Set the gallery variables from [the Getty guide](getty-gallery.md), then add these:

   ```bash
   export PUBLIC_BASE_URL=https://<your-tunnel>.trycloudflare.com
   export VINCENT_ENABLED=true
   export WHISPER_PYTHON=.venv-whisper/bin/python
   export OPENAI_API_KEY_FILE="$HOME/.config/musebridge/openai.key"
   ./scripts/run-gallery.sh
   ```

   The script starts the worker on `127.0.0.1:8003`, enables the v3 Agent and Vincent retrieval, gives the speech key only to Go and lets Vite accept the tunnel's hostname. Check that `http://127.0.0.1:8003/ready` returns `{"ready":true}` once the model has loaded, and that `GET /internal/v3/agent/readyz` on the Agent is ready ([Vincent hybrid](vincent-hybrid.md)).

4. **Open the device-check page on the phone:** `https://<your-tunnel>.trycloudflare.com/voice-check.html`. Use the same HTTPS address for every call, so the cookie and the allowed origin match. When B's page is ready, use `/a/getty-103jnh/vincent` instead.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `VINCENT_ENABLED` | `false` | Offers the `vincent` mode on Irises (`getty-gallery-v2`) and mounts the two voice routes. Requires `GENERATION_MODE=agent` and an HTTPS (or localhost) `PUBLIC_BASE_URL`. v0.4 guide conversations work without it. |
| `MUSEBRIDGE_ENABLE_VINCENT` | `1` when `VINCENT_ENABLED=true` | Agent and knowledge side: the private v3 routes and Vincent retrieval. |
| `WHISPER_BASE_URL` | none | Private worker origin. It must be a loopback address. Forward the port if the worker runs on another computer. |
| `OPENAI_API_KEY` | none | Server-side speech key. It is never sent to the browser or the worker. Keep it in a file outside the repository. |
| `VINCENT_TTS_VOICE` | `cedar` | Voice chosen with C after listening on the target iPhone. |
| `VINCENT_TTS_INSTRUCTIONS` | contract text | Delivery direction. Changing it changes the cache key. |
| `VINCENT_TTS_CONCURRENCY` | `2` | Paid syntheses in flight. Further requests for different turns get `429 voice_busy`. |
| `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE` | `small.en`, `cpu`, `int8` | Worker model settings. CPU keeps recognition off the Qwen GPU. |

When voice is enabled, Go allows 35 seconds to read a request body, so a phone can upload a 3 MiB clip through the tunnel.

## Using the controller in the Vincent page

```tsx
import { useVincentVoice } from '../voice/useVincentVoice';
import { createVincentClient } from '../voice/vincentClient';

const client = createVincentClient();
const { voice, snapshot } = useVincentVoice(conversation.conversation_id, {
  revision: () => conversation.revision,           // read when Record is tapped
  submit: async (message) => {                     // one logical message
    const { conversation: next, turn } = await client.submit(conversation.conversation_id, message);
    setConversation(next);
    addTurn(turn);                                 // show turn.answer.text and citations
    return { turnId: turn.turn_id };
  },
  welcomeUrl: '/vincent/welcome.mp3',              // optional
});
```

- **Binding.** When Record is tapped, the controller fixes `clientMessageId` and `expectedRevision`. The transcript is submitted once as `{question, clientMessageId, expectedRevision, languageOverride: null}`.
- **Lost response.** `client.submit` reads history and returns the committed turn with the same message ID before it sends the identical message again. If `submit` still rejects with a retryable error, show Retry and call `voice.retryMessage()`, which resends the same message.
- **Revision conflict.** On `conversation_state_conflict` (or any non-retryable message error), the words stay in `snapshot.unsent`. Show them for review. Sending them again is a new message with a new ID at the current revision. A fresh recording is always a new question.
- **Controls.** Call `voice.playWelcome()` once on entry. Record and Stop call `startRecording()` and `stopRecording()`. Use `retryTranscription()` when `snapshot.error.stage === 'transcription'` and the error is retryable. Stop during an answer calls `stopPlayback()`, which also cancels an answer that is still downloading. Play and Replay call `playTurn(turnId)`, and so does the retry after a speech failure. The newest Play or Stop always wins.
- **States.** `snapshot.phase` is `ready`, `recording`, `transcribing`, `generating`, `preparing_audio`, `playing`, `awaiting_play` or `error`. `playing` starts on the audio element's real `playing` event. `awaiting_play` means Safari blocked autoplay, so show a Play button that calls `playTurn(snapshot.turnId)`.
- **Text.** Read the answer text and sources from the committed turn, not from the controller. Speech reads the same saved `turn.answer.text`. Retrying speech or replaying never changes the turn count or calls Qwen. If speech fails, keep the text visible and let the visitor type. If `microphone_denied` occurs, show `snapshot.error.message` and a text box.
- **StrictMode.** The hook creates a fresh controller if React's StrictMode cleanup disposed the first one. Leaving the page or changing the conversation ID releases the microphone and silences speech.

## iPhone test record

Fill this in on the device and send it with the commit hash and test results. Mark each step **pass**, **fail** or **not run**, and note which real services were used.

| Step | What to check | Real Whisper | Real Qwen (C's v3 Agent) | Real OpenAI TTS | Result and notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Open the HTTPS page and enter Talk with Vincent; the microphone prompt appears and Record works on the first tap | | | | |
| 2 | Record “What color did you call the irises?”: the transcript matches, exactly one turn appears, and the audio reads the displayed answer | | | | |
| 3 | Record “Then why do they look blue today?”: the same conversation gets turn 2 with its own answer, sources and audio | | | | |
| 4 | Stop during playback, replay the first answer, then record again: the right clip plays, and the new recording has only the visitor's voice | | | | |
| 5 | Blocked autoplay shows a working Play button; with the speech key removed, the saved text and sources stay visible and the turn count does not change | | | | |

Automated results to attach: `go test -race ./...`, `npm test`, `python3 scripts/generate_agent_contracts.py --check` and `python3 -m unittest discover -s whisper_worker/tests -t .`.
