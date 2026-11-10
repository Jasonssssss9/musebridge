# MuseBridge API and data contract

Contract revision: `local-demo-v0.1`. Dataset: `aic-seed-v0.1`.

This is the implementation baseline for stage one. It specifies intended behavior; publishing this document does not mean the endpoints already exist. [design.md](design.md) explains the system, and [plan.md](plan.md) assigns implementation and acceptance work. Jeremiah Lim owns the browser client, Jinqing Zhang owns the Go application API and generation adapter, and Jason Zichen He owns the Python retriever and source release.

The public API is synchronous JSON. The first demo uses actual museum evidence and deterministic fixture generation. It requires no local LLM, embedding model, cloud account, or database. The interfaces leave room for a later cloud GPU adapter, but real generation needs its own contract revision and evaluation.

Use these sections when implementing a boundary:

- [Common transport, identity and scope](#1-contract-conventions)
- [Browser API and a complete material-question trace](#2-frontend-and-go-api-public-endpoints)
- [Idempotency and browser recovery](#3-submission-state-retries-and-browser-recovery)
- [Retrieval API and ranking rules](#4-go-api-and-retrieval-service-retrieval-contract)
- [Seed and evaluation file formats](#5-versioned-source-release-and-evaluation-data)
- [Follow-ups and the fixture adapter](#6-query-preparation-and-generation-boundary)
- [Errors, deadlines and configuration](#7-errors-time-budgets-and-service-health)
- [Integration and change control](#8-integration-checks-and-contract-changes)

## 1. Contract conventions

### 1.1 Transport and common validation

| Item | Rule |
| --- | --- |
| Public base | The same origin as the web app, with routes under `/api`. The frontend uses relative URLs. |
| Internal base | The Go API's configured `RETRIEVAL_BASE_URL`; `POST /retrieve` is private to the service network. |
| Encoding | UTF-8 JSON, `Content-Type: application/json`. Request bodies must be JSON objects, with no duplicate property names or trailing content. |
| Names | JSON fields use snake_case. All documented response fields are present; nullable fields explicitly use `null` and empty arrays use `[]`. |
| Request strictness | Reject unknown fields, missing required fields, null for nonnullable fields, and incorrect types with `400 invalid_request`. No implicit string-to-number conversion. |
| Response extensibility | Consumers ignore unknown response fields but validate all required fields, types, supported enum values, and invariants. Unknown enum values must not be displayed as successful answers. |
| Sizes | The Go API accepts at most 16 KiB per POST body; the retrieval service accepts at most 8 KiB. Oversize bodies return `413 payload_too_large`. The Go API caps the retrieval service's response at 64 KiB. |
| IDs | Artwork/source/chunk IDs are stable case-sensitive strings. Conversation, turn, and client message IDs are UUIDs. Accept canonical UUID text case-insensitively and store lowercase; reject malformed UUIDs. Browser-generated IDs use UUID v4. |
| Time | Server-generated RFC 3339 UTC timestamps. Examples use `Z`. Ordering uses integer sequence numbers, not timestamps. |
| Question length | Trim leading/trailing Unicode whitespace once, then require 1–500 Unicode code points. Preserve internal whitespace and punctuation in the stored question. Reject NUL and control characters other than tab, LF and CR. |
| Answer languages | Exactly `en` or `zh-CN`. This controls the answer, independently of the question's language. |
| Cache | All `/api` responses use `Cache-Control: no-store` during this stage, including catalog responses. |
| Request tracing | The Go API generates an opaque `X-Request-ID` for every HTTP attempt and forwards it to the retrieval service. The retrieval service echoes it, or generates one for direct calls. Public callers cannot choose the Go API's trusted request ID. |
| Method/path errors | Unsupported method: `405 method_not_allowed` with `Allow`. Unknown route: `404 route_not_found`. No undocumented query parameters on these endpoints; reject them with `400 invalid_request`. |
| Content type | POST with a non-JSON media type returns `415 unsupported_media_type`. A charset parameter is accepted only for UTF-8. |

`request_id` identifies an HTTP attempt for diagnostics. `client_message_id` identifies a logical question across attempts. They serve different purposes.

For the LAN HTTP demo, the frontend must generate UUID v4 values without relying exclusively on `crypto.randomUUID()`, which requires a secure context. Use `crypto.getRandomValues` with the UUID version/variant bits set, or a UUID implementation verified to use that method on HTTP. Never use `Math.random` for IDs. Validate this on the actual phone origin, since localhost can behave differently. [MDN randomUUID documentation](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/randomUUID), [MDN getRandomValues documentation](https://developer.mozilla.org/en-US/docs/Web/API/Crypto/getRandomValues).

The frontend initially selects `zh-CN` if the browser's preferred language begins with `zh`, otherwise `en`. Show a language selector, include its current value on every submission, and preserve the submitted language on retries. Changing the selector affects the next new question; it does not relabel existing answers or alter a pending payload.

Every non-2xx API response uses this envelope. `message` is a safe English diagnostic; the frontend uses `code` to choose localized visitor-facing text. Do not return stack traces, cookies, filesystem paths or upstream response bodies.

```json
{
  "request_id": "req-demo-001",
  "error": {
    "code": "retrieval_unavailable",
    "message": "The evidence service is temporarily unavailable.",
    "retryable": true
  }
}
```

An `Error` is the inner object with exactly the documented `code`, `message` and `retryable` fields. `retryable` means that a transient condition may resolve; it does not authorize an automatic loop or changing the logical message ID. Request ID values and error wording are not stable test assertions.

### 1.2 Browser identity, origins and cookies

The Go API uses an opaque random visitor token of at least 128 bits in a cookie named `musebridge_visitor`. The token is never placed in JSON, QR links, local storage, or logs. Cookie attributes are `Path=/; HttpOnly; SameSite=Lax; Max-Age=86400`, with no `Domain` attribute. Add `Secure` under HTTPS. The explicitly configured LAN HTTP demo uses `COOKIE_SECURE=false`; hosted deployment requires HTTPS and `true`.

Only `POST /api/conversations` establishes or refreshes the cookie. An absent, invalid, or unknown token at that endpoint starts a new anonymous visitor. Message and history endpoints never create a new visitor as a side effect.

The Go API accepts public POST requests only when the `Origin` exactly matches the configured `PUBLIC_BASE_URL` origin, including scheme and port. Missing, `null` or mismatched origins return `403 origin_not_allowed`. CLI clients must provide the same header. This is a selected application rule, including on initial conversation creation. Do not enable wildcard credentialed CORS. The reverse proxy preserves the cookie, `Origin`, request ID and response status.

The cookie's renewal does not extend a conversation's fixed expiry. State belongs to one Go API process. Restarting the Go API loses visitors, histories, pending requests and idempotency records. The browser must recover by creating a new conversation, and the UI must explain that the previous conversation is no longer available.

### 1.3 Stable identifiers and scope

| Artwork ID and URL slug | Title | Artist | Display date | Museum record number |
| --- | --- | --- | --- | --- |
| `aic-28560` | The Bedroom | Vincent van Gogh | 1889 | 1926.417 |
| `aic-11723` | Woman at Her Toilette | Berthe Morisot | 1875–80 | 1924.127 |

These names and records come from the museum's [Bedroom API record](https://api.artic.edu/api/v1/artworks/28560) and [Woman at Her Toilette API record](https://api.artic.edu/api/v1/artworks/11723). Slugs equal artwork IDs in this release. A QR encodes a browser route such as `http://HOST:8080/a/aic-28560`, never a conversation ID or an internal API address.

A conversation is permanently bound to one artwork and one dataset version. The server derives both from the conversation for all message requests. It never accepts a browser-supplied artwork override. No cross-artwork retrieval, comparison, exhibition fallback or shared conversation exists in this contract.

## 2. Frontend and Go API: public endpoints

| Method and path | Purpose | Success |
| --- | --- | --- |
| `GET /api/artworks/{slug}` | Load an artwork card from the Go API's local catalog | `200`, `{"artwork": Artwork}` |
| `POST /api/conversations` | Atomically create or restore the visitor's conversation for this artwork | `200`, `{"conversation": Conversation, "created": boolean}` |
| `GET /api/conversations/{conversation_id}/messages` | Restore completed turns and the latest unfinished request | `200`, `{"conversation": Conversation, "turns": Turn[], "pending_request": PendingRequest or null}` |
| `POST /api/conversations/{conversation_id}/messages` | Submit or retry one logical question | `200`, `{"turn": Turn}` |

A valid completed answer always uses HTTP 200, including `insufficient` and `unsupported_question`. A technical failure always uses a non-2xx error envelope and creates no completed turn.

### 2.1 Artwork lookup

No cookie is required. The Go API serves the catalog from the local seed, independently of the retrieval service's availability. Unknown slugs return `404 artwork_not_found`.

```json
{
  "artwork": {
    "artwork_id": "aic-28560",
    "slug": "aic-28560",
    "title": "The Bedroom",
    "artist": "Vincent van Gogh",
    "date_display": "1889",
    "museum": "Art Institute of Chicago",
    "museum_record_id": 28560,
    "accession_number": "1926.417",
    "source_url": "https://api.artic.edu/api/v1/artworks/28560",
    "image_url": null,
    "dataset_version": "aic-seed-v0.1"
  }
}
```

Every `Artwork` field above is required. `museum_record_id` is a positive integer; `image_url` is nullable and all other fields are nonempty strings. This release sets both image URLs to `null` and the frontend displays a text card or neutral placeholder. Adding museum images requires reviewed image rights, attribution fields and a new data release; text licensing alone does not authorize an image. The frontend must not guess an image URL or a gallery location.

### 2.2 Create or restore a conversation

Request:

```json
{
  "artwork_id": "aic-28560"
}
```

The Go API validates the artwork, resolves the visitor cookie, and atomically looks up the live conversation by `(visitor_id, artwork_id, dataset_version)`. The version is the Go API's configured `aic-seed-v0.1`, never a browser input. Return the existing live conversation with `created: false`, or create one with `created: true`. Both use HTTP 200.

```json
{
  "conversation": {
    "conversation_id": "b15425c0-1f61-4ed2-9ec5-74ae9a9ea152",
    "artwork_id": "aic-28560",
    "dataset_version": "aic-seed-v0.1",
    "created_at": "2026-11-07T18:00:00Z",
    "expires_at": "2026-11-08T18:00:00Z"
  },
  "created": true
}
```

The five `Conversation` fields are required. Expiry is exactly 24 hours after creation and is fixed. The conversation is unavailable when `now >= expires_at`. A restore never changes its ID, creation time or expiry. Returning to the other artwork creates or restores a separate conversation.

The frontend serializes initial creation in a browser tab. Two requests without any shared cookie cannot be reliably recognized as the same visitor. After the cookie is established, concurrent creation requests for the same key must return the same conversation. This contract does not promise multi-tab coordination before anonymous identity exists.

### 2.3 Read history

The Go API checks ownership and expiry before returning any state. Missing identity, a foreign conversation, an expired conversation, an unknown UUID, or a conversation lost on restart all return the same `404 conversation_not_found`. Do not disclose which condition applied. A malformed UUID is `400 invalid_request`.

A fresh conversation:

```json
{
  "conversation": {
    "conversation_id": "b15425c0-1f61-4ed2-9ec5-74ae9a9ea152",
    "artwork_id": "aic-28560",
    "dataset_version": "aic-seed-v0.1",
    "created_at": "2026-11-07T18:00:00Z",
    "expires_at": "2026-11-08T18:00:00Z"
  },
  "turns": [],
  "pending_request": null
}
```

`turns` contains the full completed history in ascending `sequence`. There is no pagination in this two-work demo. A `Turn` represents one user question and its completed assistant response together; there are no separate user/assistant message IDs in this API.

A non-null `PendingRequest` has these required fields:

```json
{
  "client_message_id": "0dd2a1f3-8e81-4266-926a-cbe04584ee84",
  "question": "What material is this artwork made of?",
  "answer_language": "en",
  "state": "failed",
  "last_error": {
    "code": "retrieval_unavailable",
    "message": "The evidence service is temporarily unavailable.",
    "retryable": true
  }
}
```

`state` is `processing` or `failed`. `last_error` is `null` while processing and an `Error` after a technical failure. Only the latest pending request is exposed. Historical failed attempts are not displayed as completed turns. A history read is an atomic snapshot: it must not show a committed turn and the same request as pending simultaneously.

### 2.4 Submit a question: complete Bedroom material trace

After creating the conversation, the frontend submits:

```json
{
  "client_message_id": "0dd2a1f3-8e81-4266-926a-cbe04584ee84",
  "question": "What material is this artwork made of?",
  "answer_language": "en"
}
```

All three fields are required. A browser can send the canonical Chinese material question instead, with `answer_language: "zh-CN"`. The requested answer language is not inferred again by the Go API. The Go API stores the trimmed question, validates identity, and reserves the request before calling dependencies.

The resulting Go API → retrieval service request:

```json
{
  "artwork_id": "aic-28560",
  "query": "What material is this artwork made of?",
  "top_k": 2,
  "dataset_version": "aic-seed-v0.1"
}
```

The retrieval service returns HTTP 200:

```json
{
  "dataset_version": "aic-seed-v0.1",
  "evidence": [
    {
      "chunk_id": "aic-28560-medium",
      "artwork_id": "aic-28560",
      "source_id": "aic-api-28560",
      "source_title": "The Bedroom — Art Institute of Chicago API record",
      "source_url": "https://api.artic.edu/api/v1/artworks/28560",
      "source_language": "en",
      "source_locator": "data.medium_display",
      "excerpt": "Oil on canvas",
      "license": "CC0-1.0",
      "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
      "attribution": "Art Institute of Chicago",
      "normalization": "none"
    }
  ]
}
```

The Go API selects the reviewed material fixture only after validating this evidence. It resolves the citation from the returned record and commits the turn. Public HTTP 200:

```json
{
  "turn": {
    "turn_id": "85121f3b-487d-4f23-871f-c77144e170bd",
    "sequence": 1,
    "client_message_id": "0dd2a1f3-8e81-4266-926a-cbe04584ee84",
    "question": "What material is this artwork made of?",
    "answer_language": "en",
    "dataset_version": "aic-seed-v0.1",
    "created_at": "2026-11-07T18:00:03Z",
    "answer": {
      "status": "answered",
      "mode": "fixture",
      "text": "The museum records the work as an oil painting on canvas.",
      "citations": [
        {
          "chunk_id": "aic-28560-medium",
          "artwork_id": "aic-28560",
          "source_id": "aic-api-28560",
          "source_title": "The Bedroom — Art Institute of Chicago API record",
          "source_url": "https://api.artic.edu/api/v1/artworks/28560",
          "source_language": "en",
          "source_locator": "data.medium_display",
          "excerpt": "Oil on canvas",
          "license": "CC0-1.0",
          "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
          "attribution": "Art Institute of Chicago",
          "normalization": "none"
        }
      ]
    }
  }
}
```

History subsequently contains exactly that turn and `pending_request: null`. Retrying the original request returns the same stored `turn`, including its original ID, sequence and creation time. It performs no new retrieval or generation.

### 2.5 Turn, answer and citation fields

| Field | Type and rule |
| --- | --- |
| `turn_id` | Server UUID; unique completed turn. |
| `sequence` | Integer starting at 1, contiguous within a conversation; assigned only at successful commit. |
| `client_message_id` | Original logical question UUID. |
| `question` | Trimmed original question, not the rewritten retrieval query. |
| `answer_language` | Original `en` or `zh-CN` selection. |
| `dataset_version` | Exact version pinned on the conversation. |
| `created_at` | Time of successful turn commit, in UTC. |
| `answer.status` | `answered`, `insufficient` or `unsupported_question`. |
| `answer.mode` | Exactly `fixture` throughout stage one. |
| `answer.text` | Nonempty plain text, at most 2,000 Unicode code points. No HTML or embedded source links. |
| `answer.citations` | Ordered array of `Evidence` records, as defined in section 4.2; unique by `chunk_id`. |

An `answered` turn has at least one citation. An `insufficient` or `unsupported_question` turn has an empty citation array. Citations are stored with the completed turn, so history and retries return the same evidence snapshot. For this short-answer fixture, every factual statement must be supported by the displayed evidence set; claim-level citation spans are outside this version.

The frontend renders answer text and excerpts as text, not HTML. Each source item shows its title, excerpt, source language, locator, museum attribution, license link and any normalization notice. Source URLs open as external HTTPS links with safe link handling. Do not render generated URLs. The visible response label is “Demo response — fixed test answer” in English, with an equivalent Simplified Chinese label. Label all three outcome statuses.

### 2.6 Three completed outcomes

Evaluate them in this order, after successful retrieval validation:

1. A recognized known-gap fixture is `insufficient` even if related evidence was returned.
2. An empty evidence list is `insufficient`.
3. A supported answer fixture is `answered` only if all its required chunk IDs are present. Otherwise it is `insufficient`.
4. A query outside the supported answer fixture set, with nonempty evidence, is `unsupported_question`.

A valid empty retrieval response:

```json
{
  "dataset_version": "aic-seed-v0.1",
  "evidence": []
}
```

Example `Answer` object for insufficient material:

```json
{
  "status": "insufficient",
  "mode": "fixture",
  "text": "The material available in this demo does not provide enough evidence to answer this question.",
  "citations": []
}
```

Example `Answer` object for an unrecognized fixture question that still retrieved evidence:

```json
{
  "status": "unsupported_question",
  "mode": "fixture",
  "text": "This demo uses fixed test answers, and this question is outside the supported examples. Try asking about the material or brushwork.",
  "citations": []
}
```

These objects appear inside an otherwise complete `Turn`. They are successful completed turns, so replay and ordering apply normally. They describe this seed and fixture mode, not the museum's entire knowledge. Stage one does not perform general automatic evidence-sufficiency reasoning.

## 3. Submission state, retries and browser recovery

### 3.1 Logical request state machine

Use `(conversation_id, client_message_id)` as the idempotency key. The payload identity is the trimmed question plus answer_language. Do not use the fixture matching normalization for idempotency: different punctuation or internal whitespace is a changed payload.

The Go API retains payload and state records until the conversation expires. State transitions and turn commits must be atomic; network calls run outside the state lock.

| Existing state for key | Incoming request | Result |
| --- | --- | --- |
| None, no processing request | Valid new payload | Reserve `processing` and run the attempt. If the prior pending request was `failed`, mark its key `superseded`. |
| Any recorded state | Same key, changed payload | `409 idempotency_conflict`; no downstream work. |
| `processing` | Same key and payload | `409 request_in_progress`; original attempt continues. |
| A different key is `processing` | New key | `409 conversation_busy`; do not reserve the rejected key. |
| `completed` | Same key and payload | `200` with the original turn, even if a different key is now processing. |
| `failed` and still current pending key | Same key and payload | Reserve a fresh bounded attempt; no completed turn exists yet. |
| `superseded` | Same key and payload | `409 request_superseded`; never revive it. |

Validate origin, body, conversation ownership/expiry, then same-key payload identity and stored completion before enforcing the busy rule. This ordering makes completed replay reliable. A rejected new key does not become an idempotency record. Only an accepted new key supersedes a failed pending request.

After retrieval and generation succeed, commit the entire turn, mark the key completed and clear pending state atomically. On technical failure, append no turn, set the current request to `failed` with its last error, and release the in-flight reservation. Use an attempt token so a timed-out dependency's late result cannot commit after a retry.

The Go API checks expiry at admission and immediately before commit. If the conversation expires during an attempt, discard the result and return `404 conversation_not_found`. A process restart can also end an attempt without a response; recovery then follows the unavailable-conversation flow.

Disconnecting the browser does not cancel a reserved Go API attempt. The Go API finishes it within its independent eight-second deadline, or records the failure. This makes a lost HTTP response recoverable with the same logical key. The contract guarantees at most one committed turn per key within the live Go API process, not durable execution across restarts.

### 3.2 Browser behavior by state

| Situation | Required frontend behavior |
| --- | --- |
| New question | Create a UUID once, save the draft trimmed according to section 1.1, disable parallel submission for that conversation, and send it. Do not apply fixture-selection normalization to the draft. |
| Waiting | Show the user's question and a pending indicator. Do not insert a completed assistant turn. |
| Success | Reconcile by `client_message_id` or `turn_id`, render once, and clear the matching saved draft. |
| Browser timeout or lost connection | Preserve UUID and payload. First reload history; if uncertain, retry the same request. |
| `409 request_in_progress` or `conversation_busy` | Read history, then poll it at one-second intervals while this page is active, for at most 12 seconds. After that, show a manual check/retry action. Never send a second key automatically. |
| Retryable technical failure | Keep the failed question and offer retry with the same UUID and payload. No automatic POST retry loop. |
| Edit a failed question | Deliberately create a new UUID; accepting it supersedes the old failed request. Keep the old draft until the new request is accepted or reconciled. |
| `idempotency_conflict` | Stop retrying. Reconcile history and require a deliberate new submission if the visitor changed the question. |
| `request_superseded` | Reload history; do not retry the obsolete key. |
| `404 conversation_not_found` | Explain that the session ended; create/restore a valid conversation for the current artwork. Keep an unsent draft, but require an explicit send with a new UUID. |
| Artwork switch | Stop rendering or polling the old conversation. Let the Go API complete any accepted attempt. Resolve the destination artwork's conversation and load its history. |

The frontend may keep the pending draft in `sessionStorage` under its conversation ID to survive reload. This contains the question and language but never the visitor cookie. Clear it after confirmed completion or deliberate abandonment; server history is authoritative. If storage is unavailable, use server `pending_request` for recovery. Compare conversation IDs before applying asynchronous responses so an old artwork's response cannot appear on the new page.

When the Go API reports `pending_request` but the frontend has no draft, the frontend can reconstruct the original payload from it. When a stored draft's UUID is already in `turns`, clear the draft. An old saved draft must not automatically supersede a newer server pending request.

## 4. Go API and retrieval service: retrieval contract

### 4.1 Request

`POST /retrieve` accepts:

| Field | Required | Type and rule |
| --- | --- | --- |
| `artwork_id` | Yes | One of the two released IDs. This is a hard filter. |
| `query` | Yes | String, trimmed, 1–1,000 Unicode code points; same control-character rule as questions. |
| `dataset_version` | Yes | Exactly `aic-seed-v0.1` for this release. |
| `top_k` | No | Integer 1–5; default 2. Reject floats, booleans and out-of-range values. The Go API always sends 2. |

The retrieval service receives no conversation ID, visitor identity, answer language, raw history, expected answer, or evaluation case ID. It must not infer artwork scope from query text or search another artwork when no hit is found.

The call is stateless and deterministic for identical normalized query, artwork, version and limit. The Go API makes one retrieval call per attempt and does not retry the retrieval service inside the eight-second budget. A logical retry can start a new attempt.

### 4.2 Response and evidence schema

HTTP 200 has required `dataset_version` and `evidence` fields. `evidence` is an ordered array with zero to `top_k` records. There are no duplicate chunk IDs, null entries, partial results or hidden fallback results.

Every `Evidence` record contains these required fields:

| Field | Meaning |
| --- | --- |
| `chunk_id` | Stable ID of the published chunk. |
| `artwork_id` | Exact requested artwork ID. |
| `source_id` | Stable source ID that resolves within the same seed. |
| `source_title` | Display title from that source record. |
| `source_url` | Original official HTTPS source URL from the seed. |
| `source_language` | `en` for all current evidence. |
| `source_locator` | Named API field or paragraph locator, not an invented page number. |
| `excerpt` | Original reviewed source passage, at most 4,000 Unicode code points. |
| `license` | `CC0-1.0` or `CC-BY-4.0` as assigned to this field. |
| `license_url` | The corresponding Creative Commons HTTPS URL. |
| `attribution` | `Art Institute of Chicago` for this release. |
| `normalization` | `none` or an explicit description of mechanical extraction/normalization. |

All fields are nonempty strings. The retrieval service joins source metadata to chunks; it does not ask the Go API to fetch museum pages during a request. The dataset version appears once in the envelope and must equal the requested version. A relevance score is intentionally absent: the initial ranking is an ordering heuristic, not an answer-confidence estimate.

The Go API validates the response status, content type, JSON shape, size, version, limit, uniqueness and exact artwork scope. It also verifies each chunk ID against the seed mounted in the Go API and compares the returned fields with the trusted projection of that chunk/source pair. A wrong or fabricated excerpt under a valid ID is still invalid. This requires the Go API to load the seed's provenance records for validation, although the Go API does not perform retrieval itself.

Reject the whole response on any violation. Do not silently drop an invalid record and answer with the remainder. Return `502 retrieval_invalid_response` and create no completed turn. Valid empty evidence is the only no-hit representation.

### 4.3 Deterministic evidence selection

The retrieval service identifies factual requests within the query and checks them against the reviewed evidence capabilities. Normalize text with Unicode NFKC and collapsed whitespace. English and Chinese rules separate requests, identify known artwork references, and distinguish a request from context that only introduces a subject. A dependent request can use that context to resolve its reference. A context statement alone does not select a chunk.

The request artwork ID remains the hard evidence filter. A recognized question about another work has no eligible evidence in this request. For a comparison, a supported request about the selected side can still select local evidence.

The released topics support these request facets:

| Published topic | Supported facet | Extent of support |
| --- | --- | --- |
| `material` | `medium`, `paint_medium`, `painting_support` | Catalog medium, paint family and support family. |
| `brushwork` | `brushwork_appearance` | Appearance of the described strokes. |
| `color` | `color_appearance` | The description's characterization of the colors. |
| `perspective` | `line_recession` | Qualitative description of receding lines. |
| `energy` | `visual_effect` | The museum's qualified account of the visual effect. |
| `background` | `background_objects` | Objects suggested by the described background marks. |

For example, the medium record can support the paint family and painting support in one query. It does not support dimensions, fiber or pigment identity. The description can support the appearance of brushwork without identifying the tool or the sequence in which paint was applied. Independent supported requests can still select evidence when another request lacks support.

Rank selected artwork chunks by the number of distinct published topics that support at least one request. Multiple requests that map to the same topic contribute one ranking vote. Discard zero-score chunks, sort by descending score and then ascending `chunk_id`, and keep at most `top_k`. Return the original reviewed excerpts and provenance fields.

The Python evaluation entry point can also return request bindings with normalized text offsets, scope, facet and selected chunk IDs. The private HTTP response retains only the dataset version and the deduplicated evidence list. The Go API still determines the answer status. Evaluation labels remain separate from runtime input.

### 4.4 Retrieval errors and Go API mapping

The retrieval service uses the common error envelope, with its own or forwarded request ID.

| Retrieval service condition | Retrieval service response | Go API public response |
| --- | --- | --- |
| Invalid fields, query or limit | `400 invalid_request` | `502 retrieval_invalid_response`; the Go API should never construct this request. |
| Unknown artwork | `404 artwork_not_found` | `502 retrieval_invalid_response` after the Go API has validated its own catalog. |
| Requested release differs from loaded release | `409 dataset_version_mismatch` | `502 retrieval_invalid_response`; expose the mismatch in logs, not as missing evidence. |
| Valid request with no eligible hit | `200` with `evidence: []` | A completed `insufficient` answer. |
| Temporarily unavailable index/service | `503 retrieval_unavailable` | `503 retrieval_unavailable`. |
| Unhandled retrieval service error | `500 internal_error` | `503 retrieval_unavailable`; record safe diagnostics. |
| Connection refused, reset or DNS failure | No valid response | `503 retrieval_unavailable`. |
| Go API's three-second retrieval budget expires | Request cancelled by the Go API | `504 retrieval_timeout`. |
| Invalid JSON, HTML, wrong version/scope, unknown fields in place of required fields, or oversized result | Any invalid response | `502 retrieval_invalid_response`. |
| Unexpected redirect or unlisted HTTP status | Unexpected response | `502 retrieval_invalid_response`; do not follow redirects. |

The Go API does not forward arbitrary upstream messages. A configuration or contract defect is not repaired by user retry; `retrieval_invalid_response` has `retryable: false`. Retrieval unavailability and deadlines have `retryable: true`.

### 4.5 Local retrieval transport profile

The retrieval service's local HTTP implementation requires a single decimal `Content-Length` and accepts at most 8,192 body bytes. Missing, invalid or duplicate length headers, or any `Transfer-Encoding`, return `400 invalid_request`. Send the JSON as a byte buffer with a known length. Chunked bodies are outside this local transport profile. Compressed request bodies return `415 unsupported_media_type`.

Use a single `Content-Type: application/json` header, optionally with `charset=utf-8`. Duplicate content-type headers, other charsets and unknown media-type parameters return `415 unsupported_media_type`. Every response includes `Content-Length`, `X-Request-ID` and `Connection: close`. The retrieval service also sends `Cache-Control: no-store`. A HEAD request receives the applicable status and headers without a response body, as required by HTTP.

The retrieval service echoes one nonblank printable ASCII `X-Request-ID` value of up to 128 characters; missing, invalid or duplicate values cause the retrieval service to generate a new ID. The error body's `request_id` always matches the response header.

The local server bounds socket reads to five seconds and rejects incomplete bodies as `400 invalid_request` when a response is still possible. The Go API's separate three-second retrieval deadline remains authoritative for its call. No automatic upstream retry, content fetch or model inference occurs in the retrieval service.

## 5. Versioned source release and evaluation data

### 5.1 Seed ownership and file schema

Jason Zichen He publishes `knowledge/data/aic-seed-v0.1.json`. The Go API and the retrieval service mount the same immutable file read-only. The JSON root contains exactly `dataset_version`, `artworks`, `sources` and `chunks`. All four are required: the version is a nonempty string and the other three fields are arrays of the object types defined below. The release contains two artwork records, two source records and four chunks. No live museum fetch is required for startup or visitor requests.

- `artworks` contains the `Artwork` records from section 2.1.
- Each `Source` has `source_id`, `artwork_id`, `source_title`, `source_url`, `source_language`, `institution`, `retrieved_at` and `terms_url`. All are nonempty strings; `retrieved_at` is the actual UTC fetch time recorded by Jason Zichen He when building the artifact, not an example timestamp.
- Each `Chunk` has `chunk_id`, `artwork_id`, `source_id`, `source_locator`, `excerpt`, `license`, `license_url`, `attribution`, `normalization` and `topics`. String fields follow the evidence rules. `topics` is a nonempty array of unique names from section 4.3.
- `source_id` is `aic-api-28560` or `aic-api-11723`. Source title is the artwork title followed by ` — Art Institute of Chicago API record`. `institution` and chunk attribution are `Art Institute of Chicago`. `terms_url` is `https://www.artic.edu/terms`.

Evidence is a projection of a chunk joined to its source. The source and chunk artwork IDs must agree. Startup validation checks every reference, unique ID and slug, exact version, language, URL, license and topic. A missing or malformed seed fails startup/readiness; do not start with an empty substitute dataset. For the released file, record its SHA-256 in the implementation handoff so Jinqing Zhang and Jason Zichen He can confirm both services loaded identical bytes.

The [museum API documentation](https://api.artic.edu/docs/#artworks) assigns CC BY 4.0 to `description` and CC0 1.0 to the other artwork API fields, subject to the museum's terms. Therefore record licensing per chunk, not a single blanket license for the whole source. Use `https://creativecommons.org/licenses/by/4.0/` for description chunks. Review images separately. This contract records the source basis; Jason Zichen He's publication task still verifies the captured material and attribution.

### 5.2 Frozen evidence

The material chunks use `data.medium_display` exactly, with `normalization: "none"` and the CC0 fields shown in the request trace. The second work has the same medium, so artwork isolation cannot be judged by answer text alone.

The two description chunks contain these short passages:

| Chunk | Source locator | Reviewed English excerpt |
| --- | --- | --- |
| `aic-28560-description-01` | `data.description, paragraph 1` | With its bold colors, thick and broken brushwork, and sharply receding lines, the picture might suggest a nervous energy. |
| `aic-11723-description-01` | `data.description, paragraph 1` | In the background, Morisot’s soft, feathery brushstrokes suggest a floral-patterned bedspread and wallpaper. |

The sources are the official [Bedroom record](https://api.artic.edu/api/v1/artworks/28560) and [Woman at Her Toilette record](https://api.artic.edu/api/v1/artworks/11723). Both description chunks use CC BY 4.0 and museum attribution. For the Bedroom, use normalization text `HTML removed; whitespace normalized.` For the Morisot passage, use `HTML removed; whitespace normalized; the source's split floral ligature normalized to floral.` The latter makes a mechanical correction visible; it is not a translation or a new museum claim.

The Bedroom excerpt supports the museum's qualified interpretation of nervous energy. Do not change “might suggest” into a claim about the artist's intentions or mental health. The Morisot excerpt supports a description of brushwork and suggested background objects, not a definitive identification of the sitter. Jason Zichen He should preserve raw source snapshots in the later data-preparation workflow so extraction is reviewable; only the reviewed seed is mounted at runtime.

### 5.3 Frozen evaluation file

Jason Zichen He publishes `eval/aic-seed-v0.1.tests.json` with root fields `dataset_version`, `cases` and `cross_artwork_checks`. The version is a nonempty string; `cases` is an array of six regular-case objects and `cross_artwork_checks` is an array containing the comparison object below. Each regular case has `case_id`, `artwork_id`, `question`, `answer_language`, `expected_status`, `expected_chunk_ids` and `rationale`. All except `expected_chunk_ids` are nonempty strings; that field is an array of unique chunk-ID strings. The last field is an English explanation for reviewers, not retrieval input.

`expected_chunk_ids` is the complete set required for an answered fixture. For an insufficient case it is empty because no chunk sufficiently answers the question. That empty gold set does not mean every relevant-but-inadequate retrieval must be empty.

One complete regular-case example:

```json
{
  "case_id": "bedroom-q1",
  "artwork_id": "aic-28560",
  "question": "这件作品用了什么材料？",
  "answer_language": "zh-CN",
  "expected_status": "answered",
  "expected_chunk_ids": ["aic-28560-medium"],
  "rationale": "The museum's medium field supports the material answer."
}
```

Implement all six canonical questions in [plan.md](plan.md) without changing their wording. The test file preserves the original Chinese question strings; all documentation and explanatory fields remain English.

The cross-artwork object is fully specified here:

```json
{
  "check_id": "same-query-brushwork",
  "query": "馆方如何描述这件作品的笔触？",
  "top_k": 2,
  "require_all_results_match_artwork_id": true,
  "variants": [
    {
      "artwork_id": "aic-28560",
      "expected_status": "answered",
      "expected_chunk_ids": ["aic-28560-description-01"],
      "expected_first_chunk_id": "aic-28560-description-01"
    },
    {
      "artwork_id": "aic-11723",
      "expected_status": "answered",
      "expected_chunk_ids": ["aic-11723-description-01"],
      "expected_first_chunk_id": "aic-11723-description-01"
    }
  ]
}
```

For the answer-level calls in that check, use `answer_language: "zh-CN"` and distinct logical message UUIDs. Also run the direct retrieval checks with `top_k: 1` to verify rank order. Preserve the six canonical cases even though one overlaps the cross-artwork wording; the cross check explicitly compares scopes.

## 6. Query preparation and generation boundary

### 6.1 Supported question matching

The Go API normalizes only for fixture selection: apply NFKC, lowercase Latin text, collapse whitespace, trim, and strip trailing `?`, `？`, `.` or `。` characters. Do not fuzzy-match or choose a fixture merely because a keyword overlaps. The stored question and idempotency identity retain the section 1 rules.

The fixture registry supports exactly these intents and phrasings in this release, plus the limited follow-ups below:

| Intent | Accepted standalone question strings | Artwork scope | Required chunk |
| --- | --- | --- | --- |
| Material | `What material is this artwork made of?`; `这件作品用了什么材料？` | Either artwork | Its own `-medium` chunk |
| Brushwork | `How does the museum describe this artwork's brushwork?`; `馆方如何描述这件作品的笔触？` | Either artwork | Its own `-description-01` chunk |
| Nervous energy | `Which elements might suggest nervous energy, according to the museum?`; `馆方认为，画面里的哪些元素可能让人感到紧张？` | Bedroom only | `aic-28560-description-01` |
| Known missing duration | `How many hours did Van Gogh actually spend painting the Chicago version?`; `梵高画完芝加哥收藏的这一版，实际花了多少小时？` | Bedroom only | None; known insufficient |
| Known missing identity | `What is the woman's real name?`; `画中女子的真实姓名是什么？` | Woman at Her Toilette only | None; known insufficient |

The Go API still calls the real retrieval service for recognized known-gap fixtures. The fixture outcome remains insufficient even if a fault-injection stub supplies a related chunk. No passage asserts the required hours or definitive name.

All standalone questions go to the retrieval service as their trimmed original text. Unsupported wording may retrieve evidence but must not silently select the nearest canned answer. For example, `Why did the artist choose this material?` matches the material retrieval topic but is outside the fixture registry, so the final status is `unsupported_question` when the medium chunk is present.

### 6.2 Follow-up rule

The Go API may inspect the last three completed turns from the same conversation, in order. The retrieval service never receives history. There are two explicit follow-up patterns:

| Follow-up question | Preconditions | Query sent to the retrieval service | Selected fixture |
| --- | --- | --- | --- |
| `What about the material?` or `那它是用什么做的？` | At least one completed turn exists in the current conversation | `What material is this artwork made of?` | Material for current artwork |
| `What about the brushwork?` or `那它的笔触呢？` | Same precondition | `How does the museum describe this artwork's brushwork?` | Brushwork for current artwork |

The original follow-up remains the turn's `question`, and the original requested answer language remains authoritative. With no completed context, do not apply the rewrite or select a fixture; send the original query to the retrieval service and apply the ordinary empty/nonempty outcome rules. No other implicit reference resolution is promised.

This is a deliberately narrow contextual integration check: it verifies artwork/history scoping and current evidence retrieval. It does not test general multi-turn understanding. Do not reuse previous evidence as this turn's citations. A later model-based query resolver must be evaluated separately, especially after artwork changes.

### 6.3 Internal adapter interface

The Go API owns an in-process interface; a separate generation HTTP service is unnecessary in stage one. Its input consists of:

- `artwork_id` and `dataset_version` from the conversation.
- `question` as submitted and `retrieval_query` as prepared.
- `answer_language`.
- `recent_turns`: at most three completed turns, oldest to newest, from this conversation only.
- `evidence`: the validated current retrieval result.

The output has `status`, `text` and `citation_chunk_ids`, or a typed error. It has no URLs or source passages created by the adapter. The Go API sets `mode: "fixture"`, validates output and resolves the citation IDs to the current evidence. The adapter must never treat text inside sources or visitor questions as permission to change artwork scope, load URLs, execute commands, or reveal system configuration.

Use these reviewed answer meanings for the fixture registry. The Go API stores explicit English and Simplified Chinese response strings; Jason Zichen He reviews that they stay within the cited passage. The following copy is the default:

| Fixture | English text | Simplified Chinese text |
| --- | --- | --- |
| Material, either work | The museum records the work as an oil painting on canvas. | 馆方记录的材质是布面油画。 |
| Bedroom brushwork | The museum describes thick, broken brushwork. | 馆方描述了厚重、断续的笔触。 |
| Bedroom nervous energy | According to the museum, the vivid colors, thick and broken brushwork, and strongly receding lines may create a sense of nervous energy. | 馆方认为，鲜明的色彩、厚重而断续的笔触，以及明显向纵深延伸的线条，可能让画面显出紧张的活力。 |
| Morisot brushwork | The museum describes light, featherlike strokes that suggest a patterned bed covering and wallpaper in the background. | 馆方描述了柔和、羽毛般的笔触，暗示背景中带花卉图案的床罩和墙纸。 |
| Known missing duration | The material available in this demo does not state how many hours this version took to paint. | 本演示现有资料没有说明画完这一版本实际花了多少小时。 |
| Known missing identity | The material available in this demo does not establish the woman's real name. | 本演示现有资料无法确定画中女子的真实姓名。 |
| General insufficient | The material available in this demo does not provide enough evidence to answer this question. | 本演示现有资料不足以回答这个问题。 |
| Unsupported fixture | This demo uses fixed test answers, and this question is outside the supported examples. Try asking about the material or brushwork. | 本演示使用固定测试回答，暂未覆盖这个问题。可以试着询问材质或笔触。 |

The generator's citation list must exactly cover the required IDs for its selected answer fixture. Missing expected evidence is an insufficient outcome. A generator claiming `answered` with an unknown ID, wrong status, empty text, invalid language choice, or invalid citation structure is `502 generation_invalid_response`. Do not convert an invalid generator response into a reassuring factual answer.

## 7. Errors, time budgets and service health

### 7.1 Public error catalog

| HTTP | Code | Retryable | Visitor/client handling |
| --- | --- | --- | --- |
| 400 | `invalid_request` | false | Correct the request; no logical attempt was accepted. |
| 403 | `origin_not_allowed` | false | Fix the configured origin or client URL. |
| 404 | `artwork_not_found` | false | Show an unavailable artwork link. |
| 404 | `conversation_not_found` | false | Restore a valid session as described above. |
| 404 | `route_not_found` | false | Fix the URL. |
| 405 | `method_not_allowed` | false | Fix the HTTP method. |
| 409 | `idempotency_conflict` | false | Do not reuse the key for edited input. |
| 409 | `request_in_progress` | true | Reconcile with history; same attempt is running. |
| 409 | `conversation_busy` | true | Wait for the other request in this conversation. |
| 409 | `request_superseded` | false | Load current state; obsolete key cannot run. |
| 413 | `payload_too_large` | false | Reduce the body size. |
| 415 | `unsupported_media_type` | false | Send UTF-8 JSON. |
| 500 | `internal_error` | true | Generic unexpected Go API failure; preserve the key and check history. |
| 502 | `retrieval_invalid_response` | false | Integration defect; show a service error and request ID. |
| 502 | `generation_invalid_response` | false | Integration defect; no completed answer. |
| 503 | `retrieval_unavailable` | true | Offer retry after the retrieval service recovers. |
| 503 | `generation_unavailable` | true | Offer retry after the adapter recovers. |
| 503 | `service_not_ready` | true | Startup/readiness failure; wait for the service to become ready. |
| 504 | `retrieval_timeout` | true | Retrieval exceeded its deadline. |
| 504 | `generation_timeout` | true | Adapter exceeded its deadline. |
| 504 | `request_timeout` | true | Go API's total attempt deadline expired. |

The frontend does not show a technical error as “the museum has no information.” A public `502` is an actionable team integration issue even if a manual retry button is later made available after a fix.

### 7.2 Deadline behavior

| Boundary | Budget | On expiry |
| --- | --- | --- |
| Go API → retrieval service, including connection and body read | 3 seconds | Cancel the call; `504 retrieval_timeout`. |
| Fixture adapter | 2 seconds | Reject a late result; `504 generation_timeout`. |
| Entire accepted Go API attempt | 8 seconds | Mark failed; `504 request_timeout` unless a more specific failure has already been committed. |
| Frontend fetch | 12 seconds | Treat outcome as unknown and reconcile with history. |

All budgets use monotonic elapsed time. Each child receives the lesser of its own budget and the remaining parent budget. The Go API attempt timer starts when it reserves the request; basic body validation occurs before that. Do not sleep or retry upstream inside the attempt. A single winning completion/failure transition decides the result; late callbacks cannot append turns or replace a newer pending request.

The proxy must allow at least 12 seconds for API responses. The Go API should return a structured failure before the frontend's timeout during controlled tests; genuine network loss can still leave the browser uncertain. The adapter can be replaced with a controlled slow/failing implementation in tests without loading a model.

### 7.3 Health endpoints and configuration

The Go API and retrieval service each expose private `GET /healthz` for process liveness and `GET /readyz` for local readiness. They do not require visitor cookies or public Origin checks. Return:

```json
{
  "status": "ok",
  "dataset_version": "aic-seed-v0.1"
}
```

`healthz` can use `dataset_version: null` before loading. `readyz` returns 200 only after the local seed and required local state are initialized, otherwise `503 service_not_ready`. The Go API readiness does not require the retrieval service to be up: artwork cards must remain available during retrieval outages. The retrieval service readiness requires a loaded valid index. These endpoints are not proxied as public visitor routes.

Required implementation configuration:

| Setting | Service/component | Default or meaning |
| --- | --- | --- |
| `PUBLIC_BASE_URL` | Go API, web/QR setup | Actual origin, e.g. `http://192.168.1.20:8080`; no path or trailing slash. Exact origin allowlist entry. |
| `COOKIE_SECURE` | Go API | `false` only for the local HTTP profile; `true` for HTTPS. |
| `DATASET_VERSION` | Go API and retrieval service | `aic-seed-v0.1`; must match the seed. |
| `SEED_PATH` | Go API and retrieval service | Container path of the read-only seed; map the same repo file to both. |
| `RETRIEVAL_BASE_URL` | Go API | Compose target `http://retrieval:8001`, never a browser URL. |
| `GENERATION_MODE` | Go API | `fixture`; fail startup on an unsupported mode. |
| Service listen ports | Frontend, Go API and retrieval service | Host web `8080`; Go API container `8000`; retrieval container `8001`. |

The Go API should use an in-process store with a clock abstraction for expiry tests. The retrieval service can load all four chunks into memory. No shared database, migration framework, API key, model download or GPU runtime is part of local startup. The application binds required container listeners to `0.0.0.0`; only web port 8080 is published to the host.

Routine logs include request ID, service, artwork ID, dataset version, result code, elapsed times and retrieved chunk IDs. Omit question text, answer text and cookie values by default. Test fixtures may record synthetic payloads explicitly. Logs are diagnostic evidence; the frozen test file remains the definition of expected behavior.

## 8. Integration checks and contract changes

Before connecting services, each owner can work independently:

- Jeremiah Lim implements against the public examples, including a failed pending request and all three completed statuses.
- Jinqing Zhang implements against a retrieval service stub that returns valid evidence, empty results, wrong-artwork evidence, malformed JSON, a wrong version, delayed responses and unavailability.
- Jason Zichen He implements against the seed and direct retrieval calls, without a running Go service or model.

Then use the acceptance matrix in [plan.md](plan.md) against actual Python retrieval. In particular, prove the same material question and same brushwork question cannot cross artwork boundaries; prove a lost success response replays exactly once; prove technical faults never become evidence gaps; and prove a known gap stays insufficient even when the stub returns related evidence.

The paths and field names in this contract are frozen for local-demo-v0.1. Jinqing Zhang coordinates proposed public changes, Jason Zichen He coordinates evidence/data changes, and Jeremiah Lim reviews changes to rendering and recovery. Update all affected examples, seed/test versions and checks in the same change before consumers adopt it. Additive response metadata is compatible; renamed/removed fields, changed enum meanings, altered request validation, new auth rules or new replay semantics require an explicit contract revision.

Source or chunk-content changes always create a new dataset version. Stable chunk IDs can remain when their meaning is unchanged, but identity is the pair `(dataset_version, chunk_id)`. The Go API does not hot-swap a release under a live conversation. For this in-memory stage, changing the configured release requires restarting both services and beginning new conversations.

Future work may add a real cloud GPU generator, embeddings, persistent storage, streaming, additional languages and cross-artwork features. Each needs explicit behavior and acceptance criteria. Preserve the central boundaries: the server owns artwork scope, the retrieval service returns traceable evidence, the Go API validates citations, and the frontend distinguishes valid answer outcomes from service failures.
