// The one unconfirmed submission per conversation, kept in sessionStorage so it
// survives a reload (contract 3.2). It holds the question and language only,
// never the visitor cookie. Server history always wins over this copy.
import type { Submission } from '../api/client';

const key = (conversationId: string) => `musebridge.draft.${conversationId}`;

export function loadDraft(conversationId: string): Submission | null {
  try {
    const raw = sessionStorage.getItem(key(conversationId));
    if (!raw) return null;
    const d = JSON.parse(raw) as Partial<Submission>;
    if (typeof d.client_message_id !== 'string' || typeof d.question !== 'string') return null;
    if (d.answer_language !== 'en' && d.answer_language !== 'zh-CN') return null;
    return { client_message_id: d.client_message_id, question: d.question, answer_language: d.answer_language };
  } catch {
    return null;
  }
}
export function saveDraft(conversationId: string, s: Submission): void {
  try { sessionStorage.setItem(key(conversationId), JSON.stringify(s)); } catch { /* server pending_request is the fallback */ }
}
export function clearDraft(conversationId: string): void {
  try { sessionStorage.removeItem(key(conversationId)); } catch { /* ignore */ }
}
