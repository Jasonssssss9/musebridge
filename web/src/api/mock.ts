// In-browser stand-in for the Go API, used by `npm run dev` until the real
// conversation endpoints exist. It follows docs/api-contract.md sections 2, 3
// and 6: same routes, same bodies, same idempotency state machine. State lives
// in localStorage so that, like the real server, it survives a page reload and
// keeps working on a request after the browser stops listening.
import {
  ARTWORKS, CHUNKS, DATASET_VERSION, FIXTURES, FOLLOW_UPS, GENERAL_INSUFFICIENT, TOPIC_ALIASES, UNSUPPORTED,
  type Fixture,
} from './mockData';
import type { Answer, AnswerLanguage, Conversation, ErrorBody, Turn } from './types';
import { uuidv4 } from './uuid';

export type Fault =
  | 'none' | 'slow' | 'unavailable' | 'timeout' | 'invalid' | 'lost_response' | 'browser_timeout' | 'session_lost';

export const FAULTS: { id: Fault; label: string }[] = [
  { id: 'none', label: 'Normal replies' },
  { id: 'slow', label: 'Slow reply (4 s)' },
  { id: 'unavailable', label: '503 retrieval_unavailable (retryable)' },
  { id: 'timeout', label: '504 retrieval_timeout (retryable)' },
  { id: 'invalid', label: '502 retrieval_invalid_response (not retryable)' },
  { id: 'lost_response', label: 'Answer saved, reply lost on the network' },
  { id: 'browser_timeout', label: 'Server takes 15 s (past the 12 s browser wait)' },
  { id: 'session_lost', label: 'Server restarted: conversation gone (404)' },
];

interface Rec {
  question: string;
  answer_language: AnswerLanguage;
  state: 'processing' | 'failed' | 'completed' | 'superseded';
  last_error: ErrorBody | null;
  turn_id: string | null;
  finish_at: number;
  planned_error: { status: number; body: ErrorBody } | null;
}
interface Conv {
  conversation: Conversation;
  turns: Turn[];
  records: Record<string, Rec>;
  pending: string | null; // client_message_id of the latest unfinished request
}
interface State {
  convs: Record<string, Conv>; // by conversation_id
  fault: Fault;
}

const KEY = 'musebridge.mock.v1';
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

// ---- fixture answers (contract 2.6, 6.1, 6.2) ------------------------------

function normalize(q: string): string {
  return q.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim().replace(/[?.。]+$/u, '').trim();
}

function retrieve(artworkId: string, query: string): string[] {
  const q = query.normalize('NFKC').toLowerCase();
  const topics = Object.entries(TOPIC_ALIASES)
    .filter(([, aliases]) => aliases.some((a) => (/^[a-z ]+$/.test(a) ? new RegExp(`\\b${a}\\b`).test(q) : q.includes(a))))
    .map(([t]) => t);
  return CHUNKS.filter((c) => c.artwork_id === artworkId)
    .map((c) => ({ id: c.chunk_id, score: c.topics.filter((t) => topics.includes(t)).length }))
    .filter((c) => c.score > 0)
    .sort((a, b) => b.score - a.score || a.id.localeCompare(b.id))
    .slice(0, 2)
    .map((c) => c.id);
}

function answer(artworkId: string, question: string, lang: AnswerLanguage, completedTurns: number): Answer {
  const n = normalize(question);
  const applies = (f: Fixture) => f.artworks === 'any' || f.artworks.includes(artworkId);
  let fixture = FIXTURES.find((f) => applies(f) && f.questions.includes(n));
  let query = question;
  if (!fixture && completedTurns > 0) {
    const fu = FOLLOW_UPS.find((f) => f.questions.includes(n));
    if (fu) { fixture = fu.fixture; query = fu.fixture.questions[0]; }
  }
  const hits = retrieve(artworkId, query);
  const done = (status: Answer['status'], text: string, ids: string[] = []): Answer => ({
    status, mode: 'fixture', text,
    citations: ids.map((id) => {
      const { topics: _topics, ...evidence } = CHUNKS.find((c) => c.chunk_id === id)!;
      return evidence;
    }),
  });
  if (fixture && fixture.chunk === null) return done('insufficient', fixture.text(artworkId)[lang]);
  if (hits.length === 0) return done('insufficient', GENERAL_INSUFFICIENT[lang]);
  if (fixture && fixture.chunk) {
    const need = fixture.chunk(artworkId);
    return hits.includes(need) ? done('answered', fixture.text(artworkId)[lang], [need]) : done('insufficient', GENERAL_INSUFFICIENT[lang]);
  }
  return done('unsupported_question', UNSUPPORTED[lang]);
}

// ---- request lifecycle (contract 3.1) --------------------------------------

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
      const turn: Turn = {
        turn_id: uuidv4(),
        sequence: c.turns.length + 1,
        client_message_id: id,
        question: r.question,
        answer_language: r.answer_language,
        dataset_version: c.conversation.dataset_version,
        created_at: new Date().toISOString(),
        answer: answer(c.conversation.artwork_id, r.question, r.answer_language, c.turns.length),
      };
      c.turns.push(turn);
      r.state = 'completed';
      r.turn_id = turn.turn_id;
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

