// Sample data for the mock API (mock mode only; the real app never reads answers from here).
// Artworks and passages are copied from knowledge/data/aic-seed-v0.1.json. Catalog facts follow
// docs/agent-api-contract.md section 6.1. The reply text is hand-written sample wording in the
// four supported languages; it stands in for model output and is not a quality reference.
import type { Language } from './types';

export const DATASET_VERSION = 'aic-seed-v0.1';

export const ARTWORKS = [
  {
    artwork_id: 'aic-28560', slug: 'aic-28560', title: 'The Bedroom', artist: 'Vincent van Gogh',
    date_display: '1889', museum: 'Art Institute of Chicago', museum_record_id: 28560,
    accession_number: '1926.417', source_url: 'https://api.artic.edu/api/v1/artworks/28560',
    image_url: null, dataset_version: DATASET_VERSION,
  },
  {
    artwork_id: 'aic-11723', slug: 'aic-11723', title: 'Woman at Her Toilette', artist: 'Berthe Morisot',
    date_display: '1875–80', museum: 'Art Institute of Chicago', museum_record_id: 11723,
    accession_number: '1924.127', source_url: 'https://api.artic.edu/api/v1/artworks/11723',
    image_url: null, dataset_version: DATASET_VERSION,
  },
];
export type MockArtwork = (typeof ARTWORKS)[number];

const src = (a: MockArtwork) => ({
  source_id: `aic-api-${a.museum_record_id}`,
  source_title: `${a.title} — Art Institute of Chicago API record`,
  source_url: a.source_url,
});
const CC0 = { license: 'CC0-1.0', license_url: 'https://creativecommons.org/publicdomain/zero/1.0/' };
const CCBY = { license: 'CC-BY-4.0', license_url: 'https://creativecommons.org/licenses/by/4.0/' };
const DESCRIPTIONS: Record<string, { excerpt: string; normalization: string }> = {
  'aic-28560': {
    excerpt: 'With its bold colors, thick and broken brushwork, and sharply receding lines, the picture might suggest a nervous energy.',
    normalization: 'HTML removed; whitespace normalized.',
  },
  'aic-11723': {
    excerpt: 'In the background, Morisot’s soft, feathery brushstrokes suggest a floral-patterned bedspread and wallpaper.',
    normalization: "HTML removed; whitespace normalized; the source's split floral ligature normalized to floral.",
  },
};

/** Wire-format citation records by ID: two retrieval passages and three catalog facts per artwork. */
export const CITATIONS: Record<string, Record<string, unknown>> = {};
for (const a of ARTWORKS) {
  const base = { artwork_id: a.artwork_id, ...src(a), attribution: 'Art Institute of Chicago' };
  CITATIONS[`${a.artwork_id}-medium`] = {
    chunk_id: `${a.artwork_id}-medium`, ...base, source_language: 'en', source_locator: 'data.medium_display',
    excerpt: 'Oil on canvas', ...CC0, normalization: 'none',
  };
  CITATIONS[`${a.artwork_id}-description-01`] = {
    chunk_id: `${a.artwork_id}-description-01`, ...base, source_language: 'en',
    source_locator: 'data.description, paragraph 1', ...DESCRIPTIONS[a.artwork_id], ...CCBY,
  };
  for (const field of ['title', 'artist', 'date_display'] as const) {
    CITATIONS[`${a.artwork_id}-catalog-${field}`] = {
      kind: 'catalog', fact_id: `${a.artwork_id}-catalog-${field}`, ...base, dataset_version: DATASET_VERSION,
      field, value: a[field], source_locator: `artworks[artwork_id=${a.artwork_id}].${field}`,
      normalization: 'Released catalog metadata; not a verbatim museum excerpt.',
    };
  }
}

export type PartKind = 'supported' | 'insufficient' | 'clarification' | 'out_of_scope' | 'conversation';
type Text = Record<Language, string>;

