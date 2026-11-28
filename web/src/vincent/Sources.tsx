import type { VincentCitation } from '../voice/vincentContract';
export type { VincentCitation, VincentEvidence } from '../voice/vincentContract';
import { Citation, safeHttps } from '../ui/Citation';

const labels = { artist_letter: 'Artist’s letter', museum_record: 'Museum source', guide_note: 'MuseBridge guide' };

export function VincentSources({ citations }: { citations: VincentCitation[] }) {
  return (
    <details className="vincent-sources">
      <summary>Sources <span>{citations.length}</span></summary>
      <p className="muted small">Read the original passages behind this reply.</p>
      <ul className="cites">
        {citations.map((c) => {
          if (c.kind === 'catalog') return <Citation key={c.fact_id} c={c} />;
          const source = safeHttps(c.source_url);
          const license = safeHttps(c.license_url);
          return (
            <li className="cite" key={c.chunk_id}>
              <p className="cite-kind">{labels[c.source_kind]}</p>
              <p className="cite-title">{c.source_title}</p>
              <blockquote lang={c.source_language}>{c.excerpt}</blockquote>
              <dl className="cite-meta">
                <div><dt>Where</dt><dd>{c.source_locator}</dd></div>
                <div><dt>Language</dt><dd>{c.source_language === 'en' ? 'English' : c.source_language}</dd></div>
                <div><dt>Credit</dt><dd>{c.attribution}</dd></div>
                <div><dt>{c.license === 'In Copyright' ? 'Rights' : 'License'}</dt><dd>
                  {license ? <a href={license} target="_blank" rel="noopener noreferrer">{c.license}</a> : c.license}
                </dd></div>
                {c.normalization !== 'none' && <div><dt>Note</dt><dd>{c.normalization}</dd></div>}
              </dl>
              {source && <a className="cite-link" href={source} target="_blank" rel="noopener noreferrer">
                {c.source_kind === 'guide_note' ? 'Underlying Getty record' : 'Open original source'} ↗
              </a>}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
