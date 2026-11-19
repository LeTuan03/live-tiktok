import type { Metadata } from "next";
import { ControlRoom } from "@/components/studio/ControlRoom";

export const metadata: Metadata = { title: "Phòng điều khiển · SoShop" };

export default async function StudioSessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <ControlRoom sessionId={sessionId} />;
}
