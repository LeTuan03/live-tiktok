// Runtime configuration for the generated SDK.
//
// The generated `lib/api/generated/client.gen.ts` creates an empty
// `@hey-api/client-fetch` client at module load. This file wires it
// up at app startup so every generated function (e.g.
// `listChannelsChannelsGet`, `getMyAdvertiserAdvertisersMeGet`) lands
// on the right base URL, carries a bearer token when one is supplied,
// and surfaces non-2xx responses as our canonical `ApiError`.
//
// USAGE
//
//   // Anywhere in app code:
//   import { listChannelsChannelsGet } from "@/lib/api/generated";
//   import { sdkOptions } from "@/lib/api/sdk";
//
//   const channels = await listChannelsChannelsGet(sdkOptions());
//
// A token may still be threaded through for endpoints that want one:
//
//   const result = await getMyAdvertiserAdvertisersMeGet(
//     sdkOptions({ token }),
//   );
//
// When migrating an existing hand-mirrored client (`lib/api/*.ts`),
// keep the camelCase public type + adapter — generated types are
// snake_case-shaped and a wire→domain adapter is still the right
// place to land the field renames.

import { client } from "@/lib/api/generated/client.gen";
import { ApiError, apiBaseUrl } from "@/lib/api";

let configured = false;

/**
 * Configure the SDK's shared client. Idempotent. Called automatically
 * the first time `sdkOptions()` runs but exported for tests that need
 * to reset the client between cases.
 */
export function configureSdkClient(): void {
  if (configured) return;
  client.setConfig({
    baseUrl: apiBaseUrl(),
    // `throwOnError: true` makes every generated method throw on non-2xx
    // instead of returning `{ data, error }`. We map the thrown response
    // into our canonical `ApiError` below.
  });
  configured = true;
}

interface SdkOptions {
  /** Optional bearer token. The backend is open access, so this is
   *  omitted in normal use; pass `null` (or nothing) for public calls. */
  token?: string | null;
  signal?: AbortSignal;
}

/**
 * Build a per-call options object for the generated SDK. Adds the
 * Authorization header when `token` is supplied and forwards an
 * AbortSignal for cancellation.
 *
 * The return type is intentionally `Record<string, unknown>`-shaped so
 * each generated function (which carries its own narrower `Options`
 * shape) can spread these defaults via `{...sdkOptions(), body: ...}`
 * without TS complaining about the absence of operation-specific
 * fields.
 */
export function sdkOptions(opts: SdkOptions = {}): {
  headers?: Record<string, string>;
  signal?: AbortSignal;
  throwOnError: true;
} {
  configureSdkClient();
  const headers: Record<string, string> = {};
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  return {
    headers,
    signal: opts.signal,
    throwOnError: true,
  };
}

/**
 * Adapt an `@hey-api/client-fetch` error (or arbitrary throwable) into
 * our canonical `ApiError`. Use inside a try/catch around an SDK call:
 *
 *   try {
 *     return await listChannelsChannelsGet(sdkOptions());
 *   } catch (err) {
 *     throw adaptSdkError(err);
 *   }
 *
 * The generated SDK throws a plain object `{ request, response }` when
 * `throwOnError: true`. We pull the canonical envelope off `response`
 * and produce the same `ApiError` callers already key on.
 */
export async function adaptSdkError(err: unknown): Promise<ApiError> {
  if (err instanceof ApiError) return err;
  const maybeResponse = (err as { response?: Response } | undefined)?.response;
  if (maybeResponse instanceof Response) {
    return await buildApiErrorFromResponse(maybeResponse);
  }
  const message = err instanceof Error ? err.message : "Request failed";
  return new ApiError(0, message);
}

async function buildApiErrorFromResponse(res: Response): Promise<ApiError> {
  // Reuse `lib/api.ts`'s envelope parser by lifting just the JSON shape;
  // we don't want a circular import on `authedFetch`, so the parsing
  // is duplicated minimally here. Keep the shape in sync with the
  // canonical envelope.
  let detail = res.statusText || `HTTP ${res.status}`;
  let code: string | undefined;
  try {
    const data = (await res.clone().json()) as {
      error?: { code?: unknown; message?: unknown };
    };
    if (data.error && typeof data.error === "object") {
      if (typeof data.error.message === "string") detail = data.error.message;
      if (typeof data.error.code === "string") code = data.error.code;
    }
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, detail, { code });
}
