# Talk with Vincent: connected visitor page

The Irises entry opens `/a/getty-103jnh/vincent`. The live page now uses the
v0.4 conversation client and A's voice controller. The ordinary guide retains
its existing v0.3 conversation and history. See the [public contract](vincent-contract.md)
and [voice setup](vincent-voice-setup.md) for the server configuration.

## Visitor flow

1. Create or restore the visitor's Vincent conversation, then load its history.
   The page checks the public contract, artwork, dataset, reference pack and
   English language state before enabling input. Concurrent initial mounts
   share one create request, including in React StrictMode.
2. Show the silent greeting once per page entry, then the listening portrait.
   The supplied greeting video contains no audio; a spoken welcome remains
   a separate asset/voice-selection task. It is not a fabricated conversation turn.
3. Record and stop, or type an English question. Recording fixes the logical
   message ID and expected revision at the Record tap. Typing fixes them on
   Send. Both inputs then use the same submit, recovery and retry path.
4. Display the committed answer and its sources. Request speech for that saved
   turn; the browser does not send alternative text to the speech provider.
   Only the audio element's `playing` event starts the artwork animation.
5. Stop or replay, then ask another question. A new recording stops prior
   speech. Earlier replies can be selected with their own sources and replayed.
   A refresh restores history without automatically speaking old answers.

Source cards distinguish an artist's letter, a museum record, a MuseBridge
guide note and a catalog fact. The v0.4 parser checks required fields, source
URLs, source kinds, duplicate identities, ordering and scope relationships.
The server remains responsible for verifying exact released evidence content.
The ordinary guide's parser has not been relaxed for Vincent-specific sources.

## Interrupted requests

- A lost POST response is reconciled through history using the original message
  ID. While that request is processing, the browser polls with GET; it does not
  keep posting the question. Only a confirmed absent request or retryable failed
  request permits an identical POST. The browser has one 70-second wait budget.
- Refreshing a processing conversation performs read-only recovery. Input stays
  disabled until the saved state is known. If history cannot be reached, **Check
  again** retries the history check before allowing another submission.
- **Retry same question** preserves the question, message ID and expected revision.
  It is offered only for a retryable message failure. Failed requests restored
  from history follow the same rule.
- A state conflict refreshes history and preserves the unsent words. **Review
  question** copies them into the text field; a deliberate new submission uses
  a new ID and the current revision. No automatic new question is created.
- Microphone denial opens typing. Transcription retries reuse the retained clip.
  Speech errors keep the answer and sources; Play retries only speech. Stop also
  cancels playback that is still being prepared. Leaving the page aborts pending
  client waits, releases the microphone and stops audio.

## Running the page

Use the real-service instructions in [voice setup](vincent-voice-setup.md).
The gallery launcher sets the live frontend and Agent configuration. All phone
requests must use the same trusted HTTPS origin; HTTP on a LAN address is not
sufficient for microphone access. Browser code receives no service API keys.

For UI-only work, `npm run dev` in `web/` still supports
`/a/getty-103jnh/vincent?preview=1`. This explicit development preview simulates
conversation and media states without calling the live services. Production
builds omit it. The ordinary mock/fixture mode does not open a v0.4 conversation.

## Automated checks

```bash
cd web
npm test
PUBLIC_CONTRACT=agent npm run build
```

`vincent-live.test.mjs` mounts the real page and controller under StrictMode,
with controlled public HTTP responses and browser media. It covers two turns,
recording plus typing, sources, actual playback events, Stop/replay, refresh,
lost responses, stale revisions, retry identity, failed-request restoration,
autoplay denial, microphone denial, speech failure and navigation cleanup.
`voice-client.test.mjs` checks transport recovery and malformed/wrong-scope data
using the shared contract examples. Existing guide and preview tests remain active.

The Go regression command remains `go test -race ./...` in `api/`.
Run `python3 scripts/generate_agent_contracts.py --check` from the repository root
when checking the shared generated contracts.

### Optional real-Go browser fixture

This fixture is for local integration only. It uses the actual Go public API,
conversation store, validation and media adapters, with a scripted Agent,
stand-in recognizer and a local test MP3. It needs no GPU or service key and does
not evaluate real recognition, model quality or TTS. It is available only through
Go tests, and ordinary test runs skip it.

1. Create an empty temporary directory. Put a short, valid MP3 named `answer.mp3`
   there. A test tone is sufficient; do not use personal recordings. If FFmpeg is
   already installed, generate one with:

   ```bash
   mkdir -p /tmp/musebridge-vincent-browser
   ffmpeg -f lavfi -i sine=frequency=440:duration=5 \
     -codec:a libmp3lame /tmp/musebridge-vincent-browser/answer.mp3
   ```

2. From `api/`, start the fixture (it stops after ten minutes or when the stop
   file appears). Use a fresh directory or remove a previous test's stop file:

   ```bash
   VINCENT_BROWSER_TEST_DIR=/tmp/musebridge-vincent-browser \
   VINCENT_BROWSER_TEST_ORIGIN=http://127.0.0.1:4320 \
   go test -run '^TestVincentBrowserFixture$' -count=1 -v ./internal/httpapi
   ```

3. Read `/tmp/musebridge-vincent-browser/api-url.txt`. From `web/`, use that
   loopback URL as `API_TARGET` (replace `<port>` with its printed port):

   ```bash
   PUBLIC_CONTRACT=agent PUBLIC_BASE_URL=http://127.0.0.1:4320 \
   API_TARGET=http://127.0.0.1:<port> \
   npm run dev:live -- --host 127.0.0.1 --port 4320 --strictPort
   ```

4. Open `http://127.0.0.1:4320/a/getty-103jnh/vincent` on the same computer. Submit
   a typed question, stop and replay the test tone, record a second question,
   inspect sources and refresh. The recognizer deliberately always returns
   “Why is that?” and the Agent uses the shared contract example; these are not
   real answers to arbitrary questions. The second turn should use the same
   conversation, and refresh/replay must not add a turn.
5. Close the frontend with Ctrl+C, then create the fixture's stop file:
   `touch /tmp/musebridge-vincent-browser/stop`. Delete the temporary directory
   when it is no longer needed. This does not touch model caches or project data.

## Verification status

Desktop automation and controlled providers establish UI/service integration,
not physical-phone acceptance. The remaining real-device record must cover
trusted HTTPS, the real Whisper model, C's v3 Agent/Qwen, real TTS, the selected
English voice, a spoken welcome, two contextual turns and playback recovery on
an actual iPhone. Use the acceptance table in [voice setup](vincent-voice-setup.md).
