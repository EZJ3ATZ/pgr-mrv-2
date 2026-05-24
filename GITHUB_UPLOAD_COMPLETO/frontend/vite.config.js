import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_URL || 'http://localhost:5000'

  return {
    plugins: [react()],
    base: mode === 'production' ? '/static/react/' : '/',
    resolve: {
      alias: { '@': path.resolve(__dirname, './src') },
    },
    server: {
      port: 5173,
      proxy: {
        '/controle': { target: apiTarget, changeOrigin: true },
        '/gerar':    { target: apiTarget, changeOrigin: true },
      },
    },
    build: {
      outDir: '../static/react',
      emptyOutDir: true,
    },
  }
})
