import type { VincentReply } from './View';

// Development-only sample prose. Source cards reproduce two pinned reference records.
export const SAMPLE_REPLY: VincentReply = {
  id: 'preview-turn-1',
  question: 'What color did you call the irises?',
  text: 'I described them as violet irises in my letter. Getty’s later research explains that light has changed some of the colors you see today. What do you notice when you look at the flowers?',
  citations: [
    {
      kind: 'evidence', source_kind: 'artist_letter', chunk_id: 'vincent-772-garden-colors',
      artwork_id: 'getty-103jnh', source_id: 'vincent-letter-772',
      source_title: 'Vincent to Theo and Jo, 9 May 1889 (letter 772)',
      source_url: 'https://vangoghletters.org/vg/letters/let772/letter.html', source_language: 'en',
      source_locator: 'Saint-Rémy, section addressed to Theo, English translation p.trans.6. Editorial note 4 identifies Irises (F 608 / JH 1691).',
      excerpt: 'I have two others on the go — violet irises and a lilac bush. Two subjects taken from the garden.',
      license: 'CC-BY-NC-SA-4.0', license_url: 'https://creativecommons.org/licenses/by-nc-sa/4.0/',
      attribution: 'Vincent van Gogh, English edition edited by Leo Jansen, Hans Luijten and Nienke Bakker (Van Gogh Museum and Huygens Institute, 2009).',
      normalization: 'English edition wording. Editorial note markers omitted.',
    },
    {
      kind: 'evidence', source_kind: 'museum_record', chunk_id: 'vincent-getty-light-change',
      artwork_id: 'getty-103jnh', source_id: 'vincent-getty-ultra-violet-exhibition',
      source_title: 'Ultra-Violet: New Light on Van Gogh’s Irises — Getty exhibition record',
      source_url: 'https://www.getty.edu/art/collection/exhibition/16D4X1', source_language: 'en',
      source_locator: 'Exhibition Details, sentence beginning “Lastly”.',
      excerpt: 'Lastly, the exhibition reveals how light has irrevocably changed some of the colors in Irises.',
      license: 'CC-BY-4.0', license_url: 'https://creativecommons.org/licenses/by/4.0/',
      attribution: 'J. Paul Getty Museum, Museum Collection exhibition record 16D4X1.',
      normalization: 'One sentence from the exhibition description. Formatting removed.',
    },
  ],
};
