// Channel-ring Feed root (`/`).
//
// Headline consumer surface. The route stays a server component;
// `FeedRoot` (client) bootstraps the always-on channel list via
// `GET /channels` and hands it to `ChannelsShell`, which owns the
// `ChannelRoomCoordinator` lifecycle and renders the pager. The page
// is public, and historically write actions enforced sign-in at
// their call site via `<GatedAction>` wrappers.

import { FeedRoot } from "@/components/feed/FeedRoot";

export const dynamic = "force-dynamic";

export default function ConsumerHome() {
  return <FeedRoot />;
}
