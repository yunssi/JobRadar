// @ts-check

import { daysSince, filterJobs, formatDate, validateDashboardData } from "./core.js";

/** @template {Element} T @param {string} selector @returns {T} */
function required(selector) {
  const element = document.querySelector(selector);
  if (!element) throw new Error(`Required element not found: ${selector}`);
  return /** @type {T} */ (element);
}

/** @type {{data: import('./core.js').DashboardData | null, query: string, company: string, recommendedOnly: boolean, activeOnly: boolean}} */
const state = { data: null, query: "", company: "", recommendedOnly: false, activeOnly: true };

const els = {
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
  recommended: required("#recommended-only"),
  active: required("#active-only"),
  sources: required("#source-grid"),
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

  for (const source of state.data.sources) {
    const card = /** @type {HTMLAnchorElement} */ (make("a", "source-card"));
    card.href = source.home;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    card.title = source.error || `${source.name} 공식 사이트`;
    card.setAttribute("aria-label", `${source.name} 공식 사이트 새 창에서 열기, 상태 ${labels[source.health]}`);

    const top = make("div", "source-top");
    top.append(
      make("strong", "", `${String(source.priority).padStart(2, "0")} · ${source.name}`),
      make("span", `health-dot ${source.health}`),
    );
    const detail = source.health === "healthy"
      ? `정상 · 후보 ${source.found}건 · ${formatDate(source.last_success)}`
      : `${labels[source.health]} · ${source.error || "상세 정보 없음"}`;
    card.append(top, make("p", "", detail));
    fragment.append(card);
  }
  els.sources.append(fragment);
}

function populateCompanies() {
  if (!state.data) return;
  for (const source of state.data.sources) {
    const option = /** @type {HTMLOptionElement} */ (make("option", "", `${source.priority}. ${source.name}`));
    option.value = source.id;
    /** @type {HTMLSelectElement} */ (els.company).append(option);
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
  els.scanStatus.textContent = baselineReady ? "오늘의 스캔 완료" : "첫 기준선 준비 중";
  els.scanTime.textContent = generatedAt ? `${formatDate(generatedAt)} 기준` : "첫 실행 전입니다";
}

async function init() {
  try {
    const response = await fetch(`./data/jobs.json?v=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`데이터 요청 실패: HTTP ${response.status}`);
    state.data = validateDashboardData(await response.json());
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
els.recommended.addEventListener("change", (event) => { state.recommendedOnly = checkedValue(event); renderJobs(); });
els.active.addEventListener("change", (event) => { state.activeOnly = checkedValue(event); renderJobs(); });

void init();
