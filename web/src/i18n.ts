import type { AnswerLanguage } from './api/types';

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
  statusInsufficient: 'Not enough evidence',
  statusUnsupported: 'Outside this demo’s examples',
  sources: 'Sources',
  openSource: 'Open original source',
  sourceLanguage: 'Source language',
  locator: 'Where',
  license: 'License',
  normalization: 'Note',
  langName: { en: 'English', 'zh-CN': 'Chinese (Simplified)' } as Record<string, string>,
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
  if (code.endsWith('_invalid_response') || code === 'bad_response' || code === 'origin_not_allowed' || code === 'invalid_request') return err.service;
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

/** Supported example questions per artwork (contract 6.1), shown as tap-to-ask chips. */
export function suggestions(artworkId: string, lang: AnswerLanguage): string[] {
  const zhQ = lang === 'zh-CN';
  const list = [
    zhQ ? '这件作品用了什么材料？' : 'What material is this artwork made of?',
    zhQ ? '馆方如何描述这件作品的笔触？' : "How does the museum describe this artwork's brushwork?",
  ];
  if (artworkId === 'aic-28560') {
    list.push(zhQ ? '馆方认为，画面里的哪些元素可能让人感到紧张？' : 'Which elements might suggest nervous energy, according to the museum?');
  }
  return list;
}
