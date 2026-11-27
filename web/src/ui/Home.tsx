import { UI_TEXT as t } from '../i18n';
import { Link } from './App';
import { CatalogList } from './CatalogList';

export function Home() {
  return (
    <main className="page home">
      <div className="home-heading">
        <div><h1>{t.appTagline}</h1><p className="muted">{t.homeIntro}</p></div>
        <Link to="/qr" className="btn btn-quiet">QR cards</Link>
      </div>
      <CatalogList>{(works) => (
        <ul className="worklist">
          {works.map((w) => (
            <li key={w.slug}>
              <Link to={`/a/${w.slug}`} className="workcard">
                {w.image_url && <img src={w.image_url} alt="" loading="lazy" />}
                <div className="workcard-label">
                  <span className="workcard-title">{w.title}</span>
                  <span className="muted">{w.artist}, {w.date_display}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}</CatalogList>
    </main>
  );
}
