import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next 16 refuses cross-origin requests in dev unless explicitly allowed.
  // Add hostnames the browser may use to reach this dev server (LAN IPs,
  // VM hostnames, etc.).  Loopback is implicit.
  allowedDevOrigins: [
    "192.168.76.128",
    "192.168.76.129",
    "192.168.68.108",
    "192.168.68.114",
  ],
};

export default nextConfig;
