import type { Metadata } from "next";
import { ProductsPage } from "@/components/products/ProductsPage";

export const metadata: Metadata = { title: "Sản phẩm · SoShop" };

export default function StudioProductsPage() {
  return <ProductsPage />;
}
