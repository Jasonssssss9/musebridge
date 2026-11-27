# Vincent voice transport and phone interaction

This is the implementation target for English voice conversations on Getty’s *Irises*. Read the [Vincent application and Agent contract](vincent-contract.md) for conversation creation, mode, message submission, evidence and memory. A owns audio capture, Go media endpoints, Whisper and speech synthesis. B owns the page, portrait and interaction states. C owns model-authored answer text and works with A to select delivery and voice.

## Verified starting point

An iPhone user recorded, replayed and correctly transcribed an English question in Safari. That prototype used a separate HTTPS page, a temporary tunnel, browser `MediaRecorder` and an English faster-whisper model on the demo computer. Synthetic AAC/MP4 and Opus/WebM requests also produced the expected transcript. The prototype used its own invite and an explicit Transcribe button. The released project must integrate recording, conversation ownership, answer generation and playback as described below. The prototype URL and token are temporary.

## Common public HTTP rules

- Use the same HTTPS origin for the Vincent page and relative `/api` requests. Both media routes are POSTs. Go requires the configured exact `Origin`, the existing host-only HttpOnly visitor cookie and an owned Vincent conversation. Use `Secure` cookies at the HTTPS entry point.
- Use canonical lowercase UUIDs in paths. Missing, expired and foreign conversations all produce the same 404. Check ownership and mode before private work, paid synthesis or serving cached audio. A turn must belong to the specified conversation.
- Include a server-generated `X-Request-ID` and `Cache-Control: no-store` on success and failure. Errors use the usual public JSON envelope. The transcript endpoint is the only exception here to the ordinary 8 KiB JSON body limit. Reject compressed bodies and unknown query parameters. No visitor cookie or project service token goes to the TTS provider.
- Neither media route creates a message, advances a revision or writes conversation memory. Existing message submission is the sole operation that commits a turn. Fixture deployments do not mount these routes (404 `route_not_found`). In an Agent deployment, an owned ordinary guide conversation returns 409 `voice_mode_unavailable`.

## Record and transcribe

`POST /api/conversations/{conversation_id}/transcriptions` accepts one raw recording as its body. Send the actual Blob media type. Multipart, base64 and JSON wrappers are unsupported. Support audio-only MP4/M4A with AAC or Opus, and WebM with Opus. Accept `audio/mp4`, `audio/webm` and audio-only `video/mp4` with a compatible optional `codecs` parameter. Return 415 for a video stream, unsupported format or recognizable container that disagrees with the declaration. Return 422 for broken or undecodable audio under a supported declaration. The browser negotiates a supported MIME before starting capture.

Stop capture at 20 seconds and release the microphone. Limit the full upload to 3 MiB (3,145,728 bytes), including requests without a declared length. Enforce the duration on decoded audio. Recognize speech in English with the locally installed model. Trim surrounding whitespace. The result must contain 1–500 Unicode code points and satisfy the existing message text rules. Do not truncate. The response language indicates recognition mode, not an independently verified language detection.

```http
POST /api/conversations/7b6cd85e-50fb-47df-a567-13cf87751e21/transcriptions HTTP/1.1
Host: museum.example
Origin: https://museum.example
Cookie: musebridge_visitor=<browser-issued-cookie>
Content-Type: audio/mp4; codecs=mp4a.40.2

<raw recording bytes>
```

```http
HTTP/1.1 200 OK
Content-Type: application/json; charset=utf-8
Cache-Control: no-store
X-Request-ID: req-transcription-example

{"text":"Why do they look blue today?","language":"en"}
```

Show the transcript and send it as the question using the normal Vincent message endpoint. Bind a single client message ID and expected revision to this logical question. A retry after an uncertain message response uses the original submission and existing history recovery. A transcription error leaves the prior chat intact. A new recording replaces an unsubmitted clip. Never submit a late transcript after leaving the conversation.

## Speak a committed answer

`POST /api/conversations/{conversation_id}/turns/{turn_id}/speech` takes an empty body, with `Content-Length: 0` or no length header. Do not send JSON or a content type. Reject a nonempty body as 400. Resolve the owned completed turn and use its stored `answer.text` verbatim. The client supplies neither text nor voice controls. A missing, foreign or uncommitted turn returns 404 `turn_not_found`.

