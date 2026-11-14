import { useCallback, useEffect, useState } from 'react';
import { ApiError, getArtwork } from '../api/client';
import type { Artwork } from '../api/types';
import { UI_TEXT as t } from '../i18n';
import { Link } from './App';
import { Chat } from './Chat';
import { safeHttps } from './Citation';

type Load = { state: 'loading' } | { state: 'ok'; artwork: Artwork } | { state: 'not_found' } | { state: 'error' };

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
  const image = a.image_url ? safeHttps(a.image_url) : null;
  return (
    <main className="page artwork">
      <section className="label" aria-label={a.title}>
        {/* This release ships no images (contract 2.1): show a neutral plate, never a guessed URL. */}
        {image ? <img className="plate" src={image} alt={a.title} /> : <div className="plate plate-empty">{t.noImage}</div>}
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
