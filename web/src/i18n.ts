import type { AnswerLanguage, AnswerStatus, Language, LanguagePreference } from './api/types';

// Interface labels are English. The answer language is stored separately.
export const UI_TEXT = {
  appTagline: 'Ask about the artwork in front of you',
  homeIntro: 'Scan the QR code next to an artwork, or pick one of the two demo works.',
  loadingArtwork: 'Loading artwork…',
  artworkNotFoundTitle: 'This artwork link is not available',
  artworkNotFoundBody: 'The code may be out of date. Try scanning it again, or choose a work from the demo list.',
  artworkErrorTitle: 'The artwork could not be loaded',
  artworkErrorBody: 'Check your connection and try again.',
  backToList: 'Demo artworks',
  tryAgain: 'Try again',
  accession: 'Accession no.',
  museumRecord: 'Museum record',
  noImage: 'Image not included in this demo',
  answerLanguage: 'Answer language',
  chatHeading: 'Ask about this work',
  chatEmpty: 'Answers come from the museum’s own records, and each one shows its source.',
  suggestions: 'Try asking',
  placeholder: 'Ask a question…',
  send: 'Send',
  tooLong: 'Questions can be up to 500 characters.',
  you: 'You asked',
  fixtureLabel: 'Demo response — fixed test answer',
  llmLabel: 'AI guide response',
  // Short coverage note per answer status. The answer text is always shown as well.
  statusNote: {
    answered: null,
    conversational: null,
    partially_answered: 'Partly answered',
    clarification_needed: 'Needs a detail from you',
    insufficient: 'Not confirmed by the museum record',
    out_of_scope: 'Outside this guide',
    unsupported_question: 'Outside this demo’s examples',
  } as Record<AnswerStatus, string | null>,
  fixtureInsufficient: 'Not enough evidence',
  answeredIn: 'Answered in',
  catalogBadge: 'Catalog record',
  catalogField: { title: 'Title', artist: 'Artist', date_display: 'Date' } as Record<string, string>,
  attribution: 'From',
  languageAuto: 'Auto',
  languageAutoHint: 'Auto follows the language you write in.',
  languageNext: 'Applies from your next message.',
  waitingLong: 'Still working. Answers can take up to a minute.',
  conversationChanged: 'This conversation was updated somewhere else, so your message was not sent. Check the latest replies, then send it again if you still want to.',
  sources: 'Sources',
  openSource: 'Open original source',
  sourceLanguage: 'Source language',
  locator: 'Where',
  license: 'License',
  normalization: 'Note',
  langName: { en: 'English', es: 'Spanish', 'zh-CN': 'Chinese (Simplified)', ko: 'Korean' } as Record<string, string>,
  sending: 'Sending…',
  checking: 'Checking whether your question went through…',
  stalled: 'Still waiting for an answer.',
  checkAgain: 'Check again',
  retry: 'Try again',
  edit: 'Edit question',
  reference: 'Reference',
  chatStarting: 'Starting the conversation…',
  chatUnavailable: 'The guide is not available right now. The artwork details above are still correct.',
  sessionEnded: 'Your earlier conversation is no longer available.',
  editingNote: 'Editing sends this as a new question.',
  qrTitle: 'QR cards for the demo',
  qrIntro: 'Each code opens one artwork page. Print this page or show it on a second screen, then scan with the phone.',
  qrBase: 'Address the phone will use',
  qrLocalWarning: 'A phone cannot open "localhost". Open this page through the computer’s network address (for example http://192.168.1.20:8080/qr), or type that address above.',
  qrPrint: 'Print cards',
  qrScan: 'Scan to ask about this work',
  err: {
    unavailable: 'The guide is temporarily unavailable. Your question is saved.',
    timeout: 'The guide took too long to respond. Your question is saved.',
    service: 'The guide service has a problem. This is not about what the museum knows — please tell the demo team.',
    unconfirmed: 'We could not confirm your question was received.',
    generic: 'Something went wrong. Your question is saved.',
  },
};

/** Visitor-facing text is chosen from the error code, never from the server's English message. */
export function errorText(code: string): string {
  const { err } = UI_TEXT;
  if (code === 'unconfirmed') return err.unconfirmed;
  if (code.endsWith('_timeout')) return err.timeout;
  if (code.endsWith('_unavailable') || code === 'service_not_ready') return err.unavailable;
  if (
    code.endsWith('_invalid_response') || code === 'bad_response' || code === 'origin_not_allowed' || code === 'invalid_request' ||
    code === 'agent_contract_mismatch' || code === 'agent_context_limit'
  ) return err.service;
  return err.generic;
}

const ANSWER_LANGUAGE_KEY = 'musebridge.lang';

export function initialAnswerLanguage(): AnswerLanguage {
  try {
    const saved = localStorage.getItem(ANSWER_LANGUAGE_KEY);
    if (saved === 'en' || saved === 'zh-CN') return saved;
  } catch { /* ignore */ }
  return 'en';
}
export function rememberAnswerLanguage(language: AnswerLanguage): void {
  try { localStorage.setItem(ANSWER_LANGUAGE_KEY, language); } catch { /* ignore */ }
}

/** How each language names itself, for the selector. */
export const LANGUAGE_LABEL: Record<Language, string> = { en: 'English', es: 'Español', 'zh-CN': '简体中文', ko: '한국어' };

/** The label for a selector value. */
export function preferenceLabel(p: LanguagePreference): string {
  return p === 'auto' ? UI_TEXT.languageAuto : LANGUAGE_LABEL[p];
}

const EXAMPLES: Record<Language, { material: string; brushwork: string; artist: string; energy: string }> = {
  en: {
    material: 'What material is this artwork made of?',
    brushwork: "How does the museum describe this artwork's brushwork?",
    artist: 'Who made it, and when?',
    energy: 'Which elements might suggest nervous energy, according to the museum?',
  },
  es: {
    material: '¿De qué material está hecha esta obra?',
    brushwork: '¿Cómo describe el museo la pincelada de esta obra?',
    artist: '¿Quién la hizo y cuándo?',
    energy: 'Según el museo, ¿qué elementos podrían sugerir una energía nerviosa?',
  },
  'zh-CN': {
    material: '这件作品用了什么材料？',
    brushwork: '馆方如何描述这件作品的笔触？',
    artist: '作者是谁？是什么时候画的？',
    energy: '馆方认为，画面里的哪些元素可能让人感到紧张？',
  },
  ko: {
    material: '이 작품은 무엇으로 만들어졌나요?',
    brushwork: '미술관은 이 작품의 붓질을 어떻게 설명하나요?',
    artist: '누가, 언제 만들었나요?',
    energy: '미술관은 어떤 요소가 불안한 에너지를 암시한다고 보나요?',
  },
};

/**
 * Example questions shown as tap-to-ask chips, in the given language. The fixture contract only
 * recognizes its listed wordings (api-contract 6.1), so `catalog` questions are agent-only.
 */
export function suggestions(artworkId: string, lang: Language, catalog: boolean): string[] {
  const e = EXAMPLES[lang];
  const list = [e.material, e.brushwork];
  if (catalog) list.push(e.artist);
  if (artworkId === 'aic-28560') list.push(e.energy);
  return list;
}
