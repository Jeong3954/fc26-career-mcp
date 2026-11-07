# FC26 Career MCP

**FC26 Career MCP는 EA SPORTS FC 26 커리어 모드(선수 커리어·감독 커리어)를 MCP(Model Context
Protocol)로 Claude, ChatGPT, Gemini 등 모든 LLM에 연결합니다.** Live Editor 브리지 기반의 읽기 전용 도구입니다.

**EA SPORTS FC 26 오프라인 커리어**(스쿼드, 폼, 일정, 리그 순위, 이적, 오퍼, 선수 성장)를
실제 게임 데이터 그대로 아무 AI 어시스턴트에게나 물어볼 수 있습니다. 읽기 전용입니다.

xAranaktu의 **FC 26 Live Editor** 안에서 작은 Lua 스크립트가 돌면서 플레이 중에 JSON 파일을
씁니다. 의존성 없는 로컬 서버(Python 파일 하나)가 그 파일과 최신 세이브 사본을 읽어 8개의
`fc26_*` 도구를 제공합니다. Claude, ChatGPT/Codex, Gemini, Cursor, VS Code Copilot, Windsurf,
LM Studio 등 MCP·REST·붙여넣기 텍스트를 쓸 수 있는 어떤 AI와도 동작합니다. 게임에는 아무것도
쓰지 않고, PC 밖으로 아무것도 나가지 않습니다.

제작: **GLOUNDEV**.

> **비공식 팬 프로젝트입니다.** FC26 Career MCP는 Electronic Arts Inc.(EA SPORTS)와 제휴하거나
> 승인·후원을 받은 프로젝트가 아닙니다. "EA SPORTS FC"는 Electronic Arts Inc.의 상표입니다.
> FC 26 Live Editor는 xAranaktu가 만든 별도의 제3자 도구이며 설치와 사용 책임은 본인에게 있습니다. 오프라인 커리어 전용입니다. 세이브를 백업하세요. [docs/SAFETY.md](docs/SAFETY.md)

## 설치: 프롬프트 하나를 AI에게 붙여넣으세요

설치 프로그램은 없습니다. AI 어시스턴트가 대신 설치합니다. 필요한 것: Windows 10/11, FC 26(PC),
오프라인 커리어, 설치된 **FC 26 Live Editor**, 그리고 PC에서 명령을 실행할 수 있는 AI
어시스턴트(Claude Code, 컴퓨터 접근이 켜진 Claude Desktop, Codex CLI, Gemini CLI, Cursor,
VS Code Copilot 에이전트 등).

아래를 복사해 어시스턴트에게 붙여넣고, 요청하는 것만 해주면 됩니다(게임 실행과 어시스턴트
재시작 정도입니다):

```
이 PC에 "FC26 Career MCP"를 설치해줘.
1. 저장소 https://github.com/Jeong3954/fc26-career-mcp 를 %USERPROFILE%\fc26-career-mcp 에 받아줘 (git clone 또는 zip 다운로드).
2. 그 폴더의 INSTALL_FOR_AI.md
   (https://raw.githubusercontent.com/Jeong3954/fc26-career-mcp/main/INSTALL_FOR_AI.md) 를 열어 순서대로 정확히 따라줘. 내 Live Editor 폴더와 AI 클라이언트를
   찾고, 읽기 전용 브리지와 로컬 서버를 설치하고, 클라이언트 설정에 서버를 등록하고(설정 파일은
   먼저 백업), 헬스 체크를 돌리는 방법이 적혀 있어.
3. 지금 네가 돌고 있는 클라이언트에 등록해줘(모르겠으면 나한테 물어봐). 게임 파일, 안티치트,
   세이브는 절대 건드리지 말고, 내 파일은 아무것도 지우지 말고, Python 설치나 Live Editor autorun
   폴더에 다른 스크립트가 이미 있을 때는 나한테 먼저 물어봐.
4. 끝나면 헬스 체크 결과를 보여주고, 내가 게임 안에서 해야 할 몇 가지를 알려줘.
```

English: [README.md](README.md). 직접 설치하려면 아래 "수동 설치".

## 제공 도구

