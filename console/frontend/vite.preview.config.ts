import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  root: fileURLToPath(new URL('./preview', import.meta.url)),
  plugins: [react()],
  base: './',
  build: { outDir: '../dist-preview', emptyOutDir: true, sourcemap: false },
  server: { host: '127.0.0.1', port: 4175, strictPort: true },
});
