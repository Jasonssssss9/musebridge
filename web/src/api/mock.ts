// In-browser stand-in for the Go API's agent contract (agent-demo-v0.3), used by
// `npm run dev` until the real endpoints exist. It follows docs/agent-api-contract.md
// sections 2 and 7 for routes, bodies, revisions and the idempotency rules, and
// sections 4.2 and 5.5 for how the reply language and status are derived.
//
// It is a keyword script, not a model: it shows every reply shape the page must
// render, and says nothing about real answer quality. State lives in localStorage
// so that, like the real server, it survives a reload and keeps working on a
// request after the browser stops listening.
import {
  ARTWORKS, CITATIONS, DATASET_VERSION, INTENTS, LANGUAGE_NAMES, PARTS, SETTING_CUE, SPANISH_CUE, TRANSLATE_CUE,
  type MockArtwork,
} from './mockData';
import { AGENT_CONTRACT, PREFERENCES, type ErrorBody, type Language, type LanguagePreference } from './types';
import { uuidv4 } from './uuid';

export type Fault =
  | 'none' | 'slow' | 'unavailable' | 'timeout' | 'invalid' | 'lost_response' | 'browser_timeout' | 'session_lost' | 'state_conflict';

export const FAULTS: { id: Fault; label: string }[] = [
  { id: 'none', label: 'Normal replies' },
  { id: 'slow', label: 'Slow reply (9 s)' },
  { id: 'unavailable', label: '503 generation_unavailable (retryable)' },
  { id: 'timeout', label: '504 generation_timeout (retryable)' },
  { id: 'invalid', label: '502 generation_invalid_response (not retryable)' },
  { id: 'lost_response', label: 'Answer saved, reply lost on the network' },
  { id: 'browser_timeout', label: 'Server takes 75 s (past the 70 s browser wait)' },
  { id: 'session_lost', label: 'Server restarted: conversation gone (404)' },
  { id: 'state_conflict', label: 'Another tab answered first (409 state conflict)' },
];

type Wire = Record<string, unknown>;
interface Rec {
  question: string;
  language_override: LanguagePreference | null;
  expected_revision: number;
  state: 'processing' | 'failed' | 'completed' | 'superseded';
  last_error: ErrorBody | null;
  turn_id: string | null;
  finish_at: number;
  planned_error: { status: number; body: ErrorBody } | null;
}
interface Conv {
  conversation: Wire & {
    conversation_id: string; artwork_id: string; expires_at: string; revision: number;
    language_preference: LanguagePreference; auto_language: Language; last_answer_language: Language | null;
  };
  turns: (Wire & { turn_id: string })[];
  parts: Record<string, string[]>; // turn_id -> reply part keys, so "translate that" can re-render it
  records: Record<string, Rec>;
  pending: string | null; // client_message_id of the latest unfinished request
}
interface State {
  convs: Record<string, Conv>; // by conversation_id
  fault: Fault;
}

const KEY = 'musebridge.mock.v2';
let memory: State | null = null;

function load(): State {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as State; // always re-read: another tab may have changed it
  } catch { /* storage blocked: stay in memory */ }
  return memory ?? (memory = { convs: {}, fault: 'none' });
}
function save(s: State): void {
  memory = s;
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* ignore */ }
}

export function getFault(): Fault { return load().fault; }
export function setFault(f: Fault): void { const s = load(); s.fault = f; save(s); }
export function resetMockServer(): void { save({ convs: {}, fault: 'none' }); }

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const CONTROL_RE = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;

let reqCounter = 0;
function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}
function fail(status: number, code: string, retryable: boolean, message = code): Response {
  return reply(status, { request_id: `mock-${++reqCounter}`, error: { code, message, retryable } });
}

function sleep(ms: number, signal?: AbortSignal | null): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); reject(new DOMException('Aborted', 'AbortError')); });
  });
}

// ---- scripted "agent" -------------------------------------------------------

function detectLanguage(text: string): Language | null {
  if (/[가-힯]/.test(text)) return 'ko';
  if (/[一-鿿]/.test(text)) return 'zh-CN';
  if (SPANISH_CUE.test(text)) return 'es';
  return /[a-z]/.test(text) ? 'en' : null;
}

