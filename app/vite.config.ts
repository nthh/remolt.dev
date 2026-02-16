import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const commitSha = (process.env.VITE_COMMIT_SHA || 'dev').slice(0, 7);

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(commitSha),
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/proxy': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/vscode': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8080',
        ws: true,
      },
    },
  },
});
