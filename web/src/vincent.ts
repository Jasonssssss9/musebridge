import type { Artwork } from './api/types';

export const VINCENT_PATH = '/a/getty-103jnh/vincent';

/** A server-advertised capability is required even for the eligible artwork. */
export function vincentEntryUrl(artwork: Artwork, publicOrigin: string): string | null {
  if (artwork.artwork_id !== 'getty-103jnh' || artwork.slug !== 'getty-103jnh'
    || artwork.dataset_version !== 'getty-gallery-v2' || !artwork.conversation_modes?.includes('vincent')) return null;
  try {
    const origin = new URL(publicOrigin);
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname);
    if (origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash
      || (origin.protocol !== 'https:' && !(origin.protocol === 'http:' && local))) return null;
    return new URL(VINCENT_PATH, origin).href;
  } catch {
    return null;
  }
}
