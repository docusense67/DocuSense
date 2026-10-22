import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Same-origin API calls in dev so the SameSite=Strict session cookie works.
    proxy: { '/api': 'http://localhost:8000' },
  },
})
