import type { Metadata } from "next";
import { CustomizeExperience } from "@/components/customize/CustomizeExperience";

export const metadata: Metadata = {
  title: "Create your AI host · SoShop",
  description:
    "Design an AI shopping host: upload a face, pick a voice, write a pitch, and watch them deliver it live.",
};

// Public, anonymous (inherits the consumer route group's dark theme + the
// force-dynamic setup from app/(consumer)/layout.tsx).
export default function CustomizePage() {
  return <CustomizeExperience />;
}
