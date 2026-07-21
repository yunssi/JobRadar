// @ts-check

/** @typedef {'healthy' | 'degraded' | 'error'} Health */

/**
 * @typedef {object} Job
 * @property {string} id
 * @property {string} source_id
 * @property {string} company
 * @property {number} priority
 * @property {string} title
 * @property {string} url
 * @property {number} score
 * @property {string[]} tags
 * @property {string} first_seen
 * @property {string} last_seen
 * @property {boolean} baseline
 * @property {boolean} active
 */

/**
 * @typedef {object} Source
 * @property {string} id
 * @property {string} name
 * @property {number} priority
 * @property {string} home
 * @property {boolean} ok
 * @property {Health} health
 * @property {string | null} last_checked
 * @property {string | null} last_success
 * @property {number} found
 * @property {string | null} error
 */

/**
 * @typedef {object} DashboardData
 * @property {string | null} generated_at
 * @property {boolean} baseline_ready
 * @property {{total:number, active_total:number, new_today:number, healthy_sources:number, degraded_sources:number, failed_sources:number, source_count:number}} stats
 * @property {Source[]} sources
 * @property {Job[]} jobs
 */

/**
 * @typedef {object} JobFilters
 * @property {string} query
 * @property {string} company
 * @property {boolean} recommendedOnly
 * @property {boolean} activeOnly
 */

/** @param {unknown} value @returns {value is Record<string, unknown>} */
function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** @param {unknown} value @param {string} label @returns {asserts value is number} */
function assertCount(value, label) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new TypeError(`${label} must be a non-negative integer`);
  }
}

/** @param {unknown} value @param {string} label @returns {asserts value is string} */
function assertString(value, label) {
  if (typeof value !== "string" || !value.trim()) throw new TypeError(`${label} must be a non-empty string`);
}

