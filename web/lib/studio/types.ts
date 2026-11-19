// Domain types for the livestream studio. These are the camelCase shapes the
// UI works with; `api.ts` adapts them from the snake_case wire format.

export type StudioLanguage = "vi" | "en";

export type AvatarStatus = "none" | "processing" | "ready" | "failed";

export interface StudioVoice {
  id: string;
  label: string;
  language: string;
  gender: string;
}

export interface StudioModel {
  id: string;
  name: string;
  intro: string;
  personality: string;
  language: StudioLanguage;
  voice: string;
  imageUrl: string | null;
  avatarStatus: AvatarStatus;
  avatarProgress: number | null;
  avatarError: string | null;
  createdAt: string | null;
}

export type ProductStatus = "pending" | "presenting" | "done" | "skipped";

export interface StudioProduct {
  id: string;
  sessionId: string;
  position: number;
  name: string;
  price: number | null;
  currency: string;
  description: string;
  highlights: string;
  imageUrl: string | null;
  script: string;
  status: ProductStatus;
}

export type LiveMode = "starting" | "chatting" | "presenting" | "paused" | "ending";

export interface LiveState {
  mode: LiveMode;
  currentProductId: string | null;
  lastLine: string | null;
  updatedAt: string | null;
}

export type SessionStatus = "draft" | "live" | "ended";

export interface StudioSession {
  id: string;
  title: string;
  modelId: string | null;
  model: StudioModel | null;
  notes: string;
  status: SessionStatus;
  roomName: string | null;
  startedAt: string | null;
  endedAt: string | null;
  createdAt: string | null;
  live: LiveState | null;
  products: StudioProduct[];
}

export interface LiveConnection {
  livekitUrl: string;
  token: string;
  roomName: string;
}

export interface PublicProduct {
  id: string;
  position: number;
  name: string;
  price: number | null;
  currency: string;
  description: string;
  imageUrl: string | null;
  status: ProductStatus;
}

export interface PublicSession {
  id: string;
  title: string;
  status: SessionStatus;
  model: { name: string; imageUrl: string | null } | null;
  live: LiveState | null;
  products: PublicProduct[];
}

export type ControlAction =
  | "start_products"
  | "pause"
  | "resume"
  | "skip"
  | "present"
  | "stop_products"
  | "say";

/** Editable product fields, shared by create and update. */
export interface ProductInput {
  name: string;
  price: number | null;
  currency: string;
  description: string;
  highlights: string;
  imageUrl: string | null;
  script: string;
}

export interface ModelCreateInput {
  name: string;
  intro: string;
  personality: string;
  language: StudioLanguage;
  voice: string;
  image: File;
}

export interface ChatMessage {
  id: string;
  name: string;
  text: string;
  mine: boolean;
  at: number;
}
