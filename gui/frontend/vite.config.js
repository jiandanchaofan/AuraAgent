import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// `npm run build` outputs straight into gui/static/, which gui/server.py
// mounts and serves directly in production -- one `uvicorn gui.server:app`
// serves both the API/WebSocket and the built frontend, no separate static
// host needed. `npm run dev` instead proxies /ws to the FastAPI backend
// (assumed running on 8000) so the Vite dev server's hot-reload can be used
// against a real running AppContext.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
      },
    },
  },
})
