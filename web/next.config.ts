import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  // The Windows desktop app (desktop/build-app.ps1) ships a self-contained
  // server.js that runs on a bare node.exe. Vercel and Docker builds leave it unset.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  },
};

export default nextConfig;
