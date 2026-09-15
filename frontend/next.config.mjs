/** @type {import('next').NextConfig} */

// Local development runs the backend as a separate process on the host.
// Docker Compose runs it as another service, so the host name differs.
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

const nextConfig = {
  // Compression buffers a response until it is complete, which turns the
  // agent's server-sent events into one lump arriving forty seconds late.
  // The browser asks for gzip; the dev proxy obliges; the stream dies.
  compress: false,

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
