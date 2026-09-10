# JobRadar 핸드오프

이 문서는 다른 Codex 세션이 이 저장소의 운영·수정을 바로 이어갈 수 있도록 작성한 작업 인계서다.

## 현재 상태

- 저장소: `yunssi/JobRadar`, 기본 브랜치: `main`
- 배포: GitHub Actions + GitHub Pages
- 대시보드: <https://yunssi.github.io/JobRadar/>
- 감시 대상: 수도권 공공계열 시설·운영 관련 30개 출처
- 마지막으로 검증한 커밋: `230020c` (`Bundle POMA missing TLS intermediate`)
- 마지막 상태 데이터 커밋: `d411a00` (`chore: update job radar 2026-07-24`)
- 마지막 성공 스캔 데이터: 2026-07-24 15:24:26 KST, 정상 30/30, 점검 필요 0, 실패 0, 저장 211건, 현재 133건, 신규 0건

> 위 수치는 과거 검증 결과다. 다음 세션에서는 반드시 최신 `Daily Job Scan` 실행 결과 또는 로컬 드라이런으로 현재 상태를 다시 확인한다.

## 첫 점검 순서

```powershell
git pull --ff-only origin main
uv sync --locked --dev
npm ci --ignore-scripts
uv run python monitor.py --dry-run
```

드라이런이 성공하면 GitHub의 `Actions → Daily Job Scan → Run workflow`를 실행한다.
입력 `alert_on_first_run`은 기본값인 `false`를 유지한다. 완료 후 실행 요약에서 정상·점검 필요·실패 출처 수를 확인한다.

전체 변경을 만들었을 때는 다음 검증을 수행한다.

```powershell
uv run ruff check .
uv run mypy monitor.py tests/test_monitor.py
uv run python -m unittest discover -s tests -v
npm run verify
git diff --check
```

## 운영 구조

```text
GitHub Actions (매일 06:15 KST)
  → monitor.py가 30개 공식/공식 연계 채용 출처 수집
  → data/state.json에 공고 지문·활성 상태·출처 건강도 저장
  → 새 공고만 Telegram 전송 (Secret이 설정된 경우)
  → public/data/jobs.json 생성
  → dist/ 빌드 및 GitHub Pages 배포
```

- 일정과 배포: `.github/workflows/daily-scan.yml`
- 출처 목록과 분류: `config/sources.json`
- 수집·필터·기준선·알림 로직: `monitor.py`
- 상태: `data/state.json`
- 대시보드 데이터: `public/data/jobs.json`
- 화면: `public/index.html`, `public/app.js`, `public/styles.css`

스캔 워크플로는 상태 파일과 대시보드 JSON을 `job-radar[bot]` 커밋으로 `main`에 자동 저장한다. 작업을 시작하기 전에 항상 pull/fetch하여 이 커밋을 먼저 반영한다.

## 기준선·알림 규칙

- 각 출처의 첫 성공 수집은 기존 공고를 `baseline`으로 저장하며 알리지 않는다.
- 이후 새 지문만 신규 공고로 처리한다.
- Telegram 전송이 실패하면 상태를 저장하지 않아 다음 실행에서 재시도한다.
- `job_fit: low` 출처와 제외 키워드 공고는 대시보드에는 남지만 Telegram으로 알리지 않는다.
- `--reset-baseline`은 모든 현재 공고를 새 기준선으로 만들어 알림을 없앤다. 명확한 복구 상황 외에는 절대 실행하지 않는다.
- 수동 실행의 `alert_on_first_run: true`는 기준선 공고까지 알릴 수 있으므로 일반 점검에는 사용하지 않는다.

## 출처 분류

| 분류 | 수 | 의미 |
|---|---:|---|
| `core` | 23 | 우선 지원·알림 대상 |
| `adjacent` | 6 | 보조 탐색·알림 대상 |
| `low` | 1 | 화면 전용, Telegram 제외 |

`config/sources.json`의 ID와 우선순위는 중복될 수 없고, 우선순위는 반드시 1부터 출처 수까지 연속돼야 한다.

## 전용 수집 어댑터와 주의 출처

일반 게시판 HTML만으로 안정적으로 읽기 어려운 출처는 `document_adapter` 또는 `post_request`를 쓴다.

