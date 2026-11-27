import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, getArtwork } from '../api/client';
import type { Artwork } from '../api/types';
import { UI_TEXT as t } from '../i18n';
import { Link } from './App';
import { Chat } from './Chat';
import { safeHttps } from './Citation';

type Load = { state: 'loading' } | { state: 'ok'; artwork: Artwork } | { state: 'not_found' } | { state: 'error' };

function ArtworkImage({ art }: { art: Artwork }) {
  const dialog = useRef<HTMLDialogElement>(null);
  if (!art.image_url) return <div className="plate plate-empty">{t.noImage}</div>;
  return (
    <figure className="artwork-image">
      <button className="image-preview" aria-label={`Enlarge image of ${art.title}`} onClick={() => dialog.current?.showModal()}>
        <img src={art.image_url} alt={art.image_alt ?? art.title} />
        <span aria-hidden="true">Enlarge ↗</span>
      </button>
      {art.image_credit && <figcaption>{art.image_credit} {art.image_source_url &&
        <a href={art.image_source_url} target="_blank" rel="noopener noreferrer">Image source{art.image_license && ` · ${art.image_license}`}</a>}
      </figcaption>}
      <dialog className="image-dialog" ref={dialog} aria-label={art.title}>
        <div className="image-dialog-header"><p>{art.title}</p><button className="btn btn-quiet" onClick={() => dialog.current?.close()}>Close</button></div>
        <img src={art.image_url} alt={art.image_alt ?? art.title} />
      </dialog>
    </figure>
  );
}

export function ArtworkPage({ slug }: { slug: string | null }) {
  const [load, setLoad] = useState<Load>({ state: slug === null ? 'not_found' : 'loading' });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    if (slug === null) return;
    let cancelled = false;
    setLoad({ state: 'loading' });
    getArtwork(slug).then(
      (artwork) => { if (!cancelled) setLoad({ state: 'ok', artwork }); },
      (e) => {
        if (cancelled) return;
        setLoad({ state: e instanceof ApiError && e.code === 'artwork_not_found' ? 'not_found' : 'error' });
      },
    );
    return () => { cancelled = true; };
  }, [slug, attempt]);

  useEffect(() => {
    document.title = load.state === 'ok' ? `${load.artwork.title} · MuseBridge` : 'MuseBridge';
  }, [load]);

  if (load.state === 'loading') {
    return <main className="page"><p className="muted" role="status">{t.loadingArtwork}</p></main>;
  }
  if (load.state !== 'ok') {
    const notFound = load.state === 'not_found';
    return (
      <main className="page">
        <div className="notice" role="alert">
          <h1>{notFound ? t.artworkNotFoundTitle : t.artworkErrorTitle}</h1>
          <p>{notFound ? t.artworkNotFoundBody : t.artworkErrorBody}</p>
          <div className="actions">
            {!notFound && <button className="btn" onClick={retry}>{t.tryAgain}</button>}
            <Link to="/" className="btn btn-quiet">{t.backToList}</Link>
          </div>
        </div>
      </main>
    );
  }

  const a = load.artwork;
  const record = safeHttps(a.source_url);
  return (
    <main className="page artwork">
      <section className="label" aria-label={a.title}>
        <ArtworkImage art={a} />
        <h1>{a.title}</h1>
        <p className="artist">{a.artist}, {a.date_display}</p>
        <p className="muted small">
          {a.museum} · {t.accession} {a.accession_number}
          {record && <> · <a href={record} target="_blank" rel="noopener noreferrer">{t.museumRecord}</a></>}
        </p>
      </section>
      <Chat artwork={a} />
    </main>
  );
}
