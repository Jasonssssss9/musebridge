import { useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import { IS_MOCK } from '../api/client';
import { UI_TEXT as t } from '../i18n';
import { ArtworkPage } from './ArtworkPage';
import { Home } from './Home';
import { MockPanel } from './MockPanel';
import { QrPage } from './QrPage';

/** In-app navigation without a page reload, so switching artworks mid-request can be exercised. */
export function navigate(path: string): void {
  window.history.pushState(null, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function Link({ to, className, children }: { to: string; className?: string; children: ReactNode }) {
  const onClick = (e: MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} className={className} onClick={onClick}>{children}</a>;
}

export function App() {
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const match = /^\/a\/([^/]+)\/?$/.exec(path);
  let slug: string | null = null;
  if (match) {
    try { slug = decodeURIComponent(match[1]); } catch { /* Show the unavailable artwork page. */ }
  }

  const artworkRoute = match !== null;
  useEffect(() => {
    // ArtworkPage owns its asynchronous title; other routes must clear a previous artwork's title.
    if (!artworkRoute) document.title = path === '/qr' ? `${t.qrTitle} · MuseBridge` : 'MuseBridge';
  }, [artworkRoute, path]);

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">MuseBridge</Link>
        {IS_MOCK && <MockPanel />}
      </header>
      {/* key: a different artwork is a different page with its own conversation state */}
      {match ? <ArtworkPage key={slug ?? match[1]} slug={slug} /> : path === '/qr' ? <QrPage /> : <Home />}
    </div>
  );
}
