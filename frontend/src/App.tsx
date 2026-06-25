/**
 * 앱 루트 — 라우팅·레이아웃으로 페이지를 묶는다.
 *
 * 로그인은 제거됐다(데모, ADR-026): 백엔드가 토큰 없이 항상 마스터로 응답하므로
 * 프론트도 인증 게이트 없이 바로 메인 레이아웃을 렌더한다. API 클라이언트는 토큰
 * 없이 **1회** 만들어 모든 페이지에 주입한다(Authorization 헤더 생략).
 */

import { useMemo } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { createClient } from './api/client'
import { AppLayout } from './app/AppLayout'
import { DashboardPage } from './pages/DashboardPage'
import { SearchPage } from './pages/SearchPage'
import { ChatPage } from './pages/ChatPage'
import { DiffPage } from './pages/DiffPage'
import { ParsePage } from './pages/ParsePage'
import { ExtractionPage } from './pages/ExtractionPage'

function App() {
  // 토큰 게터는 항상 null — 인증 제거로 Authorization 헤더를 싣지 않는다.
  // createClient 시그니처는 호출부 보호 위해 유지한다.
  const client = useMemo(() => createClient(() => null), [])

  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppLayout client={client} />}>
          <Route index element={<DashboardPage client={client} />} />
          <Route path="search" element={<SearchPage client={client} />} />
          <Route path="chat" element={<ChatPage client={client} />} />
          <Route path="diff" element={<DiffPage client={client} />} />
          <Route path="parse" element={<ParsePage client={client} />} />
          <Route path="extract" element={<ExtractionPage client={client} />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
