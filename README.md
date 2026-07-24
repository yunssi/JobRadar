# JobRadar

수도권 공공계열 시설·운영 관련 30개 회사·기관의 채용 게시판을 하루 한 번 확인하는
서버리스 모니터입니다. GitHub Actions가 PC 없이 수집·알림·배포를 수행하고, GitHub Pages
대시보드에서 현재 게시판에 노출된 공고와 출처별 상태를 확인할 수 있습니다.

## 동작 방식

- 매일 오전 6시 15분(KST)에 30개 채용 출처 확인
- 출처별 첫 성공 수집은 기존 공고를 기준선으로 저장하고 알림에서 제외
- 이후 처음 발견한 공고는 텔레그램으로 알린 뒤에만 지문을 저장
- 알림 실패 시 상태를 저장하지 않아 다음 실행에서 재시도하는 at-least-once 방식
- 합격자 발표·전형 결과·친인척 채용현황 등 비공고 게시물 제외
- 서울·경기·인천 및 시설·통신·관제·운영 직무에 추천 점수 부여
- 출처를 `핵심`, `보조`, `낮은 적합도`로 분류하고 화면에서 필터링
- 낮은 적합도 출처는 대시보드에 보존하되 텔레그램 알림에서는 제외
- 제목에 청년·체험형 인턴이나 명시적인 고경력 필수조건이 보이면 대시보드만 기록
- 사라진 공고는 삭제하지 않고 비활성 처리하며 화면은 기본적으로 현재 공고만 표시
- 접속 실패는 `수집 실패`, 일부 URL 실패·비정상적인 0건 전환은 `점검 필요`로 구분
- 한 회사가 실패해도 나머지 출처 수집은 계속 진행

수집기는 Python 표준 라이브러리만 사용합니다. Ruff·mypy·ESLint·TypeScript는 개발 및 CI
검증용 의존성으로 잠금 파일에 고정되어 있습니다.

## GitHub에 설치

### 1. 저장소 생성 및 푸시

GitHub에서 빈 저장소를 만든 뒤 이 폴더에서 실행합니다.

```powershell
git add .
git commit -m "Build daily job radar"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

GitHub Actions가 스캔 결과를 같은 브랜치에 커밋하므로 저장소의 `Workflow permissions`에서
읽기 및 쓰기 권한이 허용되어야 합니다. 브랜치 보호 규칙을 사용하는 경우
`github-actions[bot]`의 결과 커밋도 허용해야 합니다.

### 2. GitHub Pages 활성화

`Settings → Pages → Build and deployment → Source`에서 `GitHub Actions`를 선택합니다.
`Daily Job Scan`이 성공하면 검증된 `dist/` 산출물이 Pages에 배포됩니다.

### 3. 텔레그램 알림 연결(선택)

1. 텔레그램에서 `@BotFather`에게 `/newbot`을 보내 봇을 만들고 토큰을 받습니다.
2. 만든 봇과 대화를 시작한 후 메시지를 하나 보냅니다.
3. `https://api.telegram.org/bot<토큰>/getUpdates`에서 `chat.id`를 확인합니다.
4. `Settings → Secrets and variables → Actions`에 아래 Repository secret 두 개를 추가합니다.

| 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather가 발급한 토큰 |
| `TELEGRAM_CHAT_ID` | 알림을 받을 대화의 `chat.id` |

두 secret은 모두 설정하거나 모두 생략해야 합니다. 하나만 설정되면 잘못된 운영 설정으로 보고
워크플로가 실패합니다. 알림이 필요 없으면 둘 다 만들지 않아도 수집과 대시보드는 동작합니다.

### 4. 첫 실행과 확인

`Actions → Daily Job Scan → Run workflow`에서 수동 실행합니다. 이 프로젝트에는 로컬에서 만든
초기 기준선이 포함되어 있으므로, 그 이후 새로 생긴 공고만 알림 대상입니다. 기준선이 없는 출처는
첫 성공 시 기존 공고를 조용히 등록합니다. 수동 실행 입력의 `alert_on_first_run`을 켜면 예외적으로
기준선 공고도 알릴 수 있습니다.

실행이 끝나면 다음을 확인합니다.

- Actions 실행의 `Verify`, `Scan`, `Build`, `Deploy` 단계가 모두 성공
- 실행 요약의 정상·점검·실패 출처 수
- `Settings → Pages`에 표시된 대시보드 주소
- 텔레그램을 연결했다면 새 공고 발생 시 메시지 도착

## 로컬 개발과 전체 검증

