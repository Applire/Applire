// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import type { NextConfig } from "next";

import { version } from "./package.json";

// Release builds bake the GitHub tag in via the APP_VERSION build arg
// (.github/workflows/release.yml → frontend/Dockerfile); dev and local
// builds fall back to package.json. Inlined at build time.
const appVersion = process.env.APP_VERSION
  ? process.env.APP_VERSION.startsWith("v")
    ? process.env.APP_VERSION
    : `v${process.env.APP_VERSION}`
  : `v${version}`;

const nextConfig: NextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_APP_VERSION: appVersion,
  },
  experimental: {
    // LLM operations (CV extraction, gap analysis) can take 30-60s+;
    // Next.js dev proxy defaults to 30s which causes ECONNRESET mid-request.
    proxyTimeout: Number(process.env.PROXY_TIMEOUT_MS ?? 300000),
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8001";
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
      {
        // Template thumbnails (and other backend static assets) are served by
        // the backend at /static. The frontend uses an empty API_BASE, so these
        // requests land on the Next origin — proxy them through to the backend,
        // mirroring the /api rule, otherwise every thumbnail 404s.
        source: "/static/:path*",
        destination: `${backendUrl}/static/:path*`,
      },
    ];
  },
};

export default nextConfig;
