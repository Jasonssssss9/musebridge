import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { createVoiceController, type VoiceController, type VoiceOptions, type VoiceSnapshot } from './controller';

/**
 * React binding for the Vincent voice controller. One controller lives for one
 * conversation ID; changing the ID or leaving the page disposes it, which stops
 * the microphone and speech and drops late results.
 *
 *   const { voice, snapshot } = useVincentVoice(conversationId, { submit, welcomeUrl });
 *   <button onClick={() => voice.startRecording()}>Record</button>
 */
export function useVincentVoice(conversationId: string, options: VoiceOptions): { voice: VoiceController; snapshot: VoiceSnapshot } {
  // Keep the latest submit without recreating the controller on every render.
  const latest = useRef(options);
  latest.current = options;
  const voice = useMemo(
    () => createVoiceController(conversationId, {
      ...options,
      submit: (question) => latest.current.submit(question),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [conversationId],
  );
  useEffect(() => () => voice.dispose(), [voice]);
  const snapshot = useSyncExternalStore(voice.subscribe, voice.getSnapshot, voice.getSnapshot);
  return { voice, snapshot };
}
