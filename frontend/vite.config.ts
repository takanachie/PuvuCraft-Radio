import { fileURLToPath, URL } from 'node:url'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const backend = env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: backend,
          changeOrigin: true,
        },
        '/hls': {
          target: backend,
          changeOrigin: true,
        },
      },
    },
    build: {
      // hls.js is isolated and loaded only when native HLS is unavailable.
      chunkSizeWarningLimit: 550,
    },
    test: {
      environment: 'node',
      clearMocks: true,
    },
  }
})
