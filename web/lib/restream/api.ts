// Client for `/stream-destinations` and `/broadcasts/{id}/outputs`, on the
// generated SDK. The only module that sees the snake_case wire types.
//
// Calls run with `throwOnError: false`: on a non-2xx response the generated
// client hands back the parsed error body next to the `Response`, and the
// server's message (Vietnamese, user-facing) is kept in the `ApiError`.

import { ApiError } from "@/lib/api";
import {
  createDestinationStreamDestinationsPost,
  deleteDestinationStreamDestinationsDestinationIdDelete,
  listDestinationsStreamDestinationsGet,
  listOutputsBroadcastsBroadcastIdOutputsGet,
  switchOutputBroadcastsBroadcastIdOutputsDestinationIdPut,
  updateDestinationStreamDestinationsDestinationIdPatch,
} from "@/lib/api/generated";
import type {
  Destination as WireDestination,
  DestinationPatch,
  Output as WireOutput,
  OutputsResponse,
} from "@/lib/api/generated";
import { sdkOptions } from "@/lib/api/sdk";
import { errorMessageFromBody } from "@/lib/studio/api";
import type { Destination, DestinationInput, Output, Outputs } from "./types";

const OFFLINE = "Không kết nối được máy chủ API. Kiểm tra server đã chạy chưa.";

export function adaptDestination(wire: WireDestination): Destination {
  return {
    id: wire.id,
    platform: wire.platform,
    platformLabel: wire.platform_label,
    name: wire.name,
    serverUrl: wire.server_url,
    hasKey: wire.has_key,
    keyHint: wire.key_hint,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

export function adaptOutput(wire: WireOutput): Output {
  return {
    destination: adaptDestination(wire.destination),
    enabled: wire.enabled,
    state: wire.state,
    detail: wire.detail,
    stateAt: wire.state_at,
  };
}

export function adaptOutputs(wire: OutputsResponse): Outputs {
  return {
    available: wire.available,
    problem: wire.problem,
    outputs: wire.outputs.map(adaptOutput),
  };
}

interface Outcome<T> {
  data?: T;
  error?: unknown;
  response?: Response;
}

function options() {
  return { ...sdkOptions(), throwOnError: false as const };
}

async function unwrap<T>(pending: Promise<Outcome<T>>): Promise<T> {
  let outcome: Outcome<T>;
  try {
    outcome = await pending;
  } catch {
    throw new ApiError(0, OFFLINE);
  }
  const { data, error, response } = outcome;
  if (!response) throw new ApiError(0, OFFLINE);
  if (!response.ok) {
    const fallback = response.statusText || `HTTP ${response.status}`;
    throw new ApiError(response.status, errorMessageFromBody(error, fallback));
  }
  return data as T;
}

// -------------------------------------------------------------- destinations

export async function listDestinations(): Promise<Destination[]> {
  const data = await unwrap(listDestinationsStreamDestinationsGet(options()));
  return data.destinations.map(adaptDestination);
}

export async function createDestination(input: DestinationInput): Promise<Destination> {
  const wire = await unwrap(
    createDestinationStreamDestinationsPost({
      ...options(),
      body: {
        platform: input.platform,
        name: input.name.trim(),
        server_url: input.serverUrl.trim(),
        stream_key: (input.streamKey ?? "").trim(),
      },
    }),
  );
  return adaptDestination(wire);
}

export async function updateDestination(
  id: string,
  input: { name?: string; serverUrl?: string; streamKey?: string },
): Promise<Destination> {
  const body: DestinationPatch = {};
  if (input.name !== undefined) body.name = input.name.trim();
  if (input.serverUrl !== undefined) body.server_url = input.serverUrl.trim();
  if (input.streamKey !== undefined) body.stream_key = input.streamKey.trim();
  const wire = await unwrap(
    updateDestinationStreamDestinationsDestinationIdPatch({
      ...options(),
      path: { destination_id: id },
      body,
    }),
  );
  return adaptDestination(wire);
}

export async function deleteDestination(id: string): Promise<void> {
  await unwrap(
    deleteDestinationStreamDestinationsDestinationIdDelete({
      ...options(),
      path: { destination_id: id },
    }),
  );
}

// ------------------------------------------------------------------- outputs

export async function listOutputs(broadcastId: string): Promise<Outputs> {
  const wire = await unwrap(
    listOutputsBroadcastsBroadcastIdOutputsGet({
      ...options(),
      path: { broadcast_id: broadcastId },
    }),
  );
  return adaptOutputs(wire);
}

export async function switchOutput(
  broadcastId: string,
  destinationId: string,
  enabled: boolean,
): Promise<Outputs> {
  const wire = await unwrap(
    switchOutputBroadcastsBroadcastIdOutputsDestinationIdPut({
      ...options(),
      path: { broadcast_id: broadcastId, destination_id: destinationId },
      body: { enabled },
    }),
  );
  return adaptOutputs(wire);
}