| 도구 | 내용 |
|---|---|
| `fc26_status` | 브리지 동작 여부, 게임 날짜, 스냅샷/하트비트 나이, 호환성, 모드(감독/선수), 새로고침 방법 |
| `fc26_my_career` | 감독: 클럽 개요(예산, 순위, 결과, 일정, 스쿼드 수, 열린 오퍼). 선수: 내 선수 프로필, 평점, 기록, 요청, 시즌 이력 |
| `fc26_squad` | 계약·평점·부상·시즌 기록이 있는 스쿼드(+세이브 추가 정보: 징계, 임대, 사전 계약, 유스) |
| `fc26_player` | 선수 1명(id 또는 이름): 스쿼드 상세, 이적 타깃 상세, 또는 상위 순위 행 + 세이브 추가 정보 |
| `fc26_competitions` | 게임 세계의 모든 대회: 순위표 / 일정 / 상위 선수 / 목록 |
| `fc26_transfers` | 내 클럽 이적, 열린 오퍼, 오퍼 이력, 입찰, 협상, 타깃 - 또는 시장 전체 + 클럽 재정 |
| `fc26_save_data` | 세이브에만 있는 표: 국가대표, 감독, 보드 이력, 징계, 임대, 사전 계약, 유스, 애증 클럽, 이전 클럽, 팀 역할 등 |
| `fc26_global_events` | 게임 이벤트 피드(전역, 내 클럽에 귀속하지 않음) + 범위가 표시된 디코딩 페이로드 |

모든 응답에 `_meta`(출처 live/save, 범위 내 클럽/내 선수/전역, 게임 날짜, 데이터 나이, 경고)가
붙어 어시스턴트가 "언제 기준" 값인지 말할 수 있습니다. 감독 커리어와 선수 커리어 모두 지원.
차이점: `skill/references/mode_matrix.md`.

예시 질문: "이번 시즌 최고 활약 선수와 계약 상황은?", "리그 순위표와 다음 3경기 보여줘",
"내 선수들에게 열린 오퍼는 어떤 단계야?", "챔피언스리그 득점 순위는?", "징계나 임대 중인 선수는?",
선수 커리어: "최근 평점 추세는?"

## 어떤 LLM이든

| 어시스턴트 | 연결 방식 |
|---|---|
| Claude Desktop, Claude Code, Cursor, Windsurf, VS Code Copilot, Gemini CLI, Codex CLI, LM Studio | MCP **stdio**, 설치 스크립트가 등록 |
| URL이 필요한 클라이언트 | MCP **Streamable HTTP** `http://127.0.0.1:26261/mcp` (`install.py --http-autostart`) |
| ChatGPT 등 MCP가 없는 어시스턴트 | **붙여넣기 모드**: `python server\fc26cm_cli.py all` 이 요약 텍스트를 출력; 또는 로컬 **REST** API + OpenAPI(`/openapi.json`) |

어시스턴트용 지침: [AGENTS.md](AGENTS.md)(모든 클라이언트) 또는 `skill/SKILL.md`(Claude 스킬).
설정 조각과 테스트 매트릭스: [docs/CLIENTS.md](docs/CLIENTS.md).

## 수동 설치

