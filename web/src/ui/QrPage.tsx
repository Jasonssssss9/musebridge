import { useState } from 'react';
import type { Artwork } from '../api/types';
import { UI_TEXT as t } from '../i18n';
import { qrMatrix, qrPath } from '../qr';
import { CatalogList } from './CatalogList';

function Qr({ text }: { text: string }) {
  let matrix: boolean[][];
  try {
    matrix = qrMatrix(text);
  } catch {
    return <p className="error small">URL too long for a QR code.</p>;
  }
  const size = matrix.length + 8; // 4-module quiet zone on each side
  return (
    <svg className="qr" viewBox={`0 0 ${size} ${size}`} role="img" aria-label={text} shapeRendering="crispEdges">
      <rect width={size} height={size} fill="#fff" />
      <path d={qrPath(matrix)} fill="#000" />
    </svg>
  );
}

/**
 * Printable QR cards (plan A1/A5). A QR holds only the browser route for an
 * artwork, built from the address this page was opened at, so open it through
 * the same LAN address the phone will use.
 */
export function QrPage() {
  return <main className="page qrpage">
    <CatalogList>{(works) => <QrCards works={works} />}</CatalogList>
  </main>;
}

function QrCards({ works }: { works: Artwork[] }) {
  const [base, setBase] = useState(window.location.origin);
  let address: URL | null = null;
  try {
    const parsed = new URL(base.trim());
    if (['http:', 'https:'].includes(parsed.protocol) && !parsed.username && !parsed.password
      && parsed.pathname === '/' && !parsed.search && !parsed.hash) address = parsed;
  } catch { /* Incomplete or invalid addresses must not create misleading QR cards. */ }
  const origin = address?.origin ?? null;
  const local = address && (address.hostname === 'localhost' || address.hostname.endsWith('.localhost') || address.hostname === '[::1]'
    || address.hostname === '0.0.0.0' || /^127\./.test(address.hostname));
  return (
    <>
      <div className="no-print">
        <h1>{t.qrTitle}</h1>
        <p className="muted small">{t.qrIntro}</p>
        <label className="field">
          <span className="small">{t.qrBase}</span>
          <input value={base} onChange={(e) => setBase(e.target.value)} inputMode="url" autoCapitalize="none" spellCheck={false} aria-invalid={!origin} aria-describedby={!origin ? 'qr-address-error' : undefined} />
        </label>
        {!origin && <p id="qr-address-error" className="notice small" role="alert">{t.qrInvalidBase}</p>}
        {local && <p className="notice small" role="alert">{t.qrLocalWarning}</p>}
        <div className="actions"><button className="btn" disabled={!origin} onClick={() => window.print()}>{t.qrPrint}</button></div>
      </div>
      {origin && <ul className="qrcards">
        {works.map((w) => {
          const url = `${origin}/a/${w.slug}`;
          return (
            <li key={w.slug} className="qrcard">
              <Qr text={url} />
              <p className="workcard-title">{w.title}</p>
              <p className="muted">{w.artist}, {w.date_display}</p>
              <p className="small">{t.qrScan}</p>
              <p className="small muted"><code>{url}</code></p>
            </li>
          );
        })}
      </ul>}
    </>
  );
}
