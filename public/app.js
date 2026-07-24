// @ts-check

import { daysSince, filterJobs, formatDate, validateDashboardData } from "./core.js";

const FIT_ORDER = /** @type {const} */ (["core", "adjacent", "low"]);
const FIT_META = {
  core: { label: "핵심", groupLabel: "핵심 직무군", description: "시설·전기·통신·방재·관제 중심" },
  adjacent: { label: "보조", groupLabel: "보조 직무군", description: "보안·미화·운영 중심" },
  low: { label: "낮음", groupLabel: "낮은 적합도", description: "현재 목표 직무와 거리가 있는 출처" },
};
const ORGANIZATION_LABELS = {
  public_subsidiary: "공공기관 자회사",
  public_institution: "공공기관",
  local_public_corporation: "지방공기업",
  public_affiliate: "공공계열사",
};

/** @template {Element} T @param {string} selector @returns {T} */
function required(selector) {
  const element = document.querySelector(selector);
  if (!element) throw new Error(`Required element not found: ${selector}`);
  return /** @type {T} */ (element);
}

/** @type {{data: import('./core.js').DashboardData | null, query: string, company: string, recommendedOnly: boolean, activeOnly: boolean, jobFit: 'preferred' | 'core' | 'adjacent' | 'low' | 'all', sourceFits: Record<string, 'core' | 'adjacent' | 'low'>}} */
const state = {
  data: null,
  query: "",
  company: "",
  recommendedOnly: false,
  activeOnly: true,
  jobFit: "preferred",
  sourceFits: {},
};

const els = {
  heroCopy: required("#hero-copy"),
  scanStatus: required("#scan-status"),
  scanTime: required("#scan-time"),
  total: required("#metric-total"),
  totalDetail: required("#metric-total-detail"),
  newCount: required("#metric-new"),
  health: required("#metric-health"),
  healthDetail: required("#metric-health-detail"),
  resultCount: required("#result-count"),
  jobs: required("#jobs"),
  empty: required("#empty"),
  search: required("#search"),
  company: required("#company-filter"),
  jobFit: required("#job-fit-filter"),
  recommended: required("#recommended-only"),
  active: required("#active-only"),
  sources: required("#source-grid"),
  sourcesTitle: required("#sources-title"),
};

