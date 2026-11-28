import { readFileSync } from 'node:fs';
const examples = JSON.parse(readFileSync(new URL('../../docs/contracts/vincent-examples.json', import.meta.url)));
export const example = (name) => structuredClone(examples.schema_cases.find((c) => c.name === name).payload);
export const opened = example('vincent_created');
export const completed = example('public_completed_turn');
export const ID = opened.conversation.conversation_id;
export const message = {
  question: completed.turn.question, clientMessageId: completed.turn.client_message_id, expectedRevision: 0, languageOverride: null,
};
export const emptyHistory = () => ({ conversation: structuredClone(opened.conversation), turns: [], pending_request: null });
export const savedHistory = () => ({ conversation: structuredClone(completed.conversation), turns: [structuredClone(completed.turn)], pending_request: null });
export const pendingHistory = (error = null) => ({ ...emptyHistory(), pending_request: {
  contract_version: 'agent-demo-v0.4', client_message_id: message.clientMessageId, question: message.question,
  language_override: null, expected_revision: 0, state: error ? 'failed' : 'processing', last_error: error,
} });
