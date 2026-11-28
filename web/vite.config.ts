import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The browser always calls relative /api URLs (contract 1.1). In live mode the
// dev server forwards them to the Go API and keeps the browser's Origin header,
// which the Go API checks against PUBLIC_BASE_URL. /healthz and /readyz are
// deliberately not proxied.
// `vite --mode mock` (npm run dev) switches the app to the in-browser mock API.
export default defineConfig(({ mode }) => {
  const publicContract = process.env.PUBLIC_CONTRACT ?? 'fixture';
  if (publicContract !== 'fixture' && publicContract !== 'agent') {
    throw new Error('PUBLIC_CONTRACT must be fixture or agent.');
  }
  // A phone reaches the dev server through an HTTPS tunnel (docs/vincent-voice-setup.md).
  // Vite rejects unknown Host headers, so allow the PUBLIC_BASE_URL host explicitly.
  let publicHost: string | undefined;
  let publicOrigin = '';
  try {
    if (process.env.PUBLIC_BASE_URL) {
      const url = new URL(process.env.PUBLIC_BASE_URL);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
        || url.pathname !== '/' || url.search || url.hash) throw new Error('invalid origin');
      publicHost = url.hostname;
      publicOrigin = url.origin;
    }
  } catch {
    throw new Error('PUBLIC_BASE_URL must be an HTTP(S) origin without a path, query, fragment or credentials.');
  }
  return {
    plugins: [react()],
    define: {
      'import.meta.env.VITE_API_MODE': JSON.stringify(mode === 'mock' ? 'mock' : 'live'),
      // Live builds speak the fixture contract (local-demo-v0.1) unless PUBLIC_CONTRACT=agent is set,
      // which switches them to agent-demo-v0.3. The mock always speaks the agent contract.
      'import.meta.env.VITE_PUBLIC_CONTRACT': JSON.stringify(publicContract),
      // Public navigation only; API requests continue to use the page's own origin.
      'import.meta.env.VITE_PUBLIC_ORIGIN': JSON.stringify(publicOrigin),
    },
    server: {
      host: true, // listen on the LAN so a phone can reach it
      port: 8080,
      strictPort: true,
      allowedHosts: publicHost ? [publicHost] : undefined,
      proxy: {
        '/api': {
          target: process.env.API_TARGET ?? 'http://127.0.0.1:8000',
          changeOrigin: false,
          // Agent contract 7.4: the development proxy allows 75 s, above the browser's 70 s wait.
          timeout: 75000,
          proxyTimeout: 75000,
        },
      },
    },
  };
});
