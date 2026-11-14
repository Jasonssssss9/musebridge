import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { CONTRACT, IS_MOCK } from '../api/client';
import {
  LANGUAGES, PREFERENCES, citationKey, type Artwork, type Language, type LanguagePreference, type Turn,
} from '../api/types';
import { cleanQuestion, useConversation, type PendingView } from '../chat/useConversation';
import {
  LANGUAGE_LABEL, UI_TEXT as t, errorText, initialAnswerLanguage, preferenceLabel, rememberAnswerLanguage, suggestions,
} from '../i18n';
import { Citation } from './Citation';

function TurnView({ turn }: { turn: Turn }) {
  const a = turn.answer;
  const llm = a.mode === 'llm';
  const note = !llm && a.status === 'insufficient' ? t.fixtureInsufficient : t.statusNote[a.status];
  return (
    <li className="turn">
      <p className="question"><span className="sr-only">{t.you}: </span>{turn.question}</p>
      <article className={`answer answer-${a.status}`}>
        {/* Fixture answers keep the fixed-test-answer label; model answers say so and name their language. */}
        <p className="fixture">
          {llm ? <>{t.llmLabel} · {t.answeredIn} <span lang={turn.answer_language}>{LANGUAGE_LABEL[turn.answer_language]}</span></> : t.fixtureLabel}
        </p>
        {/* A coverage note only. The reply itself is always shown in full (agent contract 2.4). */}
        {note && <p className="status">{note}</p>}
        <p className="answer-text" lang={turn.answer_language}>{a.text}</p>
        {a.citations.length > 0 && (
          <section className="cites">
            <h3>{t.sources}</h3>
            <ul>{a.citations.map((c) => <Citation key={citationKey(c)} c={c} />)}</ul>
          </section>
        )}
      </article>
    </li>
  );
}

/** The wait can be long with a real model, so say so after a few seconds instead of looking stuck. */
function Waiting({ label }: { label: string }) {
  const [long, setLong] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setLong(true), 8000);
    return () => clearTimeout(timer);
  }, []);
  return (
    <p className="waiting" role="status">
      <span className="dots" aria-hidden="true"><i /><i /><i /></span>
      {long ? t.waitingLong : label}
    </p>
  );
}

function PendingRow(props: { p: PendingView; onRetry: () => void; onEdit: () => void; onCheck: () => void }) {
  const { p } = props;
  return (
    <li className="turn">
      <p className="question"><span className="sr-only">{t.you}: </span>{p.question}</p>
      {p.phase === 'failed' && p.error ? (
        // A technical failure is never worded as "the museum has no information" (contract 7.1).
        <div className="failure" role="alert">
          <p>{errorText(p.error.code)}</p>
          {p.error.requestId && <p className="small muted">{t.reference}: <code>{p.error.requestId}</code></p>}
          <div className="actions">
            {p.error.retryable && <button className="btn" onClick={props.onRetry}>{t.retry}</button>}
            <button className="btn btn-quiet" onClick={props.onEdit}>{t.edit}</button>
          </div>
        </div>
      ) : p.phase === 'stalled' ? (
        <div className="failure" role="status">
          <p>{t.stalled}</p>
          <div className="actions"><button className="btn" onClick={props.onCheck}>{t.checkAgain}</button></div>
        </div>
      ) : (
        <Waiting label={p.phase === 'sending' ? t.sending : t.checking} />
      )}
    </li>
  );
}

