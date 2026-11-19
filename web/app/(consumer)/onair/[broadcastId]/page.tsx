import type { Metadata } from "next";
import { Suspense } from "react";
import { OnAirView } from "@/components/broadcast/OnAirView";

export const metadata: Metadata = { title: "Livestream · SoShop" };

export default async function OnAirPage({ params }: { params: Promise<{ broadcastId: string }> }) {
  const { broadcastId } = await params;
  return (
    <Suspense>
      <OnAirView broadcastId={broadcastId} />
    </Suspense>
  );
}
