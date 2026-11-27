// Domain types for pushing livestream sessions to TikTok Live, Shopee Live
// and other RTMP destinations. camelCase; `api.ts` adapts the generated
// snake_case wire types.

export type Platform = "tiktok" | "shopee" | "youtube" | "custom";

/** How pushing one session to one destination is going, as the server reports it. */
export type OutputState = "off" | "connecting" | "live" | "retrying" | "error";

export interface Destination {
  id: string;
  platform: Platform;
  platformLabel: string;
  name: string;
  serverUrl: string;
  hasKey: boolean;
  /** The stream key's last characters. The key itself never reaches the browser. */
  keyHint: string;
  createdAt: string;
  updatedAt: string;
}

/** One destination as seen from one livestream session. */
export interface Output {
  destination: Destination;
  enabled: boolean;
  state: OutputState;
  detail: string;
  stateAt: string | null;
}

export interface Outputs {
  /** The server pushes streams (restreaming switched on and ffmpeg installed). */
  available: boolean;
  /** Why it does not, when it does not. */
  problem: string | null;
  outputs: Output[];
}

export interface DestinationInput {
  platform: Platform;
  name: string;
  serverUrl: string;
  /** Left out on an update to keep the stored key; `""` removes it. */
  streamKey?: string;
}
