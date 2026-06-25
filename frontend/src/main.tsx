import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

const rootElement = document.getElementById('root')
if (!rootElement) {
  throw new Error('root 엘리먼트를 찾을 수 없습니다 (index.html #root 누락)')
}

// 로그인 제거(데모, ADR-026): 인증 게이트 없이 App을 바로 렌더한다.
createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
