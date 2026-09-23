import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/market/',
  plugins: [vue()],
  optimizeDeps: {
    include: ['monaco-editor']
  },
  worker: {
    format: 'es'
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8093',
        changeOrigin: true
      },
      '/market/api': {
        target: 'http://127.0.0.1:8093',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/market/, '')
      }
    }
  }
})
