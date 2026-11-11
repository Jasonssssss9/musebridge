import { useEffect, useState, type MouseEvent, type ReactNode } from 'react';
import { IS_MOCK } from '../api/client';
import type { AnswerLanguage } from '../api/types';
import { UI_TEXT as t, initialAnswerLanguage, rememberAnswerLanguage } from '../i18n';
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
  const [answerLanguage, setAnswerLanguage] = useState<AnswerLanguage>(initialAnswerLanguage);

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const match = /^\/a\/([^/]+)\/?$/.exec(path);
  const slug = match ? decodeURIComponent(match[1]) : null;

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand">MuseBridge</Link>
        {IS_MOCK && <MockPanel />}
        <label className="lang">
          <span className="small muted">{t.answerLanguage}</span>
          <select
            value={answerLanguage}
            onChange={(e) => {
              const language = e.target.value as AnswerLanguage;
              setAnswerLanguage(language);
              rememberAnswerLanguage(language);
            }}
          >
            <option value="en">English</option>
            <option value="zh-CN">Chinese (Simplified)</option>
          </select>
        </label>
      </header>
      {/* key: a different artwork is a different page with its own conversation state */}
      {slug ? <ArtworkPage key={slug} slug={slug} answerLanguage={answerLanguage} /> : path === '/qr' ? <QrPage /> : <Home />}
    </div>
  );
}
