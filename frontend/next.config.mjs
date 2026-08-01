/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // フロントと同一オリジンで /api/* をFastAPIへプロキシ (CORS不要・スマホ公開も1ポート)
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
};
export default nextConfig;
