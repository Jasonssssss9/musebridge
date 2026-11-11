import { useState } from 'react';
import { UI_TEXT as t } from '../i18n';
import { qrMatrix, qrPath } from '../qr';
import { DEMO } from './Home';

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
  const [base, setBase] = useState(window.location.origin);
  const origin = base.trim().replace(/\/+$/, '');
  const local = /^https?:\/\/(localhost|127\.|\[::1\])/i.test(origin);
  return (
    <main className="page qrpage">
      <div className="no-print">
        <h1>{t.qrTitle}</h1>
        <p className="muted small">{t.qrIntro}</p>
        <label className="field">
          <span className="small">{t.qrBase}</span>
          <input value={base} onChange={(e) => setBase(e.target.value)} inputMode="url" autoCapitalize="none" spellCheck={false} />
        </label>
        {local && <p className="notice small" role="alert">{t.qrLocalWarning}</p>}
        <div className="actions"><button className="btn" onClick={() => window.print()}>{t.qrPrint}</button></div>
      </div>
      <ul className="qrcards">
        {DEMO.map((w) => {
          const url = `${origin}/a/${w.slug}`;
          return (
            <li key={w.slug} className="qrcard">
              <Qr text={url} />
              <p className="workcard-title">{w.title}</p>
              <p className="muted">{w.by}</p>
              <p className="small">{t.qrScan}</p>
              <p className="small muted"><code>{url}</code></p>
            </li>
          );
        })}
      </ul>
    </main>
  );
}
