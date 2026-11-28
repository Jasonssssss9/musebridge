import { lazy, Suspense } from 'react';
import { INITIAL_VOICE_STATE, VincentView, type VincentActions } from '../vincent/View';
import { CONTRACT, IS_MOCK } from '../api/client';
import VincentLive from '../vincent/Live';

// Production builds omit the preview module, including its scripted replies.
const Preview = import.meta.env.DEV ? lazy(() => import('../vincent/Preview')) : null;
const unavailableActions: VincentActions = {
  startRecording() {}, stopRecording() {}, playTurn() {}, stopPlayback() {}, retryTranscription() {}, submit() {},
};

/** Keep the offline preview and ordinary guide independent of the live v0.4 session. */
export function VincentPage() {
  if (Preview && new URLSearchParams(window.location.search).get('preview') === '1') {
    return <Suspense fallback={<main className="page"><p role="status">Loading interface preview…</p></main>}><Preview /></Suspense>;
  }
  return !IS_MOCK && CONTRACT === 'agent' ? <VincentLive />
    : <VincentView state={INITIAL_VOICE_STATE} reply={null} actions={unavailableActions} enabled={false} />;
}
