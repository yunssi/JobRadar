import hashlib
import io
import json
import os
import ssl
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import monitor


def make_sources() -> list[monitor.SourceConfig]:
    return [
        {
            "id": f"source-{priority}",
            "name": f"회사 {priority}",
            "priority": priority,
            "home": f"https://source-{priority}.example.com/",
            "urls": [f"https://source-{priority}.example.com/recruit"],
        }
        for priority in range(1, monitor.EXPECTED_SOURCE_COUNT + 1)
    ]


def make_job(source: monitor.SourceConfig, suffix: str = "current") -> monitor.JobRecord:
    title = f"2026년 {source['name']} 시설직 채용 {suffix}"
    url = f"{source['home']}jobs/{suffix}"
    job_id = monitor.fingerprint(source["id"], title, url)
    return {
        "id": job_id,
        "source_id": source["id"],
        "company": source["name"],
        "priority": source["priority"],
        "title": title,
        "url": url,
        "score": 5,
        "tags": ["관심직무"],
    }


def result_for(source: monitor.SourceConfig, *suffixes: str) -> monitor.CollectionResult:
    return monitor.CollectionResult(
        jobs=[make_job(source, suffix) for suffix in suffixes],
        errors=[],
        fetched_pages=1,
        has_recruitment_marker=True,
    )


class MonitorParsingTests(unittest.TestCase):
    def test_extracts_nested_korean_job_link(self) -> None:
        document = '<a href="/jobs/view?id=42"><span>2026년 제3차</span> 공무직사원 채용 공고</a>'
        links = monitor.extract_links(document)
        self.assertEqual(links[0].text, "2026년 제3차 공무직사원 채용 공고")
        self.assertTrue(monitor.is_job_title(links[0].text))

    def test_decodes_cp949_pages(self) -> None:
        text = "2026년 시설관리 직원 채용 공고"
        self.assertEqual(monitor.decode_body(text.encode("cp949")), text)

    def test_rejects_results_and_navigation(self) -> None:
        self.assertFalse(monitor.is_job_title("채용공고"))
        self.assertFalse(monitor.is_job_title("2026년 상반기 공무직 채용 최종합격자 발표"))
        self.assertFalse(monitor.is_job_title("2026년 직원채용 서류전형 결과"))
        self.assertFalse(monitor.is_job_title("임직원 사칭으로 인한 사기피해 예방 2026.03.27"))
        self.assertFalse(monitor.is_job_title("[공고 제2026-14호] 외부 심사위원 모집공고"))
        self.assertFalse(monitor.is_job_title("2026년 공개채용 사무직 면접심사 공고"))

    def test_accepts_real_world_variants(self) -> None:
        titles = [
            "경기남부지사 2026년 1차 기간제 직원 채용 공고",
            "비행장사업소 기간제 근로자 채용",
            "IBK서비스 시설직종 정규직 신규채용(서울시 용산구)",
            "(2026-3) 직원 채용 공고",
        ]
        for title in titles:
            with self.subTest(title=title):
                self.assertTrue(monitor.is_job_title(title))

    def test_url_normalization_keeps_record_id(self) -> None:
        url = "https://example.com/list?page=1&wr_id=42&utm_source=test&sst=hit&main=1"
        self.assertEqual(monitor.normalize_url(url), "https://example.com/list?wr_id=42")

    def test_job_links_are_not_followed_as_discovery_pages(self) -> None:
        link = monitor.Link(
            text="2026년 서울 시설관리 정규직 채용 공고",
            href="/board/view?bd_id=recruit&wr_id=42",
        )
        self.assertFalse(monitor.is_discovery_link(link, "https://example.com/"))

    def test_score_prefers_metro_target_and_entry(self) -> None:
        score, tags = monitor.job_score("서울 CCTV 통합관제 신입 계약직 채용")
        self.assertEqual(score, 9)
        self.assertEqual(tags, ["수도권", "관심직무", "전환친화"])

    def test_fingerprint_is_stable_across_tracking_queries(self) -> None:
        a = monitor.fingerprint("x", "직원 채용 공고", "https://example.com/view?wr_id=1&mode=view&page=2")
        b = monitor.fingerprint("x", "직원  채용 공고", "https://example.com/view?mode=view&page=9&wr_id=1")
        self.assertEqual(a, b)


