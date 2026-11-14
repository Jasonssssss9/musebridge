import type { CatalogFact, Citation as CitationRecord, Evidence } from '../api/types';
import { UI_TEXT as t } from '../i18n';

/** Only stored HTTPS URLs become links; anything else is shown as plain text (contract 2.5). */
export function safeHttps(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

function SourceLink({ url }: { url: string }) {
  const source = safeHttps(url);
  if (!source) return null;
  return <a className="cite-link" href={source} target="_blank" rel="noopener noreferrer">{t.openSource} ↗</a>;
}

/** A passage quoted from the museum record, kept in its original language. */
function EvidenceCard({ c }: { c: Evidence }) {
  const license = safeHttps(c.license_url);
  return (
    <li className="cite">
      <p className="cite-title">{c.source_title}</p>
      <blockquote lang={c.source_language}>{c.excerpt}</blockquote>
      <dl className="cite-meta">
        <div><dt>{t.locator}</dt><dd><code>{c.source_locator}</code></dd></div>
        <div><dt>{t.sourceLanguage}</dt><dd>{t.langName[c.source_language] ?? c.source_language}</dd></div>
        <div>
          <dt>{t.license}</dt>
          <dd>
            {license ? <a href={license} target="_blank" rel="noopener noreferrer">{c.license}</a> : c.license}
            {' · '}{c.attribution}
          </dd>
        </div>
        {c.normalization !== 'none' && <div><dt>{t.normalization}</dt><dd>{c.normalization}</dd></div>}
      </dl>
      <SourceLink url={c.source_url} />
    </li>
  );
}

/**
 * A catalog value (title, artist or date). It is labelled as catalog metadata and shown as
 * field and value, never styled as a quoted museum passage (agent contract 6.1).
 */
function CatalogCard({ c }: { c: CatalogFact }) {
  return (
    <li className="cite cite-catalog">
      <p className="cite-kind">{t.catalogBadge}</p>
      <p className="cite-title">{c.source_title}</p>
      <p className="cite-fact"><span className="muted">{t.catalogField[c.field]}:</span> {c.value}</p>
      <dl className="cite-meta">
        <div><dt>{t.locator}</dt><dd><code>{c.source_locator}</code></dd></div>
        <div><dt>{t.attribution}</dt><dd>{c.attribution}</dd></div>
        <div><dt>{t.normalization}</dt><dd>{c.normalization}</dd></div>
      </dl>
      <SourceLink url={c.source_url} />
    </li>
  );
}

export function Citation({ c }: { c: CitationRecord }) {
  return c.kind === 'catalog' ? <CatalogCard c={c} /> : <EvidenceCard c={c} />;
}