async function submit(convId: string, body: Record<string, unknown>, signal?: AbortSignal | null): Promise<Response> {
  const keys = Object.keys(body);
  const { client_message_id: rawId, question: rawQ, answer_language: lang } = body;
  if (
    keys.length !== 3 || typeof rawId !== 'string' || !UUID_RE.test(rawId) || typeof rawQ !== 'string' ||
    (lang !== 'en' && lang !== 'zh-CN') || CONTROL_RE.test(rawQ)
  ) return fail(400, 'invalid_request', false);
  const question = rawQ.trim();
  const len = [...question].length;
  if (len < 1 || len > 500) return fail(400, 'invalid_request', false);
  const id = rawId.toLowerCase();

  let s = load();
  settle(s);
  if (s.fault === 'session_lost') {
    s.convs = {};
    s.fault = 'none'; // one-shot: the next conversation works again
    save(s);
  }
  const c = liveConv(s, convId);
  if (!c) return fail(404, 'conversation_not_found', false);

  const rec = c.records[id];
  if (rec) {
    if (rec.question !== question || rec.answer_language !== lang) return fail(409, 'idempotency_conflict', false);
    if (rec.state === 'completed') return reply(200, { turn: c.turns.find((t) => t.turn_id === rec.turn_id) });
    if (rec.state === 'processing') return fail(409, 'request_in_progress', true);
    if (rec.state === 'superseded') return fail(409, 'request_superseded', false);
  } else {
    const cur = c.pending ? c.records[c.pending] : undefined;
    if (cur && cur.state === 'processing') return fail(409, 'conversation_busy', true);
    if (cur && cur.state === 'failed') cur.state = 'superseded';
  }

  const plan: Record<Fault, { ms: number; error: Rec['planned_error'] }> = {
    none: { ms: 700, error: null },
    slow: { ms: 4000, error: null },
    lost_response: { ms: 700, error: null },
    browser_timeout: { ms: 15000, error: null },
    session_lost: { ms: 700, error: null },
    unavailable: { ms: 500, error: { status: 503, body: err('retrieval_unavailable', 'The evidence service is temporarily unavailable.', true) } },
    timeout: { ms: 3000, error: { status: 504, body: err('retrieval_timeout', 'The evidence service did not answer in time.', true) } },
    invalid: { ms: 500, error: { status: 502, body: err('retrieval_invalid_response', 'The evidence service returned an invalid response.', false) } },
  };
  const { ms, error } = plan[s.fault];
  const dropReply = s.fault === 'lost_response';
  c.records[id] = {
    question, answer_language: lang, state: 'processing', last_error: null, turn_id: null,
    finish_at: Date.now() + ms, planned_error: error,
  };
  c.pending = id;
  save(s);

  await sleep(ms + 5, signal); // if the browser gives up here, settle() still finishes the attempt later
  s = load();
  settle(s);
  if (dropReply) throw new TypeError('Failed to fetch (mock: reply dropped after commit)');
  const after = s.convs[convId.toLowerCase()]?.records[id];
  if (!after) return fail(404, 'conversation_not_found', false);
  if (after.state === 'completed') {
    return reply(200, { turn: s.convs[convId.toLowerCase()].turns.find((t) => t.turn_id === after.turn_id) });
  }
  return reply(error?.status ?? 500, { request_id: `mock-${++reqCounter}`, error: after.last_error ?? err('internal_error', 'mock', true) });
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

  let body: Record<string, unknown> = {};
  if (method === 'POST') {
    try {
      const parsedBody: unknown = JSON.parse(String(init.body));
      if (typeof parsedBody !== 'object' || parsedBody === null || Array.isArray(parsedBody)) throw new Error('not an object');
      body = parsedBody as Record<string, unknown>;
    } catch {
      return fail(400, 'invalid_request', false);
    }
  }

  if (path === '/api/conversations' && method === 'POST') {
    const art = ARTWORKS.find((a) => a.artwork_id === body.artwork_id);
    if (!art || Object.keys(body).length !== 1) return fail(400, 'invalid_request', false);
    const existing = Object.values(s.convs).find(
      (c) => c.conversation.artwork_id === art.artwork_id && Date.now() < Date.parse(c.conversation.expires_at),
    );
    if (existing) return reply(200, { conversation: existing.conversation, created: false });
    const now = Date.now();
    const conversation: Conversation = {
      conversation_id: uuidv4(),
      artwork_id: art.artwork_id,
      dataset_version: DATASET_VERSION,
      created_at: new Date(now).toISOString(),
      expires_at: new Date(now + 24 * 3600 * 1000).toISOString(),
    };
    s.convs[conversation.conversation_id] = { conversation, turns: [], records: {}, pending: null };
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
          ? { client_message_id: c.pending, question: r.question, answer_language: r.answer_language, state: r.state, last_error: r.last_error }
          : null;
      return reply(200, { conversation: c.conversation, turns: c.turns, pending_request: pending });
    }
  }
  return fail(404, 'route_not_found', false);
}
