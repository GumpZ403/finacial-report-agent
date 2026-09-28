import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 前后端分离：dev 时 /api 代理到 FastAPI 后端（E:\financial-report-agent 的 8000 端口）
// 生产构建：dist/ 可由 FastAPI 静态托管或任意静态服务器部署
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 23617,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