class MonitorConfigurationTests(unittest.TestCase):
    def test_load_sources_accepts_exactly_twenty_unique_priorities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, make_sources())
            self.assertEqual([source["priority"] for source in monitor.load_sources(path)], list(range(1, 21)))

    def test_bundled_sectigo_intermediate_has_expected_fingerprint(self) -> None:
        path = monitor.ROOT / "certificates" / "sectigo-rsa-domain-validation-secure-server-ca.pem"
        der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
        self.assertEqual(
            hashlib.sha256(der).hexdigest(),
            "7fa4ff68ec04a99d7528d5085f94907f4d1dd1c5381bacdc832ed5c960214676",
        )

    def test_load_sources_accepts_repo_local_ca_for_https_source(self) -> None:
        sources = make_sources()
        sources[0]["tls_ca_file"] = "certificates/sectigo-rsa-domain-validation-secure-server-ca.pem"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(
            loaded[0]["tls_ca_file"],
            "certificates/sectigo-rsa-domain-validation-secure-server-ca.pem",
        )

    def test_load_sources_accepts_same_site_post_request(self) -> None:
        sources = make_sources()
        sources[0]["post_request"] = {
            "url": "https://source-1.example.com/board/data",
            "form": {"actionType": "005", "currentPage": "1"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["post_request"]["form"]["actionType"], "005")

    def test_load_sources_rejects_invalid_count_duplicate_and_url(self) -> None:
        cases: list[tuple[str, list[monitor.SourceConfig]]] = []
        too_few = make_sources()[:-1]
        cases.append(("count", too_few))

        duplicate = make_sources()
        duplicate[1]["id"] = duplicate[0]["id"]
        cases.append(("duplicate", duplicate))

        invalid_url = make_sources()
        invalid_url[0]["urls"] = ["javascript:alert(1)"]
        cases.append(("URL", invalid_url))

        missing_ca = make_sources()
        missing_ca[0]["tls_ca_file"] = "certificates/missing.pem"
        cases.append(("missing CA", missing_ca))

        escaped_ca = make_sources()
        escaped_ca[0]["tls_ca_file"] = "../outside.pem"
        cases.append(("escaped CA", escaped_ca))

        cross_site_post = make_sources()
        cross_site_post[0]["post_request"] = {
            "url": "https://untrusted.example.net/data",
            "form": {"actionType": "005"},
        }
        cases.append(("cross-site POST", cross_site_post))

        invalid_post_form = make_sources()
        invalid_post_form[0]["post_request"] = {
            "url": "https://source-1.example.com/data",
            "form": {"actionType": 5},  # type: ignore[dict-item]
        }
        cases.append(("invalid POST form", invalid_post_form))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            for label, value in cases:
                with self.subTest(label=label):
                    monitor.write_json(path, value)
                    with self.assertRaises(monitor.ConfigurationError):
                        monitor.load_sources(path)

    def test_fetch_adds_ca_to_default_verified_context(self) -> None:
        ca_file = monitor.ROOT / "certificates" / "sectigo-rsa-domain-validation-secure-server-ca.pem"
        context = mock.MagicMock()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = "정상 응답".encode()
        response.headers.get_content_charset.return_value = "utf-8"

        with (
            mock.patch("monitor.ssl.create_default_context", return_value=context),
            mock.patch("monitor.urlopen", return_value=response) as opener,
        ):
            self.assertEqual(
                monitor.fetch("https://example.com/recruit", retries=0, extra_ca_file=ca_file),
                "정상 응답",
            )

        context.load_verify_locations.assert_called_once_with(cafile=str(ca_file))
        self.assertIs(opener.call_args.kwargs["context"], context)

    def test_fetch_posts_encoded_form_with_referer(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"result"
        response.headers.get_content_charset.return_value = "utf-8"
        with mock.patch("monitor.urlopen", return_value=response) as opener:
            monitor.fetch(
                "https://example.com/data",
                retries=0,
                form_data={"actionType": "005", "currentPage": "1"},
                referer="https://example.com/recruit",
            )

        request = opener.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.data, b"actionType=005&currentPage=1")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded; charset=UTF-8")
        self.assertEqual(request.get_header("Referer"), "https://example.com/recruit")

    def test_collect_source_uses_extra_ca_only_for_configured_source(self) -> None:
        source = make_sources()[0]
        source["tls_ca_file"] = "certificates/sectigo-rsa-domain-validation-secure-server-ca.pem"
        with mock.patch("monitor.fetch", return_value="<html><body>채용정보</body></html>") as fetcher:
            monitor.collect_source(source)

        fetcher.assert_called_once_with(
            source["urls"][0],
            extra_ca_file=monitor.ROOT / source["tls_ca_file"],
        )

    def test_collect_source_uses_post_response_with_display_page_job_url(self) -> None:
        source = make_sources()[0]
        source["urls"] = ["https://source-1.example.com/recruit"]
        source["post_request"] = {
            "url": "https://source-1.example.com/board/data",
            "form": {"actionType": "005", "currentPage": "1"},
        }
        document = '<a href="javascript:show(42)">2026년 코레일네트웍스 시설직 공개채용 공고</a>'
        with mock.patch("monitor.fetch", return_value=document) as fetcher:
            result = monitor.collect_source(source)

        fetcher.assert_called_once_with(
            source["post_request"]["url"],
            extra_ca_file=None,
            form_data=source["post_request"]["form"],
            referer=source["urls"][0],
        )
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["url"], source["urls"][0])

    def test_malformed_state_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            original = b'{"schema_version": 2, broken'
            path.write_bytes(original)
            with self.assertRaisesRegex(monitor.ConfigurationError, "Invalid JSON"):
                monitor.load_state(path)
            self.assertEqual(path.read_bytes(), original)

    def test_migrates_v1_state_without_losing_known_jobs(self) -> None:
        source = make_sources()[0]
        job = make_job(source)
        job.update(
            {
                "first_seen": "2026-07-19T00:00:00Z",
                "last_seen": "2026-07-19T00:00:00Z",
                "baseline": True,
            }
        )
        old_state = {
            "schema_version": 1,
            "initialized_at": "2026-07-19T00:00:00Z",
            "known": {source["id"]: {job["id"]: job}},
            "source_status": {
                source["id"]: {
                    "ok": True,
                    "last_success": "2026-07-19T00:00:00Z",
                    "found": 1,
                }
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            monitor.write_json(path, old_state)
            migrated = monitor.load_state(path)

        migrated_job = migrated["known"][source["id"]][job["id"]]
        self.assertEqual(migrated["schema_version"], monitor.STATE_SCHEMA_VERSION)
        self.assertTrue(migrated_job["active"])
        self.assertTrue(migrated["source_status"][source["id"]]["initialized"])

    def test_reconciles_invalid_duplicate_and_removed_source_records(self) -> None:
        sources = make_sources()
        source = sources[0]
        first = make_job(source, "same")
        first.update(
            {
                "id": "legacy-a",
                "first_seen": "2026-07-18T00:00:00Z",
                "last_seen": "2026-07-19T00:00:00Z",
                "baseline": True,
                "active": False,
            }
        )
        duplicate = dict(first)
        duplicate.update(
            {
                "id": "legacy-b",
                "url": f"{first['url']}?main=1",
                "last_seen": "2026-07-20T00:00:00Z",
                "baseline": False,
                "active": True,
            }
        )
        invalid = dict(first)
        invalid.update({"id": "invalid", "title": "임직원 사칭으로 인한 사기피해 예방 2026.03.27"})
        removed_source = dict(first)
        removed_source.update({"id": "removed", "source_id": "removed-source"})
        state = monitor.default_state()
        state["known"] = {
            source["id"]: {"legacy-a": first, "legacy-b": duplicate, "invalid": invalid},
            "removed-source": {"removed": removed_source},
        }

        removed, merged = monitor.reconcile_state(state, sources)

        records = list(state["known"][source["id"]].values())
        self.assertEqual((removed, merged), (2, 1))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["first_seen"], "2026-07-18T00:00:00Z")
        self.assertEqual(records[0]["last_seen"], "2026-07-20T00:00:00Z")
        self.assertFalse(records[0]["baseline"])
        self.assertTrue(records[0]["active"])

    def test_partial_telegram_configuration_is_rejected(self) -> None:
        with (
            mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token"}, clear=True),
            self.assertRaises(monitor.ConfigurationError),
        ):
            monitor.telegram_send([])

    def test_telegram_http_failure_does_not_expose_bot_token(self) -> None:
        source = make_sources()[0]
        secret = "highly-secret-token"
        error = HTTPError(
            f"https://api.telegram.org/bot{secret}/sendMessage",
            401,
            "Unauthorized",
            hdrs=Message(),
            fp=io.BytesIO(),
        )
        self.addCleanup(error.close)
        with (
            mock.patch.dict(
                os.environ,
                {"TELEGRAM_BOT_TOKEN": secret, "TELEGRAM_CHAT_ID": "123"},
                clear=True,
            ),
            mock.patch("monitor.urlopen", side_effect=error),
            self.assertRaises(monitor.JobRadarError) as raised,
        ):
            monitor.telegram_send([make_job(source)])
        self.assertNotIn(secret, str(raised.exception))
        self.assertEqual(str(raised.exception), "Telegram HTTP 401")


class MonitorRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.sources_path = root / "sources.json"
        self.state_path = root / "state.json"
        self.public_path = root / "public" / "jobs.json"
        self.sources = make_sources()
        monitor.write_json(self.sources_path, self.sources)

    def run_monitor(self) -> int:
        return monitor.run(
            sources_path=self.sources_path,
            state_path=self.state_path,
            public_path=self.public_path,
        )

    def test_baseline_then_new_job_alerts_once_and_persists(self) -> None:
        def baseline(source: monitor.SourceConfig) -> monitor.CollectionResult:
            return result_for(source, "current")

        with (
            mock.patch("monitor.now_iso", return_value="2026-07-20T00:00:00Z"),
            mock.patch("monitor.collect_source", side_effect=baseline),
            mock.patch("monitor.telegram_send", return_value="skipped") as notifier,
        ):
            self.assertEqual(self.run_monitor(), 0)
            notifier.assert_called_once_with([])

        def with_one_new(source: monitor.SourceConfig) -> monitor.CollectionResult:
            suffixes = ("current", "new") if source["priority"] == 1 else ("current",)
            return result_for(source, *suffixes)

        with (
            mock.patch("monitor.now_iso", return_value="2026-07-21T00:00:00Z"),
            mock.patch("monitor.collect_source", side_effect=with_one_new),
            mock.patch("monitor.telegram_send", return_value="sent") as notifier,
        ):
            self.assertEqual(self.run_monitor(), 0)
            notified = notifier.call_args.args[0]
            new_id = make_job(self.sources[0], "new")["id"]
            self.assertEqual([job["id"] for job in notified], [new_id])

        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertFalse(saved["known"]["source-1"][new_id]["baseline"])
        self.assertTrue(saved["known"]["source-1"][new_id]["active"])

        with (
            mock.patch("monitor.now_iso", return_value="2026-07-22T00:00:00Z"),
            mock.patch("monitor.collect_source", side_effect=with_one_new),
            mock.patch("monitor.telegram_send", return_value="none") as notifier,
        ):
            self.assertEqual(self.run_monitor(), 0)
            notifier.assert_called_once_with([])

    def test_notification_failure_leaves_state_and_dashboard_unchanged(self) -> None:
        with (
            mock.patch("monitor.collect_source", side_effect=lambda source: result_for(source, "current")),
            mock.patch("monitor.telegram_send", return_value="skipped"),
        ):
            self.assertEqual(self.run_monitor(), 0)

        old_state = self.state_path.read_bytes()
        old_public = self.public_path.read_bytes()

        def with_one_new(source: monitor.SourceConfig) -> monitor.CollectionResult:
            suffixes = ("current", "new") if source["priority"] == 1 else ("current",)
            return result_for(source, *suffixes)

        with (
            mock.patch("monitor.collect_source", side_effect=with_one_new),
            mock.patch("monitor.telegram_send", side_effect=monitor.JobRadarError("network down")),
            self.assertRaisesRegex(monitor.JobRadarError, "network down"),
        ):
            self.run_monitor()

        self.assertEqual(self.state_path.read_bytes(), old_state)
        self.assertEqual(self.public_path.read_bytes(), old_public)

    def test_reset_baseline_suppresses_alerts_and_marks_all_records(self) -> None:
        with (
            mock.patch("monitor.collect_source", side_effect=lambda source: result_for(source, "current")),
            mock.patch("monitor.telegram_send", return_value="none"),
        ):
            self.assertEqual(self.run_monitor(), 0)

        def with_one_new(source: monitor.SourceConfig) -> monitor.CollectionResult:
            suffixes = ("current", "new") if source["priority"] == 1 else ("current",)
            return result_for(source, *suffixes)

        with (
            mock.patch("monitor.collect_source", side_effect=with_one_new),
            mock.patch("monitor.telegram_send", return_value="none") as notifier,
        ):
            result = monitor.run(
                reset_baseline=True,
                sources_path=self.sources_path,
                state_path=self.state_path,
                public_path=self.public_path,
            )

        self.assertEqual(result, 0)
        notifier.assert_called_once_with([])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(all(job["baseline"] for bucket in state["known"].values() for job in bucket.values()))
        payload = json.loads(self.public_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["stats"]["new_today"], 0)

    def test_one_failed_source_is_isolated_and_reported(self) -> None:
        def collect(source: monitor.SourceConfig) -> monitor.CollectionResult:
            if source["priority"] == 1:
                raise RuntimeError("blocked")
            return result_for(source, "current")

        with (
            mock.patch("monitor.collect_source", side_effect=collect),
            mock.patch("monitor.telegram_send", return_value="skipped"),
        ):
            self.assertEqual(self.run_monitor(), 0)

        payload = json.loads(self.public_path.read_text(encoding="utf-8"))
        first = payload["sources"][0]
        self.assertEqual(payload["stats"]["failed_sources"], 1)
        self.assertEqual(payload["stats"]["healthy_sources"], 19)
        self.assertEqual(first["health"], "error")
        self.assertIn("blocked", first["error"])

    def test_unexpected_drop_to_zero_is_degraded_and_jobs_become_inactive(self) -> None:
        source = self.sources[0]
        old_job = make_job(source, "old")
        old_job.update(
            {
                "first_seen": "2026-07-19T00:00:00Z",
                "last_seen": "2026-07-19T00:00:00Z",
                "baseline": True,
                "active": True,
            }
        )
        state = monitor.default_state()
        state["initialized_at"] = "2026-07-19T00:00:00Z"
        state["known"] = {source["id"]: {old_job["id"]: old_job}}
        state["source_status"] = {
            source["id"]: {
                "initialized": True,
                "ok": True,
                "health": "healthy",
                "last_checked": "2026-07-19T00:00:00Z",
                "last_success": "2026-07-19T00:00:00Z",
                "found": 5,
                "error": None,
            }
        }
        monitor.write_json(self.state_path, state)

        empty = monitor.CollectionResult(jobs=[], errors=[], fetched_pages=1, has_recruitment_marker=True)
        with (
            mock.patch("monitor.collect_source", return_value=empty),
            mock.patch("monitor.telegram_send", return_value="none"),
        ):
            self.assertEqual(self.run_monitor(), 0)

        payload = json.loads(self.public_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["stats"]["active_total"], 0)
        self.assertEqual(payload["stats"]["degraded_sources"], 1)
        self.assertEqual(payload["sources"][0]["health"], "degraded")
        self.assertIn("dropped from 5 to 0", payload["sources"][0]["error"])
        self.assertFalse(payload["jobs"][0]["active"])

    def test_all_sources_unavailable_returns_nonzero(self) -> None:
        with (
            mock.patch("monitor.collect_source", side_effect=RuntimeError("offline")),
            mock.patch("monitor.telegram_send", return_value="none"),
        ):
            self.assertEqual(self.run_monitor(), 2)


if __name__ == "__main__":
    unittest.main()
