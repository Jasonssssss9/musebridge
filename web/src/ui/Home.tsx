import { UI_TEXT as t } from '../i18n';
import { Link } from './App';

// The two released slugs (contract 1.3). Titles are loaded by each artwork page.
export const DEMO = [
  { slug: 'aic-28560', title: 'The Bedroom', by: 'Vincent van Gogh, 1889' },
  { slug: 'aic-11723', title: 'Woman at Her Toilette', by: 'Berthe Morisot, 1875–80' },
];

export function Home() {
  return (
    <main className="page home">
      <h1>{t.appTagline}</h1>
      <p className="muted">{t.homeIntro}</p>
      <ul className="worklist">
        {DEMO.map((w) => (
          <li key={w.slug}>
            <Link to={`/a/${w.slug}`} className="workcard">
              <span className="workcard-title">{w.title}</span>
              <span className="muted">{w.by}</span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="small"><Link to="/qr">{t.qrTitle}</Link></p>
    </main>
  );
}
