import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  agentRules: false,
  experimental: { useTypeScriptCli: false },
};

export default nextConfig;
