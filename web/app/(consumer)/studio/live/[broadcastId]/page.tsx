import type { Metadata } from "next";
import { BroadcastStudio } from "@/components/broadcast/BroadcastStudio";

export const metadata: Metadata = { title: "Phòng điều khiển livestream · SoShop" };

export default async function BroadcastPage({
  params,
}: {
  params: Promise<{ broadcastId: string }>;
}) {
  const { broadcastId } = await params;
  return <BroadcastStudio broadcastId={broadcastId} />;
}
