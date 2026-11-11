// Pending submissions and the last artwork conversation are scoped to this tab.
import type { Submission } from '../api/client';

const draftKey = (conversationId: string) => 'musebridge.draft.' + conversationId;
const conversationKey = (artworkId: string) => 'musebridge.conversation.' + artworkId;

interface DraftRecord extends Submission {
  previous?: Submission;
}

function isSubmission(value: unknown): value is Submission {
  if (typeof value !== 'object' || value === null) return false;
  const draft = value as Partial<Submission>;
  return typeof draft.client_message_id === 'string' && typeof draft.question === 'string'
    && (draft.answer_language === 'en' || draft.answer_language === 'zh-CN');
}

export function loadDraft(conversationId: string): DraftRecord | null {
  try {
    const raw = sessionStorage.getItem(draftKey(conversationId));
    if (!raw) return null;
    const draft: unknown = JSON.parse(raw);
    if (!isSubmission(draft)) return null;
    const previous = (draft as DraftRecord).previous;
    return {
      client_message_id: draft.client_message_id, question: draft.question, answer_language: draft.answer_language,
      previous: isSubmission(previous) ? previous : undefined,
    };
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
    previous: previous ? {
      client_message_id: previous.client_message_id, question: previous.question, answer_language: previous.answer_language,
    } : undefined,
  });
}

export function saveDraft(conversationId: string, submission: Submission): void {
  const { client_message_id, question, answer_language } = submission;
  writeDraft(conversationId, { client_message_id, question, answer_language });
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