| 출처 | 설정/방식 | 이유 |
|---|---|---|
| 한국마사회시설관리 | `applyin_recruit_collection` | 페이지 내 JSON 공고 데이터 |
| 코레일네트웍스 | `post_request` + `recruiter_jobnotice` | JavaScript가 POST JSON을 별도 호출 |
| LX파트너스 | `jobkorea_current_company` | 공식 사이트가 GitHub 러너에 빈 페이지를 줄 수 있어 잡코리아 진행 공고를 읽음 |
| 키콕스파트너스 | `saramin_current_company` | 공식 게시판의 클라우드 접근 문제 대응 |
| 우체국시설관리단 | `hrdms_recruitment_list` + XHR | 공식 HRDMS API에서 진행 중 공고를 읽고 hash-route 상세 링크 생성 |
| 서울물재생시설공단 | `swr_job_board` | JavaScript 게시판 링크를 상세 URL로 변환 |
| 케이워터운영관리 | `detail_deadline_filter` | 상세 페이지의 접수기간으로 마감 여부 확인 |

### TLS 중간 인증서

아래 서버들은 완전한 인증서 체인을 보내지 않는다. `monitor.py`는 시스템 CA 검증을 유지한 상태에서, 해당 출처에만 `tls_ca_file`을 추가한다. 인증서 검증을 끄거나 `CERT_NONE`을 사용하지 않는다.

| 출처 | 파일 |
|---|---|
| 한국도로공사서비스 | `certificates/sectigo-rsa-domain-validation-secure-server-ca.pem` |
| 우체국시설관리단 HRDMS API | `certificates/sectigo-public-server-authentication-ca-dv-r36.pem` |
| 서울물재생시설공단 | `certificates/globalsign-gcc-r6-alphassl-ca-2025.pem` |

인증서 오류가 재발하면 대상 서버의 leaf 인증서 발급자와 AIA(중간 인증서 다운로드 주소)를 확인하고, 발급자가 바뀌었을 때만 공식 CA에서 새 중간 인증서를 받아 PEM·SHA-256 지문 테스트·`tls_ca_file` 설정을 함께 갱신한다.

## 최근 안정화 이력

- `9d399eb`: 기존 20개에서 30개로 확장, 출처 분류와 대시보드 필터 추가
- `1662dff`: 초기 클라우드 수집 불안정 출처 보강
- `4d4baf0`: LX(잡코리아), 키콕스(사람인), 우체국시설관리단(HRDMS)용 안정 수집 경로 및 파서 추가
- `230020c`: 우체국시설관리단 HRDMS API의 Sectigo DV R36 중간 인증서 누락 대응
- `d411a00`: 최종 수동 스캔 상태 저장. LX 2건, 키콕스 3건, 우체국시설관리단 2건을 포함해 30/30 정상 확인

## 장애 대응 기준

- `수집 실패`: 해당 URL의 DNS·TLS·차단·타임아웃을 Actions 로그에서 확인한다. 한 출처 실패가 전체 실패를 의미하지는 않는다.
- `점검 필요`: 일부 URL 실패, 채용 표식 누락, 이전 5건 이상에서 갑자기 0건이 된 경우다. 사이트 개편 여부와 어댑터의 마커를 확인한다.
- 공고 수가 0건이어도 정상일 수 있다. 단, 기존에 다수였던 출처가 0건이 되면 자동으로 경고한다.
- 새 사이트 경로를 추가할 때는 먼저 로컬 `--dry-run`과 GitHub Actions 수동 실행을 모두 확인한다. 로컬과 GitHub 러너의 DNS·IP 차단·TLS 체인은 다를 수 있다.
- Actions가 새 상태 커밋을 push한 뒤에는 다시 `git pull --ff-only origin main` 한다.

## 다음 세션에 바로 사용할 요청 예시

```text
JobRadar를 이어서 점검해줘. 먼저 HANDOFF.md를 읽고 main을 최신화한 뒤,
마지막 Daily Job Scan 이후의 Actions 결과와 30개 출처 상태를 확인해줘.
문제가 있으면 원인을 진단하고, 내가 승인하면 수정·검증·커밋·푸시해줘.
```

## 보안 및 운영 금지 사항

- Telegram 토큰·chat ID는 GitHub Actions Secret으로만 관리한다. 로그·문서·커밋에 넣지 않는다.
- `data/state.json`을 수동으로 비우거나 전체 기준선을 무심코 재설정하지 않는다. 신규 알림의 중복 또는 누락으로 이어진다.
- 인증서 검증을 비활성화하지 않는다. 필요한 특정 CA 중간 인증서만 추가한다.
- 봇이 만든 상태 커밋을 reset/rebase로 지우지 않는다.
