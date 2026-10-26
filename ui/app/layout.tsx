import type { Metadata, Viewport } from "next";
import { Shell } from "@/components/Shell";
import { THEME_BOOT } from "@/components/theme/ThemeToggle";
import "./globals.css";

// Static export: files in public/ are served under the basePath (/ui for the interim API-served build), and
// metadata paths are not prefixed for us, so prefix them here.
const BASE = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/+$/, "");
const asset = (p: string) => `${BASE}${p}`;
// Share previews need an absolute image URL; set NEXT_PUBLIC_SITE_URL at build time to get one.
const SITE = (process.env.NEXT_PUBLIC_SITE_URL ?? "").replace(/\/+$/, "");

export const metadata: Metadata = {
  ...(SITE ? { metadataBase: new URL(SITE) } : {}),
  title: { default: "KeepFrame · Review desk", template: "%s · KeepFrame" },
  description: "Every frame judged. Only the close calls reach you.",
  referrer: "no-referrer",
  robots: { index: false, follow: false },
  icons: {
    icon: [
      { url: asset("/favicon.svg"), type: "image/svg+xml" },
      { url: asset("/favicon-32.png"), sizes: "32x32", type: "image/png" },
      { url: asset("/icon-512.png"), sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: asset("/apple-touch-icon-180.png"), sizes: "180x180", type: "image/png" }],
  },
  openGraph: {
    type: "website",
    siteName: "KeepFrame",
    title: "KeepFrame · Review desk",
    description: "Every frame judged. Only the close calls reach you.",
    // Without a known site URL Next would fall back to http://localhost:3000 for the image, so it is only set
    // when NEXT_PUBLIC_SITE_URL is (the file still ships in out/ either way).
    ...(SITE ? { images: [{ url: asset("/og-image-1200x630.png"), width: 1200, height: 630, alt: "KeepFrame: three frames from one burst, the kept one lifted" }] } : {}),
  },
  twitter: { card: "summary_large_image", ...(SITE ? { images: [asset("/og-image-1200x630.png")] } : {}) },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f7f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1016" },
  ],
  colorScheme: "light dark",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
