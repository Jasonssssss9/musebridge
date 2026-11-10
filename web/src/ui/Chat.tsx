import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import type { AnswerLanguage, Artwork, Turn } from '../api/types';
import { cleanQuestion, useConversation, type PendingView } from '../chat/useConversation';
import { STRINGS, errorText, suggestions, type Strings } from '../i18n';
import { Citation } from './Citation';

function TurnView({ turn, t }: { turn: Turn; t: Strings }) {
  const a = turn.answer;
  return (
    <li className="turn">
      <p className="question"><span className="sr-only">{t.you}: </span>{turn.question}</p>
      <article className={`answer answer-${a.status}`}>
        {/* Every outcome is labelled as a fixed test answer (contract 2.5). */}
        <p className="fixture">{t.fixtureLabel}</p>
        {a.status !== 'answered' && (
          <p className="status">{a.status === 'insufficient' ? t.statusInsufficient : t.statusUnsupported}</p>
        )}
        <p className="answer-text" lang={turn.answer_language}>{a.text}</p>
        {a.citations.length > 0 && (
          <section className="cites">
            <h3>{t.sources}</h3>
            <ul>{a.citations.map((c) => <Citation key={c.chunk_id} c={c} t={t} />)}</ul>
          </section>
        )}
      </article>
    </li>
  );
}

function PendingRow(props: { p: PendingView; t: Strings; onRetry: () => void; onEdit: () => void; onCheck: () => void }) {
  const { p, t } = props;
  return (
    <li className="turn">
      <p className="question"><span className="sr-only">{t.you}: </span>{p.question}</p>
      {p.phase === 'failed' && p.error ? (
        // A technical failure is never worded as "the museum has no information" (contract 7.1).
        <div className="failure" role="alert">
          <p>{errorText(t, p.error.code)}</p>
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
        <p className="waiting" role="status">
          <span className="dots" aria-hidden="true"><i /><i /><i /></span>
          {p.phase === 'sending' ? t.sending : t.checking}
        </p>
      )}
    </li>
  );
}

export function Chat({ artwork, lang }: { artwork: Artwork; lang: AnswerLanguage }) {
  const t = STRINGS[lang];
  const chat = useConversation(artwork.artwork_id);
  const [text, setText] = useState('');
  const [editing, setEditing] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);

  // Text handed back after a lost session or "Edit question".
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
    chat.send(q, lang); // the language is fixed at submission and reused on retry
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

  return (
    <section className="chat" aria-label={t.chatHeading}>
      <h2>{t.chatHeading}</h2>

      {chat.sessionEnded && <p className="notice small" role="status">{t.sessionEnded}</p>}
      {chat.status === 'starting' && <p className="muted small" role="status">{t.chatStarting}</p>}
      {chat.status === 'unavailable' && (
        <div className="notice" role="alert">
          <p>{t.chatUnavailable}</p>
          <div className="actions"><button className="btn" onClick={chat.reopen}>{t.tryAgain}</button></div>
        </div>
      )}

      {chat.status === 'ready' && count === 0 && !chat.pending && <p className="muted small">{t.chatEmpty}</p>}

      <ol className="turns">
        {chat.turns.map((turn) => <TurnView key={turn.turn_id} turn={turn} t={t} />)}
        {chat.pending && (
          <PendingRow
            p={chat.pending}
            t={t}
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
              {suggestions(artwork.artwork_id, lang).map((q) => (
                <button key={q} type="button" className="chip" onClick={() => send(q)}>{q}</button>
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
        </div>
      )}
    </section>
  );
}
