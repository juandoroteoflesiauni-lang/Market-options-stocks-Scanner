import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "assets.coincap.io" },
      { protocol: "https", hostname: "cryptologos.cc" },
      { protocol: "https", hostname: "financialmodelingprep.com" },
      { protocol: "https", hostname: "assets.parqet.com" },
    ],
  },
};

export default nextConfig;