interface Planned {
  parts: string[];
  language: Language;
  preference: LanguagePreference; // what to save as the conversation's preference
  detected: Language | null;
}

/** Decide the reply parts and language for one message (contract 4.2 rules 1–7, simplified). */
function plan(c: Conv, question: string, override: LanguagePreference | null): Planned {
  const q = question.normalize('NFKC').toLowerCase();
  const artworkId = c.conversation.artwork_id;

  const hits = INTENTS
    .map((i) => ({ i, at: q.search(i.cue) }))
    .filter((h) => h.at >= 0)
    .sort((a, b) => a.at - b.at)
    .map(({ i }) => (!i.only || i.only === artworkId ? i.part : i.elsewhere!));
  let parts = [...new Set(hits)];

  const named = LANGUAGE_NAMES.find((n) => n.cue.test(q))?.language ?? null;
  const translateTo = TRANSLATE_CUE.test(q) ? named : null;
  const setting: LanguagePreference | null =
    translateTo ? null : SETTING_CUE.test(q) && named ? named : /\bauto(matic)?\b|自动|자동/.test(q) && SETTING_CUE.test(q) ? 'auto' : null;
  // A message that is only a language instruction says nothing about the visitor's own language.
  const detected = (translateTo || setting) && parts.length === 0 ? null : detectLanguage(q);

  const candidate = override ?? c.conversation.language_preference;
  const effective = setting ?? candidate;
  const language = translateTo ?? (effective === 'auto' ? detected ?? c.conversation.auto_language : effective);

  if (translateTo) {
    const last = c.turns[c.turns.length - 1];
    parts = last ? c.parts[last.turn_id] ?? [] : [];
    if (parts.length === 0) parts = ['clarify_translation'];
  } else if (setting) {
    parts = [setting === 'auto' ? 'language_auto' : 'language_fixed', ...parts];
  } else if (parts.length === 0) {
    parts = [/[?？¿]|^(what|why|how|who|when|where|is|does|did)\b/.test(q) ? 'gap_generic' : 'chat_generic'];
  }
  // At most one clarification part (contract 5.4).
  let asked = false;
  parts = parts.filter((p) => PARTS[p].kind !== 'clarification' || (!asked && (asked = true)));
  return { parts, language, preference: setting ?? candidate, detected };
}

/** Assemble the public answer: join parts, collect sources, derive the status (contract 5.5). */
function assemble(artwork: MockArtwork, parts: string[], language: Language): Wire {
  const fill = (t: string) => t.replace('{title}', artwork.title).replace('{artist}', artwork.artist).replace('{date}', artwork.date_display);
  const kinds = parts.map((p) => PARTS[p].kind);
  const supported = kinds.includes('supported');
  const gap = kinds.some((k) => k === 'insufficient' || k === 'out_of_scope' || k === 'clarification');
  const status = supported ? (gap ? 'partially_answered' : 'answered')
    : kinds.includes('clarification') ? 'clarification_needed'
    : kinds.includes('insufficient') ? 'insufficient'
    : kinds.includes('out_of_scope') ? 'out_of_scope'
    : 'conversational';
  const ids = [...new Set(parts.map((p) => PARTS[p].cite?.(artwork.artwork_id)).filter((id): id is string => !!id))];
  return {
    mode: 'llm',
    status,
    text: parts.map((p) => fill(PARTS[p].text[language])).join('\n\n'),
    citations: supported ? ids.map((id) => CITATIONS[id]) : [],
  };
}

function commitTurn(c: Conv, clientMessageId: string, question: string, override: LanguagePreference | null): Wire & { turn_id: string } {
  const artwork = ARTWORKS.find((a) => a.artwork_id === c.conversation.artwork_id)!;
  const p = plan(c, question, override);
  const turn = {
    turn_id: uuidv4(),
    sequence: c.turns.length + 1,
    client_message_id: clientMessageId,
    question,
    language_override: override,
    answer_language: p.language,
    dataset_version: DATASET_VERSION,
    created_at: new Date().toISOString(),
    answer: assemble(artwork, p.parts, p.language),
  };
  c.turns.push(turn);
  c.parts[turn.turn_id] = p.parts;
  c.conversation.revision = turn.sequence;
  c.conversation.language_preference = p.preference;
  c.conversation.last_answer_language = p.language;
  if (p.detected) c.conversation.auto_language = p.detected;
  return turn;
}

