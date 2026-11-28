import { UI_TEXT as t } from '../i18n';
import { Link } from './App';

/** Reserve the entry destination until the Vincent conversation UI is connected. */
export function VincentPage() {
  return (
    <main className="page">
      <section className="notice" aria-labelledby="vincent-heading">
        <h1 id="vincent-heading">{t.vincentTitle}</h1>
        <p>{t.vincentUnavailable}</p>
        <div className="actions">
          <Link to="/a/getty-103jnh" className="btn">{t.backToArtwork}</Link>
        </div>
      </section>
    </main>
  );
}
