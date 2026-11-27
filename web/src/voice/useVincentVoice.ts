import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createVoiceController, type VoiceController, type VoiceOptions, type VoiceSnapshot } from './controller';

/**
 * React binding for the Vincent voice controller. One controller lives for one
 * conversation ID; changing the ID or leaving the page disposes it, which stops
 * the microphone and speech and drops late results.
 *
 *   const { voice, snapshot } = useVincentVoice(conversationId, {
 *     submit, revision: () => conversation.revision, welcomeUrl,
 *   });
 *   <button onClick={() => voice.startRecording()}>Record</button>
 *
 * React StrictMode runs effect cleanup once after the first mount. The effect
 * therefore replaces a disposed controller instead of reusing it, so the first
 * Record tap always reaches a live controller.
 */
export function useVincentVoice(conversationId: string, options: VoiceOptions): { voice: VoiceController; snapshot: VoiceSnapshot } {
  // Keep the latest callbacks without recreating the controller on every render.
  const latest = useRef(options);
  latest.current = options;
  const make = (id: string) => createVoiceController(id, {
    ...latest.current,
    submit: (message) => latest.current.submit(message),
    revision: () => latest.current.revision(),
  });
  const [voice, setVoice] = useState<VoiceController>(() => make(conversationId));
  const current = useRef(voice);
  current.current = voice;

  useEffect(() => {
    let controller = current.current;
    if (controller.isDisposed || controller.conversationId !== conversationId) {
      controller = make(conversationId);
      current.current = controller;
      setVoice(controller);
    }
    return () => controller.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  const snapshot = useSyncExternalStore(voice.subscribe, voice.getSnapshot, voice.getSnapshot);
  return { voice, snapshot };
}
