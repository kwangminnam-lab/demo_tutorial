/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 백엔드(FastAPI :8000)와의 CORS는 dev proxy로 우회한다.
// 상대경로 `/v1`·`/healthz` 호출 → 프록시 → 백엔드.
export default defineConfig({
  // 통합 이미지(백엔드가 SPA를 루트에서 서빙) — base는 항상 '/'. process.env를 안 써서
  // @types/node 불요. (서브경로 배포가 다시 필요하면 base만 조정한다.)
  base: '/',
  plugins: [react(), tailwindcss()],
  server: {
    // 모든 인터페이스 바인딩(IPv4 0.0.0.0 포함). 기본값은 localhost→::1(IPv6)만 잡혀,
    // 브라우저가 127.0.0.1 로 들어오면 연결 거부됐다. host:true 로 127.0.0.1·localhost 모두 응답.
    host: true,
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/healthz': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/setupTests.ts',
    css: false,
  },
})
