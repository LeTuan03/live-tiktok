import type { Metadata } from "next";
import { StudioHome } from "@/components/studio/StudioHome";

export const metadata: Metadata = { title: "Studio livestream · SoShop" };

export default function StudioPage() {
  return <StudioHome />;
}
