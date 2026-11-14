// Pending submissions and the last artwork conversation are scoped to this tab.
import type { Submission } from '../api/client';
import { PREFERENCES } from '../api/types';

// A retry must resend the original payload exactly, so the draft keeps every field of it.
const copy = (s: Submission): Submission => ({
  client_message_id: s.client_message_id, question: s.question,
  language_override: s.language_override, expected_revision: s.expected_revision,
});

const draftKey = (conversationId: string) => 'musebridge.draft.' + conversationId;
const conversationKey = (artworkId: string) => 'musebridge.conversation.' + artworkId;

interface DraftRecord extends Submission {
  previous?: Submission;
}

function isSubmission(value: unknown): value is Submission {
  if (typeof value !== 'object' || value === null) return false;
  const draft = value as Partial<Submission>;
  return typeof draft.client_message_id === 'string' && typeof draft.question === 'string'
    && (draft.language_override === null || (PREFERENCES as readonly unknown[]).includes(draft.language_override))
    && typeof draft.expected_revision === 'number' && Number.isInteger(draft.expected_revision) && draft.expected_revision >= 0;
}

export function loadDraft(conversationId: string): DraftRecord | null {
  try {
    const raw = sessionStorage.getItem(draftKey(conversationId));
    if (!raw) return null;
    const draft: unknown = JSON.parse(raw);
    if (!isSubmission(draft)) return null;
    const previous = (draft as DraftRecord).previous;
    return { ...copy(draft), previous: isSubmission(previous) ? copy(previous) : undefined };
  } catch {
    return null;
  }
}

function writeDraft(conversationId: string, draft: DraftRecord): void {
  try { sessionStorage.setItem(draftKey(conversationId), JSON.stringify(draft)); } catch { /* server pending_request is the fallback */ }
}

export function stageDraft(conversationId: string, submission: Submission): void {
  const draft = loadDraft(conversationId);
  // Keep the superseded candidate until the new submission is reconciled.
  const previous = draft?.client_message_id === submission.client_message_id ? draft.previous : draft;
  writeDraft(conversationId, {
    ...submission,
    previous: previous ? copy(previous) : undefined,
  });
}

export function saveDraft(conversationId: string, submission: Submission): void {
  writeDraft(conversationId, copy(submission));
}

export function clearDraft(conversationId: string, clientMessageId: string): void {
  const draft = loadDraft(conversationId);
  if (draft?.client_message_id === clientMessageId) {
    try { sessionStorage.removeItem(draftKey(conversationId)); } catch { /* ignore */ }
  } else if (draft?.previous?.client_message_id === clientMessageId) {
    const { previous: _previous, ...remaining } = draft;
    writeDraft(conversationId, remaining);
  }
}

export function lastConversationId(artworkId: string): string | null {
  try { return sessionStorage.getItem(conversationKey(artworkId)); } catch { return null; }
}

export function rememberConversation(artworkId: string, conversationId: string): void {
  try { sessionStorage.setItem(conversationKey(artworkId), conversationId); } catch { /* ignore */ }
}