/** @param {unknown} value @param {string} label @returns {asserts value is string} */
function assertHttpUrl(value, label) {
  assertString(value, label);
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new TypeError(`${label} must be a valid URL`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new TypeError(`${label} must use http or https`);
}

/** @param {unknown} value @param {string} label */
function assertNullableString(value, label) {
  if (value !== null && typeof value !== "string") throw new TypeError(`${label} must be string or null`);
}

/**
 * Fail clearly when a partial or corrupted deployment serves malformed data.
 * @param {unknown} value
 * @returns {DashboardData}
 */
export function validateDashboardData(value) {
  if (!isRecord(value) || !isRecord(value.stats) || !Array.isArray(value.sources) || !Array.isArray(value.jobs)) {
    throw new TypeError("대시보드 데이터 형식이 올바르지 않습니다.");
  }
  const requiredStats = ["total", "active_total", "new_today", "healthy_sources", "degraded_sources", "failed_sources", "source_count"];
  for (const key of requiredStats) assertCount(value.stats[key], `stats.${key}`);
  const stats = /** @type {DashboardData["stats"]} */ (value.stats);
  if (typeof value.baseline_ready !== "boolean") throw new TypeError("baseline_ready must be boolean");
  assertNullableString(value.generated_at, "generated_at");

  const sourceIds = new Set();
  const sourcePriorities = new Set();
  const sourcesById = new Map();
  const healthCounts = {healthy: 0, degraded: 0, error: 0};
  for (const [index, source] of value.sources.entries()) {
    if (!isRecord(source)) throw new TypeError(`sources[${index}] must be an object`);
    assertString(source.id, `sources[${index}].id`);
    assertString(source.name, `sources[${index}].name`);
    assertCount(source.priority, `sources[${index}].priority`);
    assertHttpUrl(source.home, `sources[${index}].home`);
    assertCount(source.found, `sources[${index}].found`);
    assertNullableString(source.last_checked, `sources[${index}].last_checked`);
    assertNullableString(source.last_success, `sources[${index}].last_success`);
    assertNullableString(source.error, `sources[${index}].error`);
    if (typeof source.ok !== "boolean") throw new TypeError(`sources[${index}].ok must be boolean`);
    if (!['healthy', 'degraded', 'error'].includes(String(source.health))) {
      throw new TypeError(`sources[${index}].health is invalid`);
    }
    if ((source.health === "error") === source.ok) {
      throw new TypeError(`sources[${index}] has inconsistent ok and health values`);
    }
    if (sourceIds.has(source.id)) throw new TypeError(`Duplicate source id: ${source.id}`);
    if (sourcePriorities.has(source.priority)) throw new TypeError(`Duplicate source priority: ${source.priority}`);
    sourceIds.add(source.id);
    sourcePriorities.add(source.priority);
    sourcesById.set(source.id, source);
    healthCounts[/** @type {Health} */ (source.health)] += 1;
  }

  const jobIds = new Set();
  for (const [index, job] of value.jobs.entries()) {
    if (!isRecord(job)) throw new TypeError(`jobs[${index}] must be an object`);
    for (const field of ["id", "source_id", "company", "title", "first_seen", "last_seen"]) {
      assertString(job[field], `jobs[${index}].${field}`);
    }
    assertHttpUrl(job.url, `jobs[${index}].url`);
    assertCount(job.priority, `jobs[${index}].priority`);
    assertCount(job.score, `jobs[${index}].score`);
    if (!Array.isArray(job.tags) || !job.tags.every((tag) => typeof tag === "string")) {
      throw new TypeError(`jobs[${index}].tags must be a string array`);
    }
    if (typeof job.baseline !== "boolean" || typeof job.active !== "boolean") {
      throw new TypeError(`jobs[${index}] must have boolean baseline and active values`);
    }
    if (!sourceIds.has(job.source_id)) throw new TypeError(`jobs[${index}] references an unknown source`);
    const source = sourcesById.get(job.source_id);
    if (!source) throw new TypeError(`jobs[${index}] references an unknown source`);
    if (job.company !== source.name || job.priority !== source.priority) {
      throw new TypeError(`jobs[${index}] is inconsistent with its source`);
    }
    if (jobIds.has(job.id)) throw new TypeError(`Duplicate job id: ${job.id}`);
    jobIds.add(job.id);
  }

  if (stats.source_count !== value.sources.length) throw new TypeError("stats.source_count is inconsistent");
  if (stats.total < value.jobs.length) throw new TypeError("stats.total is smaller than the jobs array");
  if (stats.active_total > stats.total || stats.new_today > stats.total) {
    throw new TypeError("dashboard job counts are inconsistent");
  }
  if (stats.total === value.jobs.length) {
    const renderedActive = value.jobs.filter((job) => job.active).length;
    if (renderedActive !== stats.active_total) throw new TypeError("stats.active_total is inconsistent");
  }
  if (
    stats.healthy_sources !== healthCounts.healthy
    || stats.degraded_sources !== healthCounts.degraded
    || stats.failed_sources !== healthCounts.error
  ) {
    throw new TypeError("dashboard source health counts are inconsistent");
  }
  return /** @type {DashboardData} */ (value);
}

/** @param {string | null | undefined} value */
export function formatDate(value) {
  if (!value) return "기록 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

/** @param {string | null | undefined} value @param {number} [now] */
export function daysSince(value, now = Date.now()) {
  if (!value) return Infinity;
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? Infinity : (now - timestamp) / 86400000;
}

/** @param {Job[]} jobs @param {JobFilters} filters */
export function filterJobs(jobs, filters) {
  const query = filters.query.trim().toLocaleLowerCase("ko-KR");
  return jobs.filter((job) => {
    const haystack = `${job.company} ${job.title} ${job.tags.join(" ")}`.toLocaleLowerCase("ko-KR");
    return (
      (!query || haystack.includes(query)) &&
      (!filters.company || job.source_id === filters.company) &&
      (!filters.recommendedOnly || job.score >= 3) &&
      (!filters.activeOnly || job.active)
    );
  });
}
