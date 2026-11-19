// Single shared client for the SoShop API.
//
// Every web call goes through `authedFetch` so:
//   - the `Authorization: Bearer <token>` header is built in one place
//   - the canonical error envelope (`{ error: { code, message, fields? } }`)
//     is parsed once and surfaced as `ApiError` to callers
//
// The previous codebase had near-identical copies of this helper across
// the per-domain API modules (`products.ts`, `campaigns.ts`,
// `analytics.ts`) plus per-endpoint variants. Bug-fixes had to be made in
// every copy; the analytics one in particular had drifted (no 422-list
// parsing). One helper, one error class, one place to evolve the contract.

export function apiBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!url) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL is not set. Point it at the SoShop server deployment.",
    );
  }
  return url.replace(/\/$/, "");
}

/**
 * Canonical error type raised by `authedFetch` for any non-2xx response.
 *
 * `code` is a stable machine-readable identifier from the server (e.g.
 * `concurrent_rooms_exhausted`); `message` is the human-readable copy;
 * `fieldErrors` is populated for 422 responses so forms can highlight
 * the offending input. `extra` carries any structured server-side
 * context (e.g. `retry_after` on rate-limits).
 */
export class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  fieldErrors?: Record<string, string>;
  extra?: Record<string, unknown>;

  constructor(
    httpStatus: number,
    detail: string,
    options: {
      code?: string;
      fieldErrors?: Record<string, string>;
      extra?: Record<string, unknown>;
    } = {},
  ) {
    super(detail);
    this.name = "ApiError";
    this.status = httpStatus;
    this.detail = detail;
    this.code = options.code;
    this.fieldErrors = options.fieldErrors;
    this.extra = options.extra;
  }
}

type CanonicalErrorBody = {
  error?: {
    code?: unknown;
    message?: unknown;
    fields?: unknown;
    [key: string]: unknown;
  };
  // Legacy FastAPI shape. Still parsed so older deployments don't 500
  // the UI before the matching server is rolled out.
  detail?: unknown;
};

type FastApiValidationItem = {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
};

function parseFieldsList(raw: unknown): Record<string, string> | undefined {
  if (!Array.isArray(raw)) return undefined;
  const out: Record<string, string> = {};
  for (const item of raw as Array<FastApiValidationItem | { loc?: string; msg?: string }>) {
    // Canonical envelope: `{ loc: "field.path", msg: "..." }`.
    if (item && typeof item === "object" && typeof (item as { loc?: unknown }).loc === "string") {
      const loc = String((item as { loc?: string }).loc) || "_";
      out[loc] = String((item as { msg?: string }).msg ?? "Invalid value");
      continue;
    }
    // Legacy FastAPI: `{ loc: ["body", "field"], msg: "..." }`.
    if (item && typeof item === "object") {
      const fastApi = item as FastApiValidationItem;
      const loc = (fastApi.loc ?? []).slice(1).join(".") || "_";
      out[loc] = fastApi.msg ?? "Invalid value";
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

async function buildApiError(res: Response): Promise<ApiError> {
  let detail = res.statusText || `HTTP ${res.status}`;
  let code: string | undefined;
  let fieldErrors: Record<string, string> | undefined;
  let extra: Record<string, unknown> | undefined;

  try {
    const data = (await res.json()) as CanonicalErrorBody;
    if (data && typeof data.error === "object" && data.error !== null) {
      // Canonical envelope.
      const env = data.error;
      if (typeof env.message === "string") detail = env.message;
      else if (typeof env.code === "string") detail = env.code;
      if (typeof env.code === "string") code = env.code;
      fieldErrors = parseFieldsList(env.fields);
      const reservedKeys = new Set(["code", "message", "fields"]);
      const extras: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(env)) {
        if (!reservedKeys.has(k)) extras[k] = v;
      }
      if (Object.keys(extras).length > 0) extra = extras;
    } else if (data && "detail" in data) {
      // Legacy FastAPI envelope.
      const raw = data.detail;
      if (Array.isArray(raw)) {
        fieldErrors = parseFieldsList(raw);
        detail = "Validation failed";
      } else if (typeof raw === "string") {
        detail = raw;
      } else if (raw && typeof raw === "object" && "code" in raw) {
        const obj = raw as { code?: string; message?: string };
        if (typeof obj.code === "string") code = obj.code;
        detail = obj.message ?? obj.code ?? detail;
      }
    }
  } catch {
    // Non-JSON body; keep statusText.
  }

  return new ApiError(res.status, detail, { code, fieldErrors, extra });
}

export interface AuthedFetchInit extends RequestInit {
  /**
   * Optional bearer token. When omitted, `authedFetch` issues an
   * unauthenticated request. The backend is open access, so callers
   * normally omit it.
   */
  token?: string | null;
}

/**
 * Send a request to the SoShop API and surface non-2xx responses as
 * `ApiError`. Adds `Authorization: Bearer <token>` when a token is
 * supplied and `Content-Type: application/json` whenever a body is
 * present (callers can override either by setting headers explicitly).
 */
export async function authedFetch(path: string, init: AuthedFetchInit = {}): Promise<Response> {
  const { token, headers: rawHeaders, ...rest } = init;
  const url = path.startsWith("http")
    ? path
    : `${apiBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;

  const headers = new Headers(rawHeaders);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (rest.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, { ...rest, headers, cache: "no-store" });
  if (!res.ok) {
    throw await buildApiError(res);
  }
  return res;
}
