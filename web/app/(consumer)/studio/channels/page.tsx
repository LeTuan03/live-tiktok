import type { Metadata } from "next";
import { ChannelsManager } from "@/components/restream/ChannelsManager";

export const metadata: Metadata = { title: "Kênh phát · SoShop" };

export default function ChannelsPage() {
  return <ChannelsManager />;
}
