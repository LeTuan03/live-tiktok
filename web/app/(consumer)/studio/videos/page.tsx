import type { Metadata } from "next";
import { Suspense } from "react";
import { PromoStudio } from "@/components/promo/PromoStudio";

export const metadata: Metadata = { title: "Video sản phẩm AI · SoShop" };

export default function PromoVideosPage() {
  return (
    <Suspense>
      <PromoStudio />
    </Suspense>
  );
}