export function Chat({ artwork }: { artwork: Artwork }) {
  const chat = useConversation(artwork.artwork_id);
  const [text, setText] = useState('');
  const [editing, setEditing] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);

  // Language selection.
  // Agent contract: the server stores the preference. `choice` is a selection the visitor has made
  // but not yet sent; it travels as language_override on the next new message, then the server's
  // stored value takes over again (agent contract 2.3).
  // Fixture contract: the browser remembers en / zh-CN and sends it with every question.
  const meta = chat.conversation?.agent ?? null;
  const [choice, setChoice] = useState<LanguagePreference | null>(null);
  const [fixtureLanguage, setFixtureLanguage] = useState<Language>(initialAnswerLanguage);
  const revision = meta?.revision;
  useEffect(() => setChoice(null), [revision, chat.conversation?.conversation_id]);

  const selected: LanguagePreference = CONTRACT === 'agent' ? choice ?? meta?.language_preference ?? 'auto' : fixtureLanguage;
  const unsentChoice = CONTRACT === 'agent' && choice !== null && choice !== meta?.language_preference;
  // The language example questions are shown in: the fixed selection, or what Auto would currently use.
  const exampleLanguage: Language = selected !== 'auto' ? selected : meta?.last_answer_language ?? meta?.auto_language ?? 'en';

  // Text handed back after a lost session, a state conflict or "Edit question".
  useEffect(() => {
    if (chat.restoredText === null) return;
    setText(chat.restoredText);
    chat.consumeRestoredText();
    box.current?.focus();
  }, [chat.restoredText, chat.consumeRestoredText]);

  const count = chat.turns.length;
  const phase = chat.pending?.phase;
  useEffect(() => {
    if (count > 0 || phase) end.current?.scrollIntoView({ block: 'end', behavior: 'smooth' });
  }, [count, phase]);

  const length = [...cleanQuestion(text)].length;
  const tooLong = length > 500;
  const ready = chat.canSend && length > 0 && !tooLong;

  const send = (q: string) => {
    if (!chat.canSend) return;
    // The choice is fixed at submission and reused unchanged on retry.
    chat.send(q, CONTRACT === 'agent' ? (unsentChoice ? choice : null) : fixtureLanguage);
    setText('');
    setEditing(false);
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (ready) send(text);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (ready) send(text);
    }
  };
  const onLanguage = (value: string) => {
    if (CONTRACT === 'agent') {
      setChoice(value as LanguagePreference);
    } else {
      setFixtureLanguage(value as Language);
      rememberAnswerLanguage(value as Language);
    }
  };

  return (
    <section className="chat" aria-label={t.chatHeading}>
      <h2>{t.chatHeading}</h2>

      {chat.sessionEnded && <p className="notice small" role="status">{t.sessionEnded}</p>}
      {chat.conversationChanged && <p className="notice small" role="status">{t.conversationChanged}</p>}
      {chat.status === 'starting' && <p className="muted small" role="status">{t.chatStarting}</p>}
      {chat.status === 'unavailable' && (
        <div className="notice" role="alert">
          <p>{t.chatUnavailable}</p>
          <div className="actions"><button className="btn" onClick={chat.reopen}>{t.tryAgain}</button></div>
        </div>
      )}

      {chat.status === 'ready' && count === 0 && !chat.pending && <p className="muted small">{t.chatEmpty}</p>}

      <ol className="turns">
        {chat.turns.map((turn) => <TurnView key={turn.turn_id} turn={turn} />)}
        {chat.pending && (
          <PendingRow
            p={chat.pending}
            onRetry={chat.retry}
            onCheck={chat.checkAgain}
            onEdit={() => { setEditing(true); chat.edit(); }}
          />
        )}
      </ol>
      <div ref={end} />

      {chat.status === 'ready' && (
        <div className="composer">
          {chat.canSend && !editing && (
            <div className="chips" aria-label={t.suggestions}>
              {suggestions(artwork.artwork_id, exampleLanguage, CONTRACT === 'agent').map((q) => (
                <button key={q} type="button" className="chip" lang={exampleLanguage} onClick={() => send(q)}>{q}</button>
              ))}
            </div>
          )}
          {editing && <p className="small muted">{t.editingNote}</p>}
          <form onSubmit={onSubmit}>
            <textarea
              ref={box}
              rows={1}
              value={text}
              placeholder={t.placeholder}
              aria-label={t.placeholder}
              aria-invalid={tooLong}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={onKey}
            />
            <button className="btn" type="submit" disabled={!ready}>{t.send}</button>
          </form>
          {length > 400 && <p className={`small ${tooLong ? 'error' : 'muted'}`}>{tooLong ? t.tooLong : ''} {length}/500</p>}
          <div className="langrow">
            <label className="lang">
              <span className="small muted">{t.answerLanguage}</span>
              <select value={selected} onChange={(e) => onLanguage(e.target.value)}>
                {(CONTRACT === 'agent' ? PREFERENCES : LANGUAGES.filter((l) => l === 'en' || l === 'zh-CN')).map((p) => (
                  // Agent: each language under its own name. Fixture: English labels, as before.
                  <option key={p} value={p}>{CONTRACT === 'agent' ? preferenceLabel(p) : t.langName[p]}</option>
                ))}
              </select>
            </label>
            <p className="small muted langhint">
              {unsentChoice ? t.languageNext : selected === 'auto' ? t.languageAutoHint : ''}
            </p>
          </div>
          {IS_MOCK && meta && (
            <p className="small muted mockmeta" data-testid="conversation-meta">
              revision {meta.revision} · preference {meta.language_preference} · auto {meta.auto_language} · last {meta.last_answer_language ?? 'none'}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