// ---- request lifecycle (contract 2.3) ---------------------------------------

/** Finish any attempt whose time is up, whether or not a browser is still waiting. */
function settle(s: State): void {
  const now = Date.now();
  for (const c of Object.values(s.convs)) {
    const id = c.pending;
    const r = id ? c.records[id] : undefined;
    if (!id || !r || r.state !== 'processing' || r.finish_at > now) continue;
    if (r.planned_error) {
      r.state = 'failed';
      r.last_error = r.planned_error.body;
    } else {
      r.turn_id = commitTurn(c, id, r.question, r.language_override).turn_id;
      r.state = 'completed';
      c.pending = null;
    }
  }
  save(s);
}

function liveConv(s: State, id: string): Conv | null {
  const c = s.convs[id.toLowerCase()];
  if (!c) return null;
  if (Date.now() >= Date.parse(c.conversation.expires_at)) return null;
  return c;
}

const err = (code: string, message: string, retryable: boolean): ErrorBody => ({ code, message, retryable });

async function submit(convId: string, body: Wire, signal?: AbortSignal | null): Promise<Response> {
  const { contract_version, client_message_id: rawId, question: rawQ, language_override: lang, expected_revision: rev } = body;
  if (
    Object.keys(body).length !== 5 || typeof contract_version !== 'string' || typeof rawId !== 'string' || !UUID_RE.test(rawId) ||
    typeof rawQ !== 'string' || CONTROL_RE.test(rawQ) || !(lang === null || (PREFERENCES as readonly unknown[]).includes(lang)) ||
    typeof rev !== 'number' || !Number.isInteger(rev) || rev < 0
  ) return fail(400, 'invalid_request', false);
  if (contract_version !== AGENT_CONTRACT) return fail(409, 'agent_contract_mismatch', false);
  const question = rawQ.trim();
  const len = [...question].length;
  if (len < 1 || len > 500) return fail(400, 'invalid_request', false);
  const id = rawId.toLowerCase();
  const override = lang as LanguagePreference | null;

  let s = load();
  settle(s);
  if (s.fault === 'session_lost') {
    s.convs = {};
    s.fault = 'none'; // one-shot: the next conversation works again
    save(s);
  }
  const c = liveConv(s, convId);
  if (!c) return fail(404, 'conversation_not_found', false);
  const current = c.pending ? c.records[c.pending] : undefined;
  if (s.fault === 'state_conflict' && !(current && current.state === 'processing')) {
    // Pretend another tab asked something first, so this submission's revision is stale.
    commitTurn(c, uuidv4(), 'What is it made of?', null);
    s.fault = 'none';
    save(s);
  }

  // Same order as contract 2.3: changed payload, replay, in progress, stale revision, superseded, busy.
  const rec = c.records[id];
  if (rec && (rec.question !== question || rec.language_override !== override || rec.expected_revision !== rev)) {
    return fail(409, 'idempotency_conflict', false);
  }
  if (rec?.state === 'completed') {
    return reply(200, { conversation: c.conversation, turn: c.turns.find((t) => t.turn_id === rec.turn_id) });
  }
  if (rec?.state === 'processing') return fail(409, 'request_in_progress', true);
  if (rev !== c.conversation.revision) return fail(409, 'conversation_state_conflict', false);
  if (rec?.state === 'superseded') return fail(409, 'request_superseded', false);
  if (!rec) {
    if (current && current.state === 'processing') return fail(409, 'conversation_busy', true);
    if (current && current.state === 'failed') current.state = 'superseded';
  }

  const plans: Record<Fault, { ms: number; error: Rec['planned_error'] }> = {
    none: { ms: 900, error: null },
    slow: { ms: 9000, error: null },
    lost_response: { ms: 900, error: null },
    browser_timeout: { ms: 75000, error: null },
    session_lost: { ms: 900, error: null },
    state_conflict: { ms: 900, error: null },
    unavailable: { ms: 600, error: { status: 503, body: err('generation_unavailable', 'The model service is temporarily unavailable.', true) } },
    timeout: { ms: 3000, error: { status: 504, body: err('generation_timeout', 'The model did not answer in time.', true) } },
    invalid: { ms: 600, error: { status: 502, body: err('generation_invalid_response', 'The model returned an invalid response.', false) } },
  };
  const { ms, error } = plans[s.fault];
  const dropReply = s.fault === 'lost_response';
  c.records[id] = {
    question, language_override: override, expected_revision: rev, state: 'processing', last_error: null, turn_id: null,
    finish_at: Date.now() + ms, planned_error: error,
  };
  c.pending = id;
  save(s);

  await sleep(ms + 5, signal); // if the browser gives up here, settle() still finishes the attempt later
  s = load();
  settle(s);
  if (dropReply) throw new TypeError('Failed to fetch (mock: reply dropped after commit)');
  const after = s.convs[convId.toLowerCase()];
  const done = after?.records[id];
  if (!after || !done) return fail(404, 'conversation_not_found', false);
  if (done.state === 'completed') {
    return reply(200, { conversation: after.conversation, turn: after.turns.find((t) => t.turn_id === done.turn_id) });
  }
  return reply(error?.status ?? 500, { request_id: `mock-${++reqCounter}`, error: done.last_error ?? err('internal_error', 'mock', true) });
}

