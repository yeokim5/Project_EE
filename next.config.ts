import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    const apiBaseUrl = process.env.PYTHON_API_BASE_URL;
    // Split-backend override: route /api/* to a separately-hosted Python API.
    if (apiBaseUrl) {
      return [
        {
          source: "/api/:path*",
          destination: `${apiBaseUrl.replace(/\/$/, "")}/api/:path*`,
        },
      ];
    }
    // Vercel-native: identity rewrite so the edge routes /api/* to the root
    // Python serverless functions (api/extract.py, api/export.py, api/process.py)
    // instead of letting the Next.js App Router 404 them (there are no
    // app/api/* routes by design).
    return [
      {
        source: "/api/:path*",
        destination: "/api/:path*",
      },
    ];
  },
};

export default nextConfig;
