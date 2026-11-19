import type { Metadata } from "next";
import { WatchView } from "@/components/studio/WatchView";

export const metadata: Metadata = { title: "Xem livestream · SoShop" };

export default async function WatchPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return <WatchView sessionId={sessionId} />;
}
