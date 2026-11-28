# Vincent voice: setup and status (A)

This is A's implementation of the [voice contract](vincent-voice.md): the private Whisper worker, the two Go media routes, server-side speech synthesis and the browser voice controller B's page calls. It covers how to start voice locally and what is still open.

## Status

| Part | State |
| --- | --- |
| Whisper worker (`whisper_worker/`) | Implemented. Tested with a stand-in recognizer; not yet run with the real faster-whisper model. |
| Go media routes (`api/internal/voice`) | Implemented and wired behind `VINCENT_ENABLED`. Transcription, speech, error table, coalescing, cache and busy limits are tested. |
| Browser voice controller (`web/src/voice`) | Implemented with the method and snapshot names from the contract. Tested with fake microphone, recorder and audio; not yet run on an iPhone. |
| Vincent conversations (v0.4 create and submit, Go→Agent v3) | **Not implemented.** Until a conversation can be created in Vincent mode, every voice request returns `409 voice_mode_unavailable`. This depends on C's v3 Agent endpoint. |
| Vincent page and portrait | B's [mobile layout, silent greeting, listening portrait and development preview](../web/README.md#talk-with-vincent-entry) are implemented. Live controls remain disabled; the v0.4 client, controller binding and physical iPhone acceptance are still pending. |

The Go code was compiled and tested here with an older Go than the project's 1.27 and with stand-ins for two libraries, because module downloads were unavailable. Run `go test ./...` in `api/` with the project toolchain before merging.

## Start voice locally

1. **Install the worker.** From the repository root:

   ```bash
   python3 -m venv .venv-whisper
   .venv-whisper/bin/pip install -r whisper_worker/requirements.txt
   ```

   The first start downloads the `small.en` model (about 500 MB). Set `WHISPER_MODEL=base.en` for a smaller, faster and less accurate model.

2. **Open an HTTPS entry.** Safari only grants the microphone on a trusted HTTPS page. Point a tunnel at the frontend port:

   ```bash
   cloudflared tunnel --url http://localhost:8080
   ```

   Copy the `https://…trycloudflare.com` address it prints. It changes each time the tunnel restarts.

3. **Start the gallery with voice.** Use the gallery variables from [the Getty guide](getty-gallery.md), plus:

   ```bash
   export PUBLIC_BASE_URL=https://<your-tunnel>.trycloudflare.com
   export VINCENT_ENABLED=true
   export WHISPER_PYTHON=.venv-whisper/bin/python
   export OPENAI_API_KEY_FILE="$HOME/.config/musebridge/openai.key"
   ./scripts/run-gallery.sh
   ```

   The script starts the worker on `127.0.0.1:8003`, passes the speech key only to Go, and lets Vite accept the tunnel's hostname. Check `http://127.0.0.1:8003/ready` returns `{"ready":true}` once the model has loaded.

4. **Open the tunnel address on the phone.** Use the same HTTPS address for the gallery, the Vincent page and the API, so the cookie and the allowed origin match.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `VINCENT_ENABLED` | `false` | Mounts the two voice routes. Requires `GENERATION_MODE=agent` and an HTTPS (or localhost) `PUBLIC_BASE_URL`. |
| `WHISPER_BASE_URL` | none | Private worker origin. Must be a loopback address; forward the port if the worker runs on another computer. |
| `OPENAI_API_KEY` | none | Server-side speech key. Never sent to the browser or the worker. Keep it in a file outside the repository. |
| `VINCENT_TTS_VOICE` | `cedar` | Voice chosen with C after listening on the target iPhone. |
| `VINCENT_TTS_INSTRUCTIONS` | contract text | Delivery direction. Changing it changes the cache key. |
| `VINCENT_TTS_CONCURRENCY` | `2` | Paid syntheses in flight; further different requests get `429 voice_busy`. |
| `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE` | `small.en`, `cpu`, `int8` | Worker model settings. CPU keeps recognition off the Qwen GPU. |

When voice is enabled, Go allows 35 seconds to read a request body so a phone can upload a 3 MiB clip through the tunnel.

## Using the controller in the Vincent page

```tsx
import { useVincentVoice } from '../voice/useVincentVoice';

const { voice, snapshot } = useVincentVoice(conversationId, {
  submit: async (question) => ({ turnId: (await sendQuestion(question)).turn_id }),
  welcomeUrl: '/vincent/welcome.mp3', // optional
});
```

- `submit` sends the transcript through the existing chat client with one message ID and the current expected revision, and resolves with the committed turn (after history recovery if needed).
- Call `voice.playWelcome()` once on entry; `startRecording()` and `stopRecording()` from the Record and Stop taps; `retryTranscription()` when `snapshot.error.stage === 'transcription'` and `retryable`; `stopPlayback()` from Stop; `playTurn(turnId)` from Play or Replay, and to retry audio after a speech failure.
- `snapshot.phase` is one of `ready`, `recording`, `transcribing`, `generating`, `preparing_audio`, `playing`, `awaiting_play` or `error`. `playing` starts on the audio element's real `playing` event. `awaiting_play` means Safari blocked autoplay: show a Play button that calls `playTurn(snapshot.turnId)`.
- Read the answer text and sources from the committed turn, not from the controller. On a capture error with code `microphone_denied`, show `snapshot.error.message` and a text box.

## Checks still to run on real devices

These come from the [voice contract](vincent-voice.md#https-setup-and-acceptance) and need the v0.4 Vincent conversation first:

- iPhone Safari: allow the microphone, ask an unscripted question, see the matching transcript, and confirm exactly one message is sent.
- Desktop Chrome (WebM) and iPhone (MP4) both transcribe; silence, a corrupt file, over 20 seconds and over 3 MiB give the specified errors with no message sent.
- Two real turns where the second is "What do you mean by that?".
- Stop speech while it plays, replay it, then record again; the new recording contains only the visitor's voice.
- With the speech key removed, the answer and sources stay readable, and retrying audio does not rerun Qwen.
- Two simultaneous speech requests for the same turn make one provider call.
