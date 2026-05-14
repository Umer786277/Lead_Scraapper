import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: [
    "192.168.100.164",
    "localhost",
  ],
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