1. 저장소를 `%USERPROFILE%\fc26-career-mcp`에 둡니다(이후 옮기지 마세요).
2. 실제 Python 3.9 이상(`py -3 --version`; Microsoft Store 스텁은 안 됩니다).
3. `python installer\install.py --detect` 후 `python installer\install.py --client auto`
   (옵션: `--le-dir`, `--data-dir`, `--client <이름들>`, `--allow-coexist`, `--http-autostart`,
   `--dry-run`). 브리지를 `<Live Editor>\lua\autorun\`에 복사하고, `문서\FC26CareerMCP\`를 만들고,
   각 클라이언트 설정에 서버를 병합합니다(먼저 백업).
4. `python installer\healthcheck.py` - PASS / WARN / FAIL 한 줄씩.
5. Live Editor로 FC 26을 실행하고 오프라인 커리어를 불러온 뒤 **커리어 허브**에서 10초쯤
   기다리면 데이터 폴더에 `snapshot.json`이 생깁니다. AI 클라이언트를 완전히 재시작하고
   "내 커리어 어때?"라고 물어보세요.

autorun이 안 돌면: Live Editor -> Lua Engine -> `<Live Editor>\lua\autorun\fc26cm_bridge.lua` 로드 ->
허브 화면에서 Execute(F5). 제거: `python installer\uninstall.py`(`--purge`가 없으면 데이터 폴더 유지).

## 폴더 구성

```
bridge/      fc26cm_bridge.lua(수동) + fc26cm_bridge_autorun.lua       - Live Editor 안에서 실행
server/      fc26cm_mcp.py(stdio)  fc26cm_http.py(HTTP/REST)  fc26cm_cli.py(텍스트)  fc26cm_core.py
save/        save_reader.py + 벤더 fc26-mcp 파서(MIT)                   - 세이브 사본 파싱
installer/   install.py  uninstall.py  healthcheck.py                   - AI 또는 사용자가 실행
skill/       SKILL.md + references(Claude)   AGENTS.md(모든 클라이언트)   INSTALL_FOR_AI.md(AI용)
docs/        DESIGN  CLIENTS  COMPATIBILITY  TROUBLESHOOTING  SAFETY
tests/       unittest + 정제된 픽스처(감독 + 선수)                        python -m unittest discover -s tests
```

데이터 폴더: `%USERPROFILE%\Documents\FC26CareerMCP\`(`status.json`, `heartbeat.json`,
`snapshot.json`, `world.json`, `event_payloads.jsonl`, `save_extract.json`). `--data-dir`로 변경.
세이브 리더는 `%LOCALAPPDATA%\EA SPORTS FC 26\settings\`의 최신 세이브 **사본**을 읽습니다.

## 프라이버시와 안전

네트워크 없음: 서버는 클라이언트와 stdio로 대화하거나 127.0.0.1에서만 듣습니다. 읽기 전용(쓰기
핸들러 없음, 어시스턴트의 Lua 실행 없음, 테이블 덤프 없음). 오프라인 커리어 전용; 어떤 종류의
안티치트 우회도 없음. 모든 게임 호출은 `pcall` 아래; 읽기 한도(게임 하루당 무거운 읽기 1회, 경기
중 없음); 이상 징후가 보이면 카나리 검사가 메모리 읽기를 끕니다. 전문: [docs/SAFETY.md](docs/SAFETY.md).

## 호환성

| Live Editor | FC 26 | 상태 |
|---|---|---|
| v26.3.5 | TU 1.6.3 (exe 1.0.139.20381) | full |
| 그 외 | 모두 | partial (스쿼드, 능력치, 기록, 이름, 이벤트, 세이브 데이터; 순위표/일정/이적/오퍼 없음) |

업데이트 후: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). 문제 해결:
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## 자주 묻는 질문

**FC 26 커리어 모드를 Claude나 ChatGPT에 어떻게 연결하나요?**
FC 26 Live Editor를 설치한 뒤, 위 "설치"의 프롬프트를 PC에서 명령을 실행할 수 있는 AI(Claude Code,
Claude Desktop, Codex CLI, Gemini CLI, Cursor 등)에 붙여넣으세요. 브리지를 설치하고 `fc26-career-mcp`
MCP 서버를 등록합니다. 웹 ChatGPT는 `python server\fc26cm_cli.py all` 출력(요약)을 붙여넣거나 로컬
REST API(`http://127.0.0.1:26261/openapi.json`)를 쓰면 됩니다.

**선수 커리어에서도 되나요?**
네. 감독 커리어와 선수 커리어를 모두 지원합니다. 모드별 차이는 `skill/references/mode_matrix.md`에 있습니다.

**어떤 AI에서 쓸 수 있나요?**
Claude, ChatGPT, Gemini, LM Studio의 로컬 모델 등 모든 LLM과 MCP 클라이언트. MCP가 없으면 붙여넣기나
REST를 씁니다. [docs/CLIENTS.md](docs/CLIENTS.md)

**안전한가요? 안티치트에 걸리지 않나요?**
읽기 전용입니다. 게임·세이브·메모리에 아무것도 쓰지 않고 안티치트 우회 기능은 전혀 없습니다. 오프라인
커리어 전용이며 온라인 모드에서 Live Editor를 쓰지 마세요. Live Editor 자체는 제3자 도구로 사용 책임은
본인에게 있습니다. [docs/SAFETY.md](docs/SAFETY.md)

**데이터가 PC 밖으로 나가나요?**
아니요. 서버는 stdio 또는 127.0.0.1에서만 동작합니다. AI 제공자에게는 어시스턴트가 도구 답변에서
보내는 내용만 전달됩니다.

**EA SPORTS 공식 도구인가요?**
아니요. GLOUNDEV가 만든 비공식 팬 프로젝트이며 Electronic Arts와 무관하고 승인받지 않았습니다.

## 라이선스

MIT, (c) 2026 GLOUNDEV - [LICENSE](LICENSE). 제3자 고지(fc26-mcp 파서, MIT, (c) 2025 Afjal):
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). EA SPORTS FC는 Electronic Arts Inc.의
상표입니다. 이 프로젝트는 Electronic Arts / EA SPORTS와 제휴하거나 승인받지 않았습니다.
FC 26 Live Editor는 xAranaktu의 도구이며, 이 프로젝트는 xAranaktu와 무관합니다.
