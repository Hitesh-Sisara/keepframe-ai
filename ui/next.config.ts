import type { NextConfig } from "next";

// Static export to ui/out (S3 + CloudFront, or the interim API-Gateway-served path under /ui). Dynamic ids travel in the query string
// (/events?id=…, /groups?event=…&id=…) so every page is a plain prebuilt HTML file.
// NEXT_PUBLIC_BASE_PATH=/ui builds the interim variant served from the HTTP API (docs/DEPLOY.md);
// unset (CloudFront) serves from the root.
const basePath = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");
const config: NextConfig = {
  output: "export",
  ...(basePath ? { basePath } : {}),
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
};

export default config;