```http
POST /api/conversations/7b6cd85e-50fb-47df-a567-13cf87751e21/turns/d4fc3fee-17cb-44c5-b44e-b64910511600/speech HTTP/1.1
Host: museum.example
Origin: https://museum.example
Cookie: musebridge_visitor=<browser-issued-cookie>
Content-Length: 0
```

```http
HTTP/1.1 200 OK
Content-Type: audio/mpeg
Cache-Control: no-store
X-Request-ID: req-speech-example

<MP3 audio bytes>
```

Go requests `POST https://api.openai.com/v1/audio/speech` server-side using `model=gpt-4o-mini-tts`, `voice=cedar` by default, `response_format=mp3`, and `input=turn.answer.text`. Keep the key and delivery instructions on the server. Start with the direction “Speak as if you are standing beside one visitor in a quiet gallery. Use a warm conversational voice and leave a brief pause between thoughts.” A and C may revise the delivery preset together. The synthesis request contains the committed answer and the server’s speech settings. Exclude citations, audio recordings, transcripts as separate fields, history and memory. [Speech API reference](https://developers.openai.com/api/docs/guides/text-to-speech)

The browser replays its in-memory Blob and releases the object URL when discarded. Go coalesces simultaneous requests for an identical owned turn and preset into one in-flight synthesis. Keep successful MP3s in a process-local cache keyed by conversation, turn and preset revision, bounded to 32 MiB total, with least-recently-used eviction and a ten-minute TTL capped by session expiry. Failed syntheses are not cached. Cache eviction or restart may require a new paid synthesis. A replay or retry never runs Qwen again.

## Private local recognition

A configures Go with a private loopback Whisper worker on the same host or a loopback port forward to the demo computer. Go calls `POST /recognize` with the same raw body and MIME, an internal trace ID and no browser cookie. The worker decodes audio in memory, applies the same limits, and returns `200 application/json` with `{"text":"Why do they look blue today?","language":"en"}`. It exposes `GET /ready` returning 200 `{"ready":true}` only after its local English model has loaded. Keep both worker routes off the public proxy. No OpenAI key is needed by Whisper.

Private failures use `{"error":"<code>"}`. Use 413 `payload_too_large`, 415 `unsupported_media_type`, 422 `audio_invalid`, `no_speech` or `transcript_too_long`, 429 `voice_busy`, and 503 `transcription_unavailable`. Go translates these into the public envelope, validates success fields before accepting them, and maps malformed worker success to 503 `transcription_unavailable`. The worker may decode incrementally in memory and must discard each clip after processing. Do not store clips, transcripts or answer bodies in transport logs.

## Error handling and deadlines

| HTTP | Public code | Retryable | Browser action |
| --- | --- | --- | --- |
| 400 | `invalid_request` | false | Correct a malformed path or body. |
| 403 | `origin_not_allowed` | false | Open the configured HTTPS page. |
| 404 | `conversation_not_found` / `turn_not_found` | false | Refresh conversation history or start a new session. |
| 409 | `voice_mode_unavailable` | false | Enter Vincent from the *Irises* page. |
| 413 / 415 | `payload_too_large` / `unsupported_media_type` | false | Shorten the recording or use a supported capture format. |
| 422 | `audio_invalid` / `no_speech` / `transcript_too_long` | false | Record a new, clear question. Over-duration or corrupt data uses `audio_invalid`. |
| 429 | `voice_busy` | true | Offer retry after the current request finishes. |
| 503 | `transcription_unavailable` / `speech_unavailable` | true | Retry this media stage. Keep the clip or completed answer visible. |
| 504 | `voice_timeout` | true | Retry this media stage without resubmitting a chat question. |

```json
{"request_id":"req-voice-example","error":{"code":"no_speech","message":"No clear speech was recognized. Record another question.","retryable":false}}
```

Give each media operation a separate 30-second Go deadline, including its downstream call. The browser allows 35 seconds per media operation. Map provider failures to the stage-specific 503 or 504 without exposing provider bodies or secrets. An explicit media retry runs only that stage. Existing message deadlines and recovery rules still apply separately. Silence creates no turn. A successful text turn remains committed even if its audio is unavailable.

## Browser states and integration checks

| State or event | A supplies | B displays |
| --- | --- | --- |
| Enter / ready | Owned Vincent conversation, optional prerecorded welcome | Short portrait greeting once per entry; disclose “An AI interpretation of Vincent van Gogh, with an AI-generated voice.” The welcome has the same voice, no museum claim and no conversation turn. |
| Recording | Explicit tap, microphone permission, elapsed time and stop | Listening portrait, active microphone and Stop. On denial show recovery instructions and permit text input. |
| Transcribing | Capture stopped; microphone tracks released | Show recognition progress. On success display the transcript before sending. |
| Generating | Original message submission or history recovery | Show the visitor's question and waiting state. |
| Preparing audio | Completed answer and citations, speech request pending | Show the full answer and sources immediately. |
| Playing | Actual media `playing` event | Show the artwork detail and spoken-answer captions. Display the exact `answer.text`; captions need no word-level timing. |
| End / stop / replay | `ended`, user stop, playback rejection or local replay | Stop motion, retain answer and sources. Offer Play when automatic playback is blocked. |

Stop current playback before opening the microphone again. On leaving, release tracks, stop audio and ignore late results for the old conversation or capture. An accepted chat run may still complete; retrieve it through normal history.

**A ↔ B browser handoff.** The view calls `playWelcome()`, `startRecording()`, `stopRecording()`, `retryTranscription()`, `stopPlayback()`, `playTurn(turnId)` and `dispose()` on a controller bound to one conversation ID. Ordinary text submissions use the existing chat client. A publishes a snapshot with `conversationId`, `phase` (`ready | recording | transcribing | generating | preparing_audio | playing | awaiting_play | error`), nullable `transcript`, nullable `turnId` and nullable `error`. An error identifies the failed stage (`capture | transcription | message | speech | playback`), human-readable English message, retryability and public error code when available. B reads the authoritative turn for text and citations. A permission denial offers typed input; blocked autoplay uses `awaiting_play` and a Play button. Explicit stop or natural end returns to `ready`. B triggers the welcome once per entry through A’s controller. A cancels it when recording starts and owns playback so two voices cannot play simultaneously.

## HTTPS setup and acceptance

Point the temporary HTTPS entry at the frontend's public gateway. Configure `PUBLIC_BASE_URL` with that exact HTTPS origin, enable secure cookies, and forward the relative `/api` routes to Go. Update the entry link when the temporary hostname changes. Opening the ordinary hotspot page over HTTP still lets a visitor browse; the page that requests microphone access must itself open under trusted HTTPS.

For the new implementation, Go reads `VINCENT_ENABLED` (default false), `WHISPER_BASE_URL` (private worker origin) and server-only `OPENAI_API_KEY` for speech. Serve the gallery, role page and API under the one configured HTTPS origin for full guide and Vincent interaction. A QR code pointing to an older HTTP gallery may navigate to that HTTPS origin. Its cookie and authorized POST origin are separate. Use a single recognition slot for the local model and bound concurrent paid synthesis on the server. Return `429 voice_busy` when capacity is occupied, after coalescing requests for the same speech. Keep the raw recording in memory only while the visitor can retry transcription.

Keep the local recognition worker, Ollama and the private Agent API behind the gateway. Validate the public Origin and cookie before forwarding audio. For an external TTS request, enable the server-side API credential and identify the voice as AI-generated on the visitor page.

| Check | Observable result |
| --- | --- |
| iPhone Safari microphone | Accept permission, ask an unscripted English question and stop. The visible text matches the spoken question and exactly one message is submitted. |
| Desktop WebM and Safari MP4 | Both produce a text result. Silent, corrupt, over-duration or over-limit input produces the specified error and zero submissions. |
| Real follow-up | After the first committed answer, record “What do you mean by that?” in the same session. The second answer refers to the first turn. |
| Stop and replay | Stop speech while it plays, replay from the local Blob, then record again. The next recording contains the visitor’s voice. |
| TTS unavailable | The answer and evidence remain readable. Retrying audio preserves the answer text, conversation revision and inference count. |
| Mode or ownership change | Guide, expired, foreign and mismatched-turn requests follow the table above. Leaving during capture or playback keeps old results off the new page. |
| Concurrent speech requests | Simultaneous requests for the same authorized turn and preset cause one provider call. A failed prior attempt can be retried. |
