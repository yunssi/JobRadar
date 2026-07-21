import assert from "node:assert/strict";
import test from "node:test";

import {daysSince, filterJobs, validateDashboardData} from "../../public/core.js";

const jobs = [
  {
    id: "a", source_id: "one", company: "회사A", priority: 1,
    title: "서울 시설관리 신입 채용", url: "https://example.com/a", score: 9,
    tags: ["수도권", "관심직무"], first_seen: "2026-07-20T00:00:00Z",
    last_seen: "2026-07-20T00:00:00Z", baseline: false, active: true,
  },
  {
    id: "b", source_id: "two", company: "회사B", priority: 2,
    title: "지방 행정 경력직 채용", url: "https://example.com/b", score: 0,
    tags: [], first_seen: "2026-07-01T00:00:00Z", last_seen: "2026-07-02T00:00:00Z",
    baseline: true, active: false,
  },
];

const sources = [
  {
    id: "one", name: "회사A", priority: 1, home: "https://example.com/one",
    ok: true, health: "healthy", last_checked: "2026-07-20T00:00:00Z",
    last_success: "2026-07-20T00:00:00Z", found: 1, error: null,
  },
  {
    id: "two", name: "회사B", priority: 2, home: "https://example.com/two",
    ok: false, health: "error", last_checked: "2026-07-20T00:00:00Z",
    last_success: null, found: 0, error: "offline",
  },
];

test("filters by active, recommendation, company, and query", () => {
  assert.deepEqual(filterJobs(jobs, {query: "시설", company: "", recommendedOnly: true, activeOnly: true}), [jobs[0]]);
  assert.deepEqual(filterJobs(jobs, {query: "", company: "two", recommendedOnly: false, activeOnly: false}), [jobs[1]]);
  assert.deepEqual(filterJobs(jobs, {query: "", company: "two", recommendedOnly: false, activeOnly: true}), []);
});

test("daysSince handles invalid and deterministic timestamps", () => {
  assert.equal(daysSince("invalid", 0), Infinity);
  assert.equal(daysSince("2026-07-20T00:00:00Z", Date.parse("2026-07-21T00:00:00Z")), 1);
});

test("dashboard validator rejects malformed payloads", () => {
  assert.throws(() => validateDashboardData({stats: {}, sources: [], jobs: []}), TypeError);
});

test("dashboard validator accepts the production shape", () => {
  const data = validateDashboardData({
    generated_at: "2026-07-20T00:00:00Z",
    baseline_ready: true,
    stats: {total: 2, active_total: 1, new_today: 1, healthy_sources: 1, degraded_sources: 0, failed_sources: 1, source_count: 2},
    sources,
    jobs,
  });
  assert.equal(data.jobs.length, 2);
});

test("dashboard validator rejects unsafe links and inconsistent health totals", () => {
  const unsafe = sources.map((source) => ({...source}));
  unsafe[0].home = "javascript:alert(1)";
  const payload = {
    generated_at: null,
    baseline_ready: true,
    stats: {total: 0, active_total: 0, new_today: 0, healthy_sources: 1, degraded_sources: 0, failed_sources: 1, source_count: 2},
    sources: unsafe,
    jobs: [],
  };
  assert.throws(() => validateDashboardData(payload), /http or https/);

  payload.sources = sources;
  payload.stats.failed_sources = 0;
  assert.throws(() => validateDashboardData(payload), /health counts/);
});