/** Every reply part the mock can produce. {title}, {artist} and {date} are filled from the catalog. */
export const PARTS: Record<string, { kind: PartKind; cite?: (artworkId: string) => string; text: Text }> = {
  catalog_title: {
    kind: 'supported', cite: (a) => `${a}-catalog-title`,
    text: { en: 'The catalog title is {title}.', es: 'El título de catálogo es {title}.', 'zh-CN': '馆藏目录中的标题是 {title}。', ko: '소장품 목록의 제목은 {title}입니다.' },
  },
  catalog_artist: {
    kind: 'supported', cite: (a) => `${a}-catalog-artist`,
    text: { en: 'The catalog lists {artist} as the artist.', es: 'El catálogo indica a {artist} como artista.', 'zh-CN': '目录记载的作者是 {artist}。', ko: '목록에는 작가가 {artist}(으)로 기재되어 있습니다.' },
  },
  catalog_date: {
    kind: 'supported', cite: (a) => `${a}-catalog-date_display`,
    text: { en: 'The catalog gives the date as {date}.', es: 'El catálogo da como fecha {date}.', 'zh-CN': '目录给出的年代是 {date}。', ko: '목록에 기재된 제작 연도는 {date}입니다.' },
  },
  material: {
    kind: 'supported', cite: (a) => `${a}-medium`,
    text: { en: 'The museum lists the medium as oil on canvas.', es: 'El museo indica que la técnica es óleo sobre lienzo.', 'zh-CN': '馆方记录的材质是布面油画。', ko: '미술관 기록에 따르면 재료는 캔버스에 유채입니다.' },
  },
  brush_bedroom: {
    kind: 'supported', cite: (a) => `${a}-description-01`,
    text: { en: 'The museum describes thick, broken brushwork.', es: 'El museo describe una pincelada gruesa y entrecortada.', 'zh-CN': '馆方描述了厚重、断续的笔触。', ko: '미술관은 두껍고 끊어진 붓질을 언급합니다.' },
  },
  brush_toilette: {
    kind: 'supported', cite: (a) => `${a}-description-01`,
    text: {
      en: 'The museum describes soft, feathery brushstrokes that suggest a floral-patterned bedspread and wallpaper in the background.',
      es: 'El museo describe pinceladas suaves y plumosas que sugieren una colcha y un papel pintado con motivos florales al fondo.',
      'zh-CN': '馆方描述了柔和、羽毛般的笔触，暗示背景中带花卉图案的床罩和墙纸。',
      ko: '미술관은 배경의 꽃무늬 침대보와 벽지를 암시하는 부드럽고 깃털 같은 붓질을 언급합니다.',
    },
  },
  energy: {
    kind: 'supported', cite: (a) => `${a}-description-01`,
    text: {
      en: 'According to the museum, the bold colors, thick and broken brushwork, and sharply receding lines might suggest a nervous energy.',
      es: 'Según el museo, los colores intensos, la pincelada gruesa y entrecortada y las líneas que retroceden bruscamente podrían sugerir una energía nerviosa.',
      'zh-CN': '馆方认为，鲜明的色彩、厚重而断续的笔触，以及明显向纵深延伸的线条，可能让画面显出紧张的活力。',
      ko: '미술관에 따르면 강렬한 색채, 두껍고 끊어진 붓질, 급격히 멀어지는 선들이 불안한 에너지를 암시할 수 있습니다.',
    },
  },
  gap_generic: {
    kind: 'insufficient',
    text: {
      en: "I can't confirm that from the museum record I have for this work. I can tell you about its materials, brushwork, title, artist or date.",
      es: 'No puedo confirmarlo con el registro del museo que tengo de esta obra. Puedo hablarte de sus materiales, la pincelada, el título, el artista o la fecha.',
      'zh-CN': '我无法根据现有的馆方记录确认这一点。我可以介绍这件作品的材质、笔触、标题、作者或年代。',
      ko: '제가 가진 미술관 기록으로는 그 내용을 확인할 수 없습니다. 이 작품의 재료, 붓질, 제목, 작가, 제작 연도는 알려 드릴 수 있습니다.',
    },
  },
  gap_hours: {
    kind: 'insufficient',
    text: { en: "The record I have doesn't say how long this version took to paint.", es: 'El registro que tengo no indica cuánto tiempo se tardó en pintar esta versión.', 'zh-CN': '我手头的记录没有说明这一版画了多久。', ko: '제가 가진 기록에는 이 버전을 그리는 데 걸린 시간이 나와 있지 않습니다.' },
  },
  gap_name: {
    kind: 'insufficient',
    text: { en: "The record I have doesn't establish who the woman is.", es: 'El registro que tengo no establece quién es la mujer.', 'zh-CN': '我手头的记录无法确定画中女子是谁。', ko: '제가 가진 기록으로는 그림 속 여성이 누구인지 알 수 없습니다.' },
  },
  clarify_object: {
    kind: 'clarification',
    text: {
      en: 'Which object do you mean? Tell me where it is in the picture or what it looks like.',
      es: '¿A qué objeto te refieres? Dime dónde está en el cuadro o cómo es.',
      'zh-CN': '你指的是哪个物件？请告诉我它在画面的什么位置，或者它长什么样。',
      ko: '어떤 물건을 말씀하시는 건가요? 그림의 어느 위치에 있는지, 어떻게 생겼는지 알려 주세요.',
    },
  },
  clarify_translation: {
    kind: 'clarification',
    text: {
      en: "Which answer should I translate? There isn't an earlier answer yet.",
      es: '¿Qué respuesta quieres que traduzca? Todavía no hay ninguna respuesta anterior.',
      'zh-CN': '你想让我翻译哪一段？目前还没有之前的回答。',
      ko: '어떤 답변을 번역할까요? 아직 이전 답변이 없습니다.',
    },
  },
  out_scope: {
    kind: 'out_of_scope',
    text: {
      en: "I can only talk about this artwork and its museum record, so I can't help with that here.",
      es: 'Solo puedo hablar de esta obra y de su registro en el museo, así que no puedo ayudarte con eso aquí.',
      'zh-CN': '我只能讨论这件作品及其馆方记录，这个问题在这里帮不上忙。',
      ko: '저는 이 작품과 미술관 기록에 대해서만 이야기할 수 있어서, 그 부분은 도와드리기 어렵습니다.',
    },
  },
  chat_hello: {
    kind: 'conversation',
    text: { en: 'Hello! Ask me anything about this work.', es: '¡Hola! Pregúntame lo que quieras sobre esta obra.', 'zh-CN': '你好！关于这件作品，想问什么都可以。', ko: '안녕하세요! 이 작품에 대해 무엇이든 물어보세요.' },
  },
  chat_thanks: {
    kind: 'conversation',
    text: { en: "You're welcome. Is there anything else you'd like to look at?", es: 'De nada. ¿Hay algo más que quieras ver?', 'zh-CN': '不客气。还有什么想了解的吗？', ko: '천만에요. 더 알고 싶은 것이 있나요?' },
  },
  chat_reaction: {
    kind: 'conversation',
    text: { en: 'What part of it caught your attention?', es: '¿Qué parte te llamó la atención?', 'zh-CN': '是哪个部分吸引了你？', ko: '어떤 부분이 눈길을 끌었나요?' },
  },
  chat_generic: {
    kind: 'conversation',
    text: {
      en: "I'm here to talk about this work. You could ask what it is made of, who made it, or how the museum describes it.",
      es: 'Estoy aquí para hablar de esta obra. Puedes preguntar de qué está hecha, quién la hizo o cómo la describe el museo.',
      'zh-CN': '我可以陪你聊这件作品。你可以问它用了什么材料、作者是谁，或者馆方如何描述它。',
      ko: '이 작품에 대해 이야기해 드릴게요. 무엇으로 만들어졌는지, 누가 만들었는지, 미술관이 어떻게 설명하는지 물어보세요.',
    },
  },
  language_fixed: {
    kind: 'conversation',
    text: { en: "Okay, I'll answer in English from now on.", es: 'De acuerdo, a partir de ahora responderé en español.', 'zh-CN': '好的，接下来我会用中文回答。', ko: '알겠습니다. 이제부터 한국어로 답하겠습니다.' },
  },
  language_auto: {
    kind: 'conversation',
    text: { en: "Okay, I'll follow the language you write in.", es: 'De acuerdo, responderé en el idioma en que escribas.', 'zh-CN': '好的，我会跟随你使用的语言回答。', ko: '알겠습니다. 쓰시는 언어에 맞춰 답하겠습니다.' },
  },
};

