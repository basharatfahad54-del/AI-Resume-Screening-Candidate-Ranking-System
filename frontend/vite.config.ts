import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

// `import.meta.url` rather than `__dirname`: the package is ESM ("type":
// "module"), so CommonJS globals do not exist in this file.
const srcDir = fileURLToPath(new URL('./src', import.meta.url))

// The dev server proxies /api to the FastAPI backend so the browser sees a
// single origin. That keeps CORS and relative URLs out of the picture during
// development while still calling the real API. In production the same path is
// served by nginx (see docker/nginx.conf), so VITE_API_URL can stay empty.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': srcDir,
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
        },
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})