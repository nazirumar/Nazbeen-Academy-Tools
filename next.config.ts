import type { NextConfig } from "next";

/**
 * Phase 9 hardening:
 * - `ignoreBuildErrors` is DISABLED: type errors fail the production build
 *   instead of being silently swallowed (quality gate).
 * - Security headers on every response, incl. a CSP matched to what the app
 *   actually needs (Next.js hydration uses inline bootstrap scripts, charts
 *   may create blob: workers, self-hosted fonts/images).
 * - In "direct mode" (NEXT_PUBLIC_BACKEND_ORIGIN set) the browser talks to
 *   that origin, so it is added to connect-src; in gateway mode everything
 *   is same-origin.
 */

const backendOrigin = (process.env.NEXT_PUBLIC_BACKEND_ORIGIN ?? "").replace(/\/$/, "");

const cspParts = [
  "default-src 'self'",
  // Next.js injects inline hydration scripts; 'unsafe-eval' is dev-only.
  `script-src 'self' 'unsafe-inline'${process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self'${backendOrigin ? " " + backendOrigin : ""}`,
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
];
const CSP = cspParts.join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: CSP },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
];

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: false,
  typescript: {
    // Phase 9: type errors must fail the build (was: ignoreBuildErrors: true).
    ignoreBuildErrors: false,
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
