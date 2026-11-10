import { useState } from 'react';
import { FAULTS, getFault, resetMockServer, setFault, type Fault } from '../api/mock';

/** Development only: shown when the app runs against the in-browser mock API. */
export function MockPanel() {
  const [open, setOpen] = useState(false);
  const [fault, setF] = useState<Fault>(getFault);
  return (
    <aside className="mock">
      <button className="mock-pill" onClick={() => { setF(getFault()); setOpen((o) => !o); }} aria-expanded={open}>
        Mock API{fault !== 'none' ? ' · fault on' : ''}
      </button>
      {open && (
        <div className="mock-body">
          <label>
            Next question gets
            <select
              value={fault}
              onChange={(e) => {
                const f = e.target.value as Fault;
                setFault(f);
                setF(f);
              }}
            >
              {FAULTS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
            </select>
          </label>
          <button
            className="btn btn-quiet"
            onClick={() => {
              resetMockServer();
              window.location.reload();
            }}
          >
            Reset mock server
          </button>
          <p>No backend is involved. Run <code>npm run dev:live</code> to use the Go API.</p>
        </div>
      )}
    </aside>
  );
}
