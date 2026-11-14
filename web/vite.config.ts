import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The browser always calls relative /api URLs (contract 1.1). In live mode the
// dev server forwards them to the Go API and keeps the browser's Origin header,
// which the Go API checks against PUBLIC_BASE_URL. /healthz and /readyz are
// deliberately not proxied.
// `vite --mode mock` (npm run dev) switches the app to the in-browser mock API.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_API_MODE': JSON.stringify(mode === 'mock' ? 'mock' : 'live'),
    // Live builds speak the fixture contract (local-demo-v0.1) unless PUBLIC_CONTRACT=agent is set,
    // which switches them to agent-demo-v0.3. The mock always speaks the agent contract.
    'import.meta.env.VITE_PUBLIC_CONTRACT': JSON.stringify(process.env.PUBLIC_CONTRACT === 'agent' ? 'agent' : 'fixture'),
  },
  server: {
    host: true, // listen on the LAN so a phone can reach it
    port: 8080,
    strictPort: true,
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
}));
