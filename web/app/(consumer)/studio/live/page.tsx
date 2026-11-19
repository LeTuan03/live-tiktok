import type { Metadata } from "next";
import { BroadcastsHome } from "@/components/broadcast/BroadcastsHome";

export const metadata: Metadata = { title: "Phiên livestream · SoShop" };

export default function BroadcastsPage() {
  return <BroadcastsHome />;
}
