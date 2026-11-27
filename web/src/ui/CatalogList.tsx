import { useEffect, useState, type ReactNode } from 'react';
import { getArtworks } from '../api/client';
import type { Artwork } from '../api/types';

export function CatalogList({ children }: { children: (artworks: Artwork[]) => ReactNode }) {
  const [artworks, setArtworks] = useState<Artwork[] | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    getArtworks().then(
      (cards) => { if (active) setArtworks(cards); },
      () => { if (active) setError(true); },
    );
    return () => { active = false; };
  }, [attempt]);
  if (error) return (
    <div className="notice" role="alert">
      <p>The collection could not be loaded.</p>
      <button className="btn btn-quiet" onClick={() => { setError(false); setAttempt(attempt + 1); }}>Try again</button>
    </div>
  );
  if (!artworks) return <p className="muted" role="status">Loading the collection…</p>;
  return <>{children(artworks)}</>;
}