/** @param {string} tag @param {string} className @param {string} [text] */
function make(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderJobs() {
  if (!state.data) return;
  const jobs = filterJobs(state.data.jobs, state);
  els.jobs.replaceChildren();
  /** @type {HTMLElement} */ (els.empty).hidden = jobs.length !== 0;
  els.resultCount.textContent = `총 ${jobs.length.toLocaleString("ko-KR")}건`;
  const fragment = document.createDocumentFragment();

  for (const job of jobs) {
    const card = /** @type {HTMLAnchorElement} */ (make("a", "job-card"));
    card.href = job.url;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    card.setAttribute("aria-label", `${job.company} - ${job.title} 공식 원문 새 창에서 열기`);

    const company = make("div", "company");
    company.append(
      make("span", "company-index", String(job.priority).padStart(2, "0")),
      make("span", "", job.company),
    );

    const main = make("div", "job-main");
    main.append(make("h3", "", job.title));
    const tags = make("div", "tags");
    if (!job.baseline && daysSince(job.first_seen) < 1.5) tags.append(make("span", "tag new", "NEW"));
    if (job.active) tags.append(make("span", "tag active", "게시판 노출중"));
    for (const tag of job.tags) tags.append(make("span", "tag", tag));
    if (!job.tags.length) tags.append(make("span", "tag", "공식공고"));
    main.append(tags);

    const meta = make("div", "job-meta");
    meta.append(
      make("strong", "", job.score >= 3 ? `추천 ${job.score}점` : "공식 원문"),
      make("span", "", `발견 ${formatDate(job.first_seen)}`),
    );
    card.append(company, main, meta);
    fragment.append(card);
  }
  els.jobs.append(fragment);
}

function renderSources() {
  if (!state.data) return;
  els.sources.replaceChildren();
  const fragment = document.createDocumentFragment();
  const labels = { healthy: "정상", degraded: "점검 필요", error: "수집 실패" };

  for (const fit of FIT_ORDER) {
    const sources = state.data.sources.filter((source) => source.job_fit === fit);
    if (!sources.length) continue;
    const meta = FIT_META[fit];
    const group = make("section", `source-group fit-${fit}`);
    const headingId = `source-group-${fit}`;
    group.setAttribute("aria-labelledby", headingId);

    const heading = make("div", "source-group-heading");
    const title = make("h3", "", meta.groupLabel);
    title.id = headingId;
    heading.append(
      title,
      make("p", "", `${meta.description} · ${sources.length.toLocaleString("ko-KR")}곳`),
    );

    const grid = make("div", "source-grid");
    for (const source of sources) {
      const card = /** @type {HTMLAnchorElement} */ (make("a", `source-card fit-${fit}`));
      card.href = source.home;
      card.target = "_blank";
      card.rel = "noopener noreferrer";
      card.title = source.error || `${source.name} 공식 사이트`;
      const organizationLabel = ORGANIZATION_LABELS[source.organization_type];
      card.setAttribute(
        "aria-label",
        `${source.name} 공식 사이트 새 창에서 열기, ${meta.label} 적합도, ${organizationLabel}, 상태 ${labels[source.health]}`,
      );

      const top = make("div", "source-top");
      const healthDot = make("span", `health-dot ${source.health}`);
      healthDot.setAttribute("aria-hidden", "true");
      top.append(
        make("strong", "", `${String(source.priority).padStart(2, "0")} · ${source.name}`),
        healthDot,
      );
      const badges = make("div", "source-badges");
      badges.append(
        make("span", `source-badge fit-${fit}`, meta.label),
        make("span", "source-badge organization", organizationLabel),
      );
      const detail = source.health === "healthy"
        ? `정상 · 후보 ${source.found}건 · ${formatDate(source.last_success)}`
        : `${labels[source.health]} · ${source.error || "상세 정보 없음"}`;
      card.append(top, badges, make("p", "", detail));
      grid.append(card);
    }
    group.append(heading, grid);
    fragment.append(group);
  }
  els.sources.append(fragment);
}

function populateCompanies() {
  if (!state.data) return;
  const select = /** @type {HTMLSelectElement} */ (els.company);
  const allOption = /** @type {HTMLOptionElement} */ (make("option", "", "전체 회사"));
  allOption.value = "";
  select.replaceChildren(allOption);

  for (const fit of FIT_ORDER) {
    const sources = state.data.sources.filter((source) => source.job_fit === fit);
    if (!sources.length) continue;
    const group = document.createElement("optgroup");
    group.label = FIT_META[fit].groupLabel;
    for (const source of sources) {
      const option = /** @type {HTMLOptionElement} */ (make("option", "", `${source.priority}. ${source.name}`));
      option.value = source.id;
      group.append(option);
    }
    select.append(group);
  }
}

function renderSummary() {
  if (!state.data) return;
  const { stats, generated_at: generatedAt, baseline_ready: baselineReady } = state.data;
  els.total.textContent = stats.active_total.toLocaleString("ko-KR");
  els.totalDetail.textContent = `누적 ${stats.total.toLocaleString("ko-KR")}건`;
  els.newCount.textContent = stats.new_today.toLocaleString("ko-KR");
  els.health.textContent = `${stats.healthy_sources}/${stats.source_count}`;
  els.healthDetail.textContent = stats.degraded_sources || stats.failed_sources
    ? `점검 ${stats.degraded_sources} · 실패 ${stats.failed_sources}`
    : "모든 출처 정상";
  els.heroCopy.textContent = `수도권 공공계열 ${stats.source_count.toLocaleString("ko-KR")}곳의 채용 게시판을 매일 확인합니다. 새로 발견한 공고와 전환 가능성이 높은 직무를 한곳에서 보세요.`;
  els.sourcesTitle.textContent = `${stats.source_count.toLocaleString("ko-KR")}개 회사 감시 현황`;
  els.scanStatus.textContent = baselineReady ? "오늘의 스캔 완료" : "첫 기준선 준비 중";
  els.scanTime.textContent = generatedAt ? `${formatDate(generatedAt)} 기준` : "첫 실행 전입니다";
}

async function init() {
  try {
    const response = await fetch(`./data/jobs.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`데이터 요청 실패: HTTP ${response.status}`);
    state.data = validateDashboardData(await response.json());
    state.sourceFits = Object.fromEntries(
      state.data.sources.map((source) => [source.id, source.job_fit]),
    );
    renderSummary();
    populateCompanies();
    renderJobs();
    renderSources();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    els.scanStatus.textContent = "데이터를 불러오지 못했습니다";
    els.scanTime.textContent = "GitHub Actions의 마지막 실행을 확인해 주세요";
    els.jobs.replaceChildren(make("p", "empty", message));
  }
}

/** @param {Event} event */
function inputValue(event) {
  return /** @type {HTMLInputElement | HTMLSelectElement} */ (event.currentTarget).value;
}

/** @param {Event} event */
function checkedValue(event) {
  return /** @type {HTMLInputElement} */ (event.currentTarget).checked;
}

els.search.addEventListener("input", (event) => { state.query = inputValue(event); renderJobs(); });
els.company.addEventListener("change", (event) => { state.company = inputValue(event); renderJobs(); });
els.jobFit.addEventListener("change", (event) => {
  state.jobFit = /** @type {typeof state.jobFit} */ (inputValue(event));
  renderJobs();
});
els.recommended.addEventListener("change", (event) => { state.recommendedOnly = checkedValue(event); renderJobs(); });
els.active.addEventListener("change", (event) => { state.activeOnly = checkedValue(event); renderJobs(); });

void init();
