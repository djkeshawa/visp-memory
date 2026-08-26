/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'export',
  basePath: '/dashboard',
  images: {
    unoptimized: true,
  },
  distDir: 'out',
}

export default nextConfig
