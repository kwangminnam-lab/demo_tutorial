"""DocuX MCP 서버 — 카탈로그 챗봇 ↔ DocuX API 연결 통로.

FastMCP(streamable HTTP)로 6개 툴(search·ask_rag·get_document·compare_documents·
parse_document·list_sources)을 노출한다. 각 툴은 DOCUX_API_BASE의 REST API에
httpx로 proxy한다(이중 검증). 토큰은 변형 없이 forward.

K8S_MCP_INTEGRATION.html §9 참고.
"""
