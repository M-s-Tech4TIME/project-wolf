import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Phase 5.9-d: produce a self-contained production build at
  // .next/standalone/ with only the runtime deps the server needs.
  // wolf-dashboard.deb ships the standalone build at
  // /usr/lib/wolf-dashboard/ and the shipped CLI shim invokes
  // `node server.js` from there. `npm run dev` is unaffected by
  // this setting — it only changes what `npm run build` produces.
  output: "standalone",

  // Next refuses cross-origin requests in dev unless allowed. Wildcard the
  // private-network ranges so a LAN-IP rotation isn't a paper-cut. Loopback
  // is implicit. Public IPs are not matched.
  allowedDevOrigins: [
    "192.168.*.*",
    "10.*.*.*",
    "172.16.*.*", "172.17.*.*", "172.18.*.*", "172.19.*.*",
    "172.20.*.*", "172.21.*.*", "172.22.*.*", "172.23.*.*",
    "172.24.*.*", "172.25.*.*", "172.26.*.*", "172.27.*.*",
    "172.28.*.*", "172.29.*.*", "172.30.*.*", "172.31.*.*",
  ],
};

export default nextConfig;
