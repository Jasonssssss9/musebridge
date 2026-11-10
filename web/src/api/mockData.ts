// Copied from knowledge/data/aic-seed-v0.1.json and docs/api-contract.md section 6.
// Mock mode only: the real app never reads evidence or answers from here.
import type { Artwork, Evidence } from './types';

export const DATASET_VERSION = 'aic-seed-v0.1';

export const ARTWORKS: Artwork[] = [
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

const src = (n: number, title: string) => ({
  source_id: `aic-api-${n}`,
  source_title: `${title} — Art Institute of Chicago API record`,
  source_url: `https://api.artic.edu/api/v1/artworks/${n}`,
  source_language: 'en',
  attribution: 'Art Institute of Chicago',
});
const CC0 = { license: 'CC0-1.0', license_url: 'https://creativecommons.org/publicdomain/zero/1.0/' };
const CCBY = { license: 'CC-BY-4.0', license_url: 'https://creativecommons.org/licenses/by/4.0/' };

export const CHUNKS: (Evidence & { topics: string[] })[] = [
  {
    chunk_id: 'aic-28560-medium', artwork_id: 'aic-28560', ...src(28560, 'The Bedroom'), ...CC0,
    source_locator: 'data.medium_display', excerpt: 'Oil on canvas', normalization: 'none', topics: ['material'],
  },
  {
    chunk_id: 'aic-28560-description-01', artwork_id: 'aic-28560', ...src(28560, 'The Bedroom'), ...CCBY,
    source_locator: 'data.description, paragraph 1',
    excerpt: 'With its bold colors, thick and broken brushwork, and sharply receding lines, the picture might suggest a nervous energy.',
    normalization: 'HTML removed; whitespace normalized.', topics: ['brushwork', 'energy', 'color', 'perspective'],
  },
  {
    chunk_id: 'aic-11723-medium', artwork_id: 'aic-11723', ...src(11723, 'Woman at Her Toilette'), ...CC0,
    source_locator: 'data.medium_display', excerpt: 'Oil on canvas', normalization: 'none', topics: ['material'],
  },
  {
    chunk_id: 'aic-11723-description-01', artwork_id: 'aic-11723', ...src(11723, 'Woman at Her Toilette'), ...CCBY,
    source_locator: 'data.description, paragraph 1',
    excerpt: 'In the background, Morisot’s soft, feathery brushstrokes suggest a floral-patterned bedspread and wallpaper.',
    normalization: "HTML removed; whitespace normalized; the source's split floral ligature normalized to floral.",
    topics: ['brushwork', 'background'],
  },
];

export const TOPIC_ALIASES: Record<string, string[]> = {
  material: ['material', 'materials', 'medium', 'made of', 'oil', 'canvas', '材料', '材质', '媒介', '油画', '画布', '底材'],
  brushwork: ['brushwork', 'brushstroke', 'brushstrokes', 'brush stroke', 'brush strokes', '笔触', '画痕', '笔痕'],
  energy: ['nervous', 'energy', '紧张'],
  color: ['color', 'colors', 'colour', 'colours', '颜色', '色彩'],
  perspective: ['perspective', 'receding lines', '透视'],
  background: ['background', 'wallpaper', 'bedspread', '背景', '墙纸', '床罩'],
};

type Text = { en: string; 'zh-CN': string };
export interface Fixture {
  questions: string[]; // already normalized for matching
  artworks: string[] | 'any';
  chunk: ((artworkId: string) => string) | null; // null = known source gap
  text: (artworkId: string) => Text;
}

const MATERIAL: Text = { en: 'The museum records the work as an oil painting on canvas.', 'zh-CN': '馆方记录的材质是布面油画。' };
const BRUSH: Record<string, Text> = {
  'aic-28560': { en: 'The museum describes thick, broken brushwork.', 'zh-CN': '馆方描述了厚重、断续的笔触。' },
  'aic-11723': {
    en: 'The museum describes light, featherlike strokes that suggest a patterned bed covering and wallpaper in the background.',
    'zh-CN': '馆方描述了柔和、羽毛般的笔触，暗示背景中带花卉图案的床罩和墙纸。',
  },
};

export const MATERIAL_FIXTURE: Fixture = {
  questions: ['what material is this artwork made of', '这件作品用了什么材料'],
  artworks: 'any', chunk: (a) => `${a}-medium`, text: () => MATERIAL,
};
export const BRUSHWORK_FIXTURE: Fixture = {
  questions: ["how does the museum describe this artwork's brushwork", '馆方如何描述这件作品的笔触'],
  artworks: 'any', chunk: (a) => `${a}-description-01`, text: (a) => BRUSH[a],
};
export const FIXTURES: Fixture[] = [
  MATERIAL_FIXTURE,
  BRUSHWORK_FIXTURE,
  {
    questions: ['which elements might suggest nervous energy, according to the museum', '馆方认为,画面里的哪些元素可能让人感到紧张'],
    artworks: ['aic-28560'], chunk: () => 'aic-28560-description-01',
    text: () => ({
      en: 'According to the museum, the vivid colors, thick and broken brushwork, and strongly receding lines may create a sense of nervous energy.',
      'zh-CN': '馆方认为，鲜明的色彩、厚重而断续的笔触，以及明显向纵深延伸的线条，可能让画面显出紧张的活力。',
    }),
  },
  {
    questions: ['how many hours did van gogh actually spend painting the chicago version', '梵高画完芝加哥收藏的这一版,实际花了多少小时'],
    artworks: ['aic-28560'], chunk: null,
    text: () => ({
      en: 'The material available in this demo does not state how many hours this version took to paint.',
      'zh-CN': '本演示现有资料没有说明画完这一版本实际花了多少小时。',
    }),
  },
  {
    questions: ["what is the woman's real name", '画中女子的真实姓名是什么'],
    artworks: ['aic-11723'], chunk: null,
    text: () => ({
      en: "The material available in this demo does not establish the woman's real name.",
      'zh-CN': '本演示现有资料无法确定画中女子的真实姓名。',
    }),
  },
];

export const FOLLOW_UPS: { questions: string[]; fixture: Fixture }[] = [
  { questions: ['what about the material', '那它是用什么做的'], fixture: MATERIAL_FIXTURE },
  { questions: ['what about the brushwork', '那它的笔触呢'], fixture: BRUSHWORK_FIXTURE },
];

export const GENERAL_INSUFFICIENT: Text = {
  en: 'The material available in this demo does not provide enough evidence to answer this question.',
  'zh-CN': '本演示现有资料不足以回答这个问题。',
};
export const UNSUPPORTED: Text = {
  en: 'This demo uses fixed test answers, and this question is outside the supported examples. Try asking about the material or brushwork.',
  'zh-CN': '本演示使用固定测试回答，暂未覆盖这个问题。可以试着询问材质或笔触。',
};