/** Keyword cues per intent. A cue can be limited to one artwork; elsewhere it becomes an evidence gap. */
export const INTENTS: { part: string; only?: string; elsewhere?: string; cue: RegExp }[] = [
  { part: 'gap_hours', cue: /how many hours|how long|cu[aá]nt[ao]s? horas|cu[aá]nto tiempo|多少小时|多久|몇 시간|얼마나 걸/ },
  { part: 'gap_name', only: 'aic-11723', elsewhere: 'gap_generic', cue: /real name|who is (she|the woman)|woman's name|nombre real|qui[eé]n es (ella|la mujer)|真实姓名|她是谁|女子是谁|실제 이름|여성은 누구|그녀는 누구/ },
  { part: 'clarify_object', cue: /(that|this) (little |small )?(object|thing)|ese (peque[nñ]o )?objeto|esa cosa|那个东西|那个物件|这个东西|那个小东西|저 물건|그 물건|저것은|그것은 뭐/ },
  { part: 'out_scope', cue: /weather|restroom|\btoilets?\b|\btickets?\b|parking|mona lisa|compare|other (painting|artwork)s?|qué tiempo hace|\bba[nñ]os?\b|\bentradas?\b|comparar|天气|洗手间|门票|比较|날씨|화장실|입장권|비교/ },
  { part: 'material', cue: /materials?|\bmedium\b|made of|canvas|\boil\b|hech[ao] de|[oó]leo|lienzo|材料|材质|媒介|油画|画布|재료|재질|유채|캔버스|무엇으로/ },
  { part: 'brush_bedroom', only: 'aic-28560', elsewhere: 'brush_toilette', cue: /brush|pincelada|trazo|笔触|붓질|붓터치|붓 자국/ },
  { part: 'energy', only: 'aic-28560', elsewhere: 'gap_generic', cue: /nervous|energy|\btense\b|nervios[ao]|energ[ií]a|紧张|불안|에너지/ },
  { part: 'brush_toilette', only: 'aic-11723', elsewhere: 'gap_generic', cue: /background|wallpaper|bedspread|\bfondo\b|papel pintado|colcha|背景|墙纸|床罩|배경|벽지|침대보/ },
  { part: 'catalog_artist', cue: /who (made|painted|created)|\bartist\b|\bpainter\b|qui[eé]n (la |lo )?(hizo|pint[oó]|cre[oó])|artista|pintor|作者|谁画|画家|누가|작가|화가/ },
  { part: 'catalog_date', cue: /\bwhen\b|what year|\bdate\b|cu[aá]ndo|qu[eé] a[nñ]o|\bfecha\b|什么时候|哪一年|年代|何时|언제|몇 년|연도/ },
  { part: 'catalog_title', cue: /\btitle\b|\bcalled\b|t[ií]tulo|se llama|标题|叫什么|제목/ },
  { part: 'chat_hello', cue: /^(hi|hello|hey)\b|\bhola\b|你好|您好|안녕/ },
  { part: 'chat_thanks', cue: /thank|gracias|谢谢|감사|고마워/ },
  { part: 'chat_reaction', cue: /beautiful|love (it|this)|like (it|this)|amazing|\bwow\b|hermos[ao]|bonit[ao]|me gusta|me encanta|好美|漂亮|喜欢|真美|아름답|예쁘|좋아/ },
];

export const LANGUAGE_NAMES: { language: Language; cue: RegExp }[] = [
  { language: 'en', cue: /english|ingl[eé]s|英文|英语|영어/ },
  { language: 'es', cue: /spanish|espa[nñ]ol|西班牙语|西语|스페인어/ },
  { language: 'zh-CN', cue: /chinese|chino|中文|汉语|중국어/ },
  { language: 'ko', cue: /korean|coreano|韩语|韩文|한국어/ },
];
export const TRANSLATE_CUE = /translate|traduc|翻译|번역/;
export const SETTING_CUE = /answer in|respond in|reply in|speak|from now on|responde en|contesta en|h[aá]blame en|a partir de ahora|回答|请说|로 (대답|답|말)/;
export const SPANISH_CUE = /[¿¡ñáéíóú]|\b(hola|gracias|qui[eé]n|c[oó]mo|cu[aá]ndo|est[aá]|esta obra|el cuadro|pintura|hech[ao]|por favor|de qu[eé])\b/;
