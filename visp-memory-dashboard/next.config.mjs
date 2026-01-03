/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  basePath: '/dashboard',
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  distDir: 'out',
}

export default nextConfig
