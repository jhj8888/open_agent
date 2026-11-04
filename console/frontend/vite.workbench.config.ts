import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  root: fileURLToPath(new URL('./workbench', import.meta.url)),
  plugins: [react()],
  base: '/open-agent/',
  build: { outDir: '../dist-workbench', emptyOutDir: true, sourcemap: false },
  server: { host: '127.0.0.1', port: 4176, strictPort: true },
});
