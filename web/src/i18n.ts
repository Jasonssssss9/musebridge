import type { AnswerLanguage } from './api/types';

// One selector drives both the interface language and the requested answer
// language (contract 1.1). Source excerpts always stay in their original language.
const en = {
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
  language: 'Language',
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
  sessionEnded: 'Your earlier conversation is no longer available, so a new one has started. Your question is back in the box below — send it again when you are ready.',
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

const zh: typeof en = {
  appTagline: '问问眼前这件作品',
  homeIntro: '扫描作品旁的二维码，或从下面两件演示作品中选择。',
  loadingArtwork: '正在加载作品…',
  artworkNotFoundTitle: '这个作品链接无法打开',
  artworkNotFoundBody: '二维码可能已过期。请重新扫描，或从演示列表中选择作品。',
  artworkErrorTitle: '作品加载失败',
  artworkErrorBody: '请检查网络后重试。',
  backToList: '演示作品',
  tryAgain: '重试',
  accession: '馆藏编号',
  museumRecord: '馆方记录',
  noImage: '本演示未包含作品图片',
  language: '语言',
  chatHeading: '就这件作品提问',
  chatEmpty: '回答依据馆方记录，并附上出处。',
  suggestions: '可以这样问',
  placeholder: '输入你的问题…',
  send: '发送',
  tooLong: '问题最多 500 个字符。',
  you: '你的提问',
  fixtureLabel: '演示回答 — 固定测试答案',
  statusInsufficient: '资料不足',
  statusUnsupported: '超出本演示的示例范围',
  sources: '出处',
  openSource: '打开原始出处',
  sourceLanguage: '原文语言',
  locator: '位置',
  license: '许可',
  normalization: '说明',
  langName: { en: '英文', 'zh-CN': '简体中文' },
  sending: '正在发送…',
  checking: '正在确认问题是否已送达…',
  stalled: '仍在等待回答。',
  checkAgain: '再次检查',
  retry: '重试',
  edit: '修改问题',
  reference: '参考编号',
  chatStarting: '正在开始对话…',
  chatUnavailable: '导览暂时无法使用。上方的作品信息仍然有效。',
  sessionEnded: '之前的对话已失效，已为你开始新的对话。你的问题已放回下方输入框，可以重新发送。',
  editingNote: '修改后将作为新问题发送。',
  qrTitle: '演示用二维码',
  qrIntro: '每个二维码对应一件作品的页面。可以打印本页，或在另一块屏幕上显示，再用手机扫描。',
  qrBase: '手机访问的地址',
  qrLocalWarning: '手机无法打开“localhost”。请通过电脑的局域网地址打开本页（例如 http://192.168.1.20:8080/qr），或在上方填入该地址。',
  qrPrint: '打印二维码',
  qrScan: '扫码就这件作品提问',
  err: {
    unavailable: '导览暂时无法使用。你的问题已保留。',
    timeout: '导览响应超时。你的问题已保留。',
    service: '导览服务出现故障。这与馆方资料无关，请告知演示团队。',
    unconfirmed: '无法确认你的问题是否已送达。',
    generic: '出了点问题。你的问题已保留。',
  },
};

export type Strings = typeof en;
export const STRINGS: Record<AnswerLanguage, Strings> = { en, 'zh-CN': zh };

/** Visitor-facing text is chosen from the error code, never from the server's English message. */
export function errorText(t: Strings, code: string): string {
  if (code === 'unconfirmed') return t.err.unconfirmed;
  if (code.endsWith('_timeout')) return t.err.timeout;
  if (code.endsWith('_unavailable') || code === 'service_not_ready') return t.err.unavailable;
  if (code.endsWith('_invalid_response') || code === 'bad_response' || code === 'origin_not_allowed' || code === 'invalid_request') return t.err.service;
  return t.err.generic;
}

const LANG_KEY = 'musebridge.lang';

export function initialLanguage(): AnswerLanguage {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === 'en' || saved === 'zh-CN') return saved;
  } catch { /* ignore */ }
  return (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
}
export function rememberLanguage(l: AnswerLanguage): void {
  try { localStorage.setItem(LANG_KEY, l); } catch { /* ignore */ }
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
