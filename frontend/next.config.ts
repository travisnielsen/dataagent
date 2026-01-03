import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Output as static files for Azure Static Web Apps
  output: "export",
  
  // Disable image optimization (not supported in static export)
  images: {
    unoptimized: true,
  },
  
  // Trailing slashes help with SWA routing
  trailingSlash: true,
};

export default nextConfig;