export async function mockFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  const signal = init.signal;
  await sleep(120, signal);
  const s = load();
  settle(s);

  let m = /^\/api\/artworks\/([^/?]+)$/.exec(path);
  if (m && method === 'GET') {
    const art = ARTWORKS.find((a) => a.slug === decodeURIComponent(m![1]));
    return art ? reply(200, { artwork: art }) : fail(404, 'artwork_not_found', false);
  }

  let body: Wire = {};
  if (method === 'POST') {
    try {
      const parsedBody: unknown = JSON.parse(String(init.body));
      if (typeof parsedBody !== 'object' || parsedBody === null || Array.isArray(parsedBody)) throw new Error('not an object');
      body = parsedBody as Wire;
    } catch {
      return fail(400, 'invalid_request', false);
    }
  }

  if (path === '/api/conversations' && method === 'POST') {
    const art = ARTWORKS.find((a) => a.artwork_id === body.artwork_id);
    if (!art || Object.keys(body).length !== 2 || typeof body.contract_version !== 'string') return fail(400, 'invalid_request', false);
    if (body.contract_version !== AGENT_CONTRACT) return fail(409, 'agent_contract_mismatch', false);
    const existing = Object.values(s.convs).find(
      (c) => c.conversation.artwork_id === art.artwork_id && Date.now() < Date.parse(c.conversation.expires_at),
    );
    if (existing) return reply(200, { conversation: existing.conversation, created: false });
    const now = Date.now();
    const conversation: Conv['conversation'] = {
      contract_version: AGENT_CONTRACT,
      conversation_id: uuidv4(),
      artwork_id: art.artwork_id,
      dataset_version: DATASET_VERSION,
      created_at: new Date(now).toISOString(),
      expires_at: new Date(now + 24 * 3600 * 1000).toISOString(),
      revision: 0,
      language_preference: 'auto',
      auto_language: 'en',
      last_answer_language: null,
    };
    s.convs[conversation.conversation_id] = { conversation, turns: [], parts: {}, records: {}, pending: null };
    save(s);
    return reply(200, { conversation, created: true });
  }

  m = /^\/api\/conversations\/([^/?]+)\/messages$/.exec(path);
  if (m) {
    if (!UUID_RE.test(m[1])) return fail(400, 'invalid_request', false);
    if (method === 'POST') return submit(m[1], body, signal);
    if (method === 'GET') {
      const c = liveConv(s, m[1]);
      if (!c) return fail(404, 'conversation_not_found', false);
      const r = c.pending ? c.records[c.pending] : undefined;
      const pending =
        c.pending && r && (r.state === 'processing' || r.state === 'failed')
          ? {
            contract_version: AGENT_CONTRACT, client_message_id: c.pending, question: r.question,
            language_override: r.language_override, expected_revision: r.expected_revision, state: r.state, last_error: r.last_error,
          }
          : null;
      return reply(200, { conversation: c.conversation, turns: c.turns, pending_request: pending });
    }
  }
  return fail(404, 'route_not_found', false);
}
