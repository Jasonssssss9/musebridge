import type { Evidence } from '../api/types';
import type { Strings } from '../i18n';

/** Only stored HTTPS URLs become links; anything else is shown as plain text (contract 2.5). */
export function safeHttps(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

export function Citation({ c, t }: { c: Evidence; t: Strings }) {
  const source = safeHttps(c.source_url);
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
      {source && (
        <a className="cite-link" href={source} target="_blank" rel="noopener noreferrer">{t.openSource} ↗</a>
      )}
    </li>
  );
}
