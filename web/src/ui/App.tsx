import { useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import { IS_MOCK } from '../api/client';
import type { AnswerLanguage } from '../api/types';
import { STRINGS, initialLanguage, rememberLanguage } from '../i18n';
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
  const [lang, setLang] = useState<AnswerLanguage>(initialLanguage);
  const t = STRINGS[lang];

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const match = /^\/a\/([^/]+)\/?$/.exec(path);
  const slug = match ? decodeURIComponent(match[1]) : null;

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">MuseBridge</Link>
        {IS_MOCK && <MockPanel />}
        <label className="lang">
          <span className="sr-only">{t.language}</span>
          <select
            value={lang}
            onChange={(e) => {
              const l = e.target.value as AnswerLanguage;
              setLang(l);
              rememberLanguage(l);
            }}
          >
            <option value="en">English</option>
            <option value="zh-CN">简体中文</option>
          </select>
        </label>
      </header>
      {/* key: a different artwork is a different page with its own conversation state */}
      {slug ? <ArtworkPage key={slug} slug={slug} lang={lang} /> : path === '/qr' ? <QrPage lang={lang} /> : <Home lang={lang} />}
    </div>
  );
}