Python 3.13, Node.js 22, [uv](https://docs.astral.sh/uv/)가 필요합니다.

```powershell
uv sync --locked --dev
npm ci --ignore-scripts

uv run ruff check .
uv run mypy monitor.py tests/test_monitor.py
uv run python -m unittest discover -s tests -v
npm run lint
npm run typecheck
npm test
npm run build
```

실제 사이트를 읽되 파일과 알림을 변경하지 않는 점검은 다음과 같습니다.

```powershell
uv run python monitor.py --dry-run
```

`npm run build`는 데이터 스키마, 동적으로 설정된 출처 수, 초기화된 기준선을 검증한 뒤 배포 파일을 `dist/`에
만듭니다. `dist/`는 생성물이라 Git에는 커밋하지 않습니다.

빌드한 화면을 로컬에서 확인하려면 `npm run serve`를 실행하고 `http://127.0.0.1:8765/`를 엽니다.

## 설정 위치

- 감시 회사와 공식 페이지: `config/sources.json`
- 실행 시각과 배포: `.github/workflows/daily-scan.yml`
- PR·push 품질 검사: `.github/workflows/ci.yml`
- 추천 키워드와 제외 규칙: `monitor.py`
- 화면과 필터: `public/index.html`, `public/app.js`, `public/styles.css`
- 영속 상태: `data/state.json`
- Pages용 데이터: `public/data/jobs.json`

목록의 ID와 우선순위는 중복될 수 없고, N개 출처의 우선순위는 정확히 1–N이어야 합니다.
`organization_type`은 공공기관 자회사·공공기관·지방공기업·공공계열사를 구분하고,
`job_fit`은 `core`, `adjacent`, `low` 중 하나를 사용합니다. 잘못되거나 손상된 설정·상태
JSON은 빈 상태로 덮어쓰지 않고 즉시 실패합니다.

한국도로공사서비스와 서울물재생시설공단 서버는 각각 필요한 TLS 중간 인증서를 전송하지 않으므로
해당 출처에만 인증기관의 공식 중간 인증서 파일을 추가합니다. `tls_ca_file`은 저장소 안의 유효한
인증서와 HTTPS URL에만 사용할 수 있으며, 시스템 신뢰 저장소·인증서 검증·호스트명 검증은 그대로
유지됩니다.

코레일네트웍스 채용 목록은 화면을 연 뒤 JavaScript가 별도 POST 요청으로 불러옵니다. 사용자에게
보여줄 공식 채용 페이지는 `urls`에 두고, 수집용 요청은 같은 사이트의 `post_request`에 정의합니다.
JSON으로 응답하는 공식 채용 포털은 `response_adapter`로 공고명과 상세 링크를 안전하게 변환합니다.
페이지 안에 JSON으로 공고를 넣는 채용 포털은 `document_adapter`로 진행 중인 공고를 변환합니다.
클라우드 IP에서 공식 게시판 접속을 막는 출처는 공식 홈페이지 링크를 유지하면서 회사의 사람인
채용 페이지 중 `진행중 공고` 섹션만 전용 `document_adapter`로 읽습니다.
목록에 종료일이 있는 출처는 `deadline_filter`로 지난 행을 제외하고, 종료 상태가 없는 게시판은
`active_window_days`로 최근 게시물만 포함합니다. 상세 본문의 접수기간을 확인해야 하는 출처는
`detail_deadline_filter`를 사용합니다. 상세 확인에 실패하거나 날짜 형식을 인식하지 못하면 누락
방지를 위해 공고는 포함하고 출처를 `점검 필요`로 표시합니다.

GitHub Actions cron은 UTC 기준입니다. `15 21 * * *`는 한국시간 다음 날 오전 6시 15분이며,
GitHub 실행 상황에 따라 시작이 몇 분 늦어질 수 있습니다.

## 장애 대응

- `수집 실패`: 공식 사이트 접속 차단·타임아웃·전체 URL 실패 여부를 실행 로그에서 확인합니다.
- 한국도로공사서비스에서 다시 인증서 오류가 나면 서버 인증서의 발급자가 바뀌었는지 확인한 뒤
  `certificates/`의 중간 인증서와 `tls_ca_file` 설정을 함께 검토합니다. 인증서 검증을 끄지 않습니다.
- 서울물재생시설공단의 인증서 오류도 같은 원칙으로 GlobalSign 중간 인증서와 발급자를 확인합니다.
- `점검 필요`: 일부 URL이 실패했거나, 채용 표시가 사라졌거나, 기존 5건 이상이 갑자기 0건이 된
  경우입니다. 공식 페이지 개편 여부를 확인하고 `config/sources.json` URL을 수정합니다.
- 텔레그램 단계 실패: 상태 파일은 갱신되지 않으므로 원인을 해결한 다음 워크플로를 재실행합니다.
  일부 메시지만 전달된 뒤 실패했다면 재시도 과정에서 중복 메시지가 생길 수 있습니다.
- 결과 커밋 실패: 브랜치 쓰기 권한과 보호 규칙을 확인합니다. 커밋되지 않은 실행 결과는 다음 실행의
  기준선이 되지 않습니다.
- 모든 출처 실패: 모니터가 종료 코드 2로 실패하고 Pages는 마지막 정상 배포본을 유지합니다.

모든 현재 공고를 새 기준선으로 다시 잡아야 하는 복구 상황에서만 `uv run python monitor.py
--reset-baseline`을 사용합니다. 이 실행에서 이미 보이는 모든 공고는 이후 알림 대상에서 제외됩니다.

공고 내용과 지원 자격은 반드시 연결된 회사 공식 원문에서 최종 확인해야 합니다.
