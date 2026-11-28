import { lazy, Suspense } from 'react';
import { INITIAL_VOICE_STATE, VincentView, type VincentActions } from '../vincent/View';

// Production builds omit the preview module, including its scripted replies.
const Preview = import.meta.env.DEV ? lazy(() => import('../vincent/Preview')) : null;
const unavailableActions: VincentActions = {
  startRecording() {}, stopRecording() {}, playTurn() {}, stopPlayback() {}, retryTranscription() {}, submit() {},
};

/** The presentation is ready to bind to A's controller once v0.4 is connected. */
export function VincentPage() {
  if (Preview && new URLSearchParams(window.location.search).get('preview') === '1') {
    return <Suspense fallback={<main className="page"><p role="status">Loading interface preview…</p></main>}><Preview /></Suspense>;
  }
  return <VincentView state={INITIAL_VOICE_STATE} reply={null} actions={unavailableActions} enabled={false} />;
}
