import { useEffect, useRef, useState } from 'react';
import { INITIAL_VOICE_STATE, VincentView, type VincentReply, type VincentVoiceState } from './View';
import { SAMPLE_REPLY } from './previewData';

const scenarios = [
  ['ready', 'Ready to record'], ['welcome', 'Opening greeting'], ['recording', 'Recording'],
  ['transcribing', 'Transcribing'], ['generating', 'Preparing reply'], ['preparing_audio', 'Preparing audio'],
  ['awaiting_play', 'Waiting for Play'], ['playing', 'Playing reply'], ['ended', 'Playback ended'],
  ['microphone_denied', 'Microphone denied'], ['no_speech', 'No speech recognized'],
  ['transcription_error', 'Transcription unavailable'], ['speech_error', 'Audio unavailable'], ['long_reply', 'Long reply'],
] as const;
type Scenario = typeof scenarios[number][0];

/** Only local visual assets load; capture, speech, conversation storage and model calls stay simulated. */
export default function VincentPreview() {
  const [state, setState] = useState<VincentVoiceState>(INITIAL_VOICE_STATE);
  const [reply, setReply] = useState<VincentReply | null>(null);
  const [scenario, setScenario] = useState<Scenario>('ready');
  const [question, setQuestion] = useState(SAMPLE_REPLY.question);
  const [entry, setEntry] = useState(0);
  const sequence = useRef(1);

  useEffect(() => {
    if (state.phase !== 'recording') return;
    const timer = window.setInterval(() => setState((current) => ({ ...current, recordingSeconds: Math.min(20, current.recordingSeconds + 1) })), 1000);
    return () => window.clearInterval(timer);
  }, [state.phase]);
  useEffect(() => {
    if (state.phase === 'recording' && state.recordingSeconds >= 20) {
      setState((current) => ({ ...current, phase: 'transcribing' }));
      setScenario('transcribing');
    }
  }, [state.phase, state.recordingSeconds]);

  function sampleReply(forQuestion = SAMPLE_REPLY.question): VincentReply {
    return { ...SAMPLE_REPLY, id: `preview-turn-${sequence.current++}`, question: forQuestion,
      ...(forQuestion === SAMPLE_REPLY.question ? {} : {
        text: 'This is a sample reply for checking the layout. In the connected experience, Vincent will respond to your question.', citations: [],
      }),
    };
  }

  function choose(value: Scenario) {
    if (value === 'welcome') setEntry((current) => current + 1);
    setScenario(value);
    setQuestion(SAMPLE_REPLY.question);
    const answered = ['preparing_audio', 'awaiting_play', 'playing', 'ended', 'speech_error', 'long_reply'].includes(value);
    const nextReply = answered ? sampleReply() : null;
    if (nextReply && value === 'long_reply') nextReply.text += '\n\n' + 'This extra sample paragraph checks that a longer reply stays readable on a small screen. The text should wrap naturally, and the sources and playback controls should remain reachable. '.repeat(6);
    setReply(nextReply);
    const next: VincentVoiceState = { ...INITIAL_VOICE_STATE,
      transcript: answered || value === 'generating' ? SAMPLE_REPLY.question : null, turnId: nextReply?.id ?? null,
    };
    switch (value) {
      case 'welcome': next.phase = 'playing'; next.welcome = true; break;
      case 'microphone_denied': next.phase = 'error'; next.error = {
        stage: 'capture', code: 'microphone_denied', retryable: false, message: 'Microphone access was not allowed.',
      }; break;
      case 'no_speech': next.phase = 'error'; next.error = {
        stage: 'transcription', code: 'no_speech', retryable: false, message: 'No clear speech was recognized. Record another question.',
      }; break;
      case 'transcription_error': next.phase = 'error'; next.error = {
        stage: 'transcription', code: 'transcription_unavailable', retryable: true, message: 'Your recording could not be transcribed. Please try again.',
      }; break;
      case 'speech_error': next.phase = 'error'; next.error = {
        stage: 'speech', code: 'speech_unavailable', retryable: true, message: 'Audio is unavailable right now. Try Play answer again.',
      }; break;
      case 'long_reply': case 'ended': next.phase = 'ready'; break;
      default: next.phase = value;
    }
    setState(next);
  }

  function nextStep() {
    if (state.phase === 'transcribing') {
      setState((current) => ({ ...current, phase: 'generating', transcript: question }));
      setScenario('generating');
    } else if (state.phase === 'generating') {
      const answer = sampleReply(question);
      setReply(answer);
      setState((current) => ({ ...current, phase: 'preparing_audio', turnId: answer.id }));
      setScenario('preparing_audio');
    } else if (state.phase === 'preparing_audio') {
      setState((current) => ({ ...current, phase: 'awaiting_play' }));
      setScenario('awaiting_play');
    } else if (state.phase === 'playing') {
      setState((current) => ({ ...current, phase: 'ready', welcome: false }));
      setScenario('ended');
    }
  }

  return <VincentView key={entry} state={state} reply={reply} enabled actions={{
    startRecording() {
      setQuestion(SAMPLE_REPLY.question);
      setState({ ...INITIAL_VOICE_STATE, phase: 'recording' });
      setScenario('recording');
    },
    stopRecording() { setState((current) => ({ ...current, phase: 'transcribing' })); setScenario('transcribing'); },
    playTurn(turnId) {
      setState((current) => ({ ...current, phase: 'playing', turnId, welcome: false, error: null }));
      setScenario('playing');
    },
    stopPlayback() { setState((current) => ({ ...current, phase: 'ready', welcome: false })); setScenario('ended'); },
    retryTranscription() { setState((current) => ({ ...current, phase: 'transcribing', error: null })); setScenario('transcribing'); },
    submit(text) {
      setQuestion(text);
      setState({ ...INITIAL_VOICE_STATE, phase: 'generating', transcript: text });
      setScenario('generating');
    },
  }} previewControls={
    <details className="vincent-preview" open>
      <summary>Interface preview · Simulated conversation</summary>
      <p className="vincent-preview-note">The silent greeting video is real. No microphone, audio or AI services are used. Choose a state, or use Next step to move through processing. Sample replies are not generated answers.</p>
      <div className="vincent-preview-controls">
        <label>Preview state<select value={scenario} onChange={(event) => choose(event.target.value as Scenario)}>
          {scenarios.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <button className="btn btn-quiet" onClick={nextStep}
          disabled={!['transcribing', 'generating', 'preparing_audio', 'playing'].includes(state.phase)}>Next step</button>
        <button className="btn btn-quiet" onClick={() => { setEntry((current) => current + 1); choose('ready'); }}>Reset preview</button>
      </div>
    </details>
  } />;
}
