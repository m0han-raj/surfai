export type { Favourite } from '@shared/types';

/** Payload for creating a favourite; the server assigns id and timestamps. */
export interface FavouriteDraft {
  name: string;
  url: string;
  domain?: string;
  intent?: string;
  description?: string;
  preferences?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}
