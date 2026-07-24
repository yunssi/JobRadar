import hashlib
import io
import json
import os
import ssl
import tempfile
import unittest
from datetime import datetime
from email.message import Message
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import monitor


def make_sources(count: int = 20) -> list[monitor.SourceConfig]:
    return [
        {
            "id": f"source-{priority}",
            "name": f"회사 {priority}",
            "priority": priority,
            "organization_type": "public_subsidiary",
            "job_fit": "core",
            "home": f"https://source-{priority}.example.com/",
            "urls": [f"https://source-{priority}.example.com/recruit"],
        }
        for priority in range(1, count + 1)
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

    def test_extracts_title_element_from_metadata_heavy_anchor(self) -> None:
        title = "[공고 제2026-16호] 서울메트로환경 공무직 공개채용 공고"
        document = f"""
        <a href="/recruit/428">
          <p>366 <span><b>{title}</b> 서울메트로환경 2026.07.21 10</span></p>
          <p class="ver2">{title}</p><p>서울메트로환경</p><p>2026.07.21</p>
        </a>
        """
        accepted = [link for link in monitor.extract_links(document) if monitor.is_job_title(link.text)]
        self.assertIn(monitor.Link(text=title, href="/recruit/428"), accepted)

    def test_extracts_title_cell_from_clickable_table_row_without_duplicate(self) -> None:
        title = "[산은비즈] 정규직 채용공고(시설)"
        document = f"""
        <table><tr onclick="window.location='/recruit/view?UID=622'">
          <td>603</td><td>시설관리</td><td>산업은행 본점</td>
          <td>{title}</td><td>2026-07-31</td><td>접수중</td>
        </tr></table>
        """
        accepted = [link for link in monitor.extract_links(document) if monitor.is_job_title(link.text)]
        self.assertEqual(accepted, [monitor.Link(text=title, href="/recruit/view?UID=622")])

    def test_skips_closed_clickable_table_row(self) -> None:
        document = """
        <table><tr onclick="window.location='/recruit/view?UID=621'">
          <td>602</td><td>[산은비즈] 정규직 채용공고(시설)</td><td>2026-07-02</td><td>마감</td>
        </tr></table>
        """
        accepted = [link for link in monitor.extract_links(document) if monitor.is_job_title(link.text)]
        self.assertEqual(accepted, [])

    def test_skips_closed_link_inside_table_row(self) -> None:
        document = """
        <table><tr>
          <td><a href="/recruit/view/621">2026년 시설관리 정규직 채용공고</a></td>
          <td>2026-07-02</td><td>종료</td>
        </tr></table>
        """
        accepted = [link for link in monitor.extract_links(document) if monitor.is_job_title(link.text)]
        self.assertEqual(accepted, [])

    def test_extracts_only_open_selectin_clickable_row(self) -> None:
        document = """
        <table>
          <tr onclick="go_post('recruitDefault?comp_idx=22&amp;recruit_idx=981')">
            <td class="tit"><strong>2026년 시설관리 직원 채용 공고</strong></td>
            <td><span class="status">채용중</span></td>
          </tr>
          <tr onclick="go_post('recruitDefault?comp_idx=22&amp;recruit_idx=980')">
            <td class="tit"><strong>2026년 전기직 직원 채용 공고</strong></td>
            <td><span class="status">심사중</span></td>
          </tr>
        </table>
        """
        accepted = [link for link in monitor.extract_links(document) if monitor.is_job_title(link.text)]
        self.assertEqual(
            accepted,
            [
                monitor.Link(
                    text="2026년 시설관리 직원 채용 공고",
                    href="recruitDefault?comp_idx=22&recruit_idx=981",
                )
            ],
        )

    def test_uses_clean_board_title_attribute_and_filters_closed_row(self) -> None:
        document = """
        <table>
          <tr><td><a href="view.php?id=2" title="2026년 시설관리 직원 채용 - 게시물 보기">
            2026년 시설관리 직원 채용 <span class="row-mobile">관리자 2026-07-24</span>
          </a></td><td>접수중</td></tr>
          <tr><td><a href="view.php?id=1" title="2026년 전기직 직원 채용 - 게시물 보기">
            2026년 전기직 직원 채용 <span>관리자 2026-06-01</span>
          </a></td><td>마감</td></tr>
        </table>
        """
        accepted = [link for link in monitor.extract_links(document) if monitor.is_job_title(link.text)]
        self.assertEqual(
            accepted,
            [monitor.Link(text="2026년 시설관리 직원 채용", href="view.php?id=2")],
        )

    def test_deadline_filter_keeps_only_unexpired_table_rows(self) -> None:
        document = """
        <table>
          <tr><td><a href="/open">2026년 9차 시설직원 통합 채용 공고</a></td>
            <td>2026-07-23</td><td>2026-08-03</td></tr>
          <tr><td><a href="/closed">2026년 8차 시설직원 통합 채용 공고</a></td>
            <td>2026.6.23</td><td>2026.7.7</td></tr>
        </table>
        """
        accepted = [
            link
            for link in monitor.extract_links(
                document,
                filter_expired=True,
                today=datetime(2026, 7, 24).date(),
            )
            if monitor.is_job_title(link.text)
        ]
        self.assertEqual(accepted, [monitor.Link(text="2026년 9차 시설직원 통합 채용 공고", href="/open")])

    def test_active_window_filters_old_rows_without_a_deadline(self) -> None:
        document = """
        <table>
          <tr><td><a href="/recent">2026년 시설관리 직원 채용 공고</a></td>
            <td>2026-07-10</td></tr>
          <tr><td><a href="/old">2026년 전기직 직원 채용 공고</a></td>
            <td>2026-06-01</td></tr>
        </table>
        """
        accepted = [
            link
            for link in monitor.extract_links(
                document,
                active_window_days=30,
                today=datetime(2026, 7, 24).date(),
            )
            if monitor.is_job_title(link.text)
        ]
        self.assertEqual(accepted, [monitor.Link(text="2026년 시설관리 직원 채용 공고", href="/recent")])

    def test_decodes_cp949_pages(self) -> None:
        text = "2026년 시설관리 직원 채용 공고"
        self.assertEqual(monitor.decode_body(text.encode("cp949")), text)

    def test_cleans_visual_new_badges_and_private_use_icons_from_titles(self) -> None:
        self.assertEqual(
            monitor.clean_job_title("새글 2026년 시설관리 직원 채용 공고 \ue149"),
            "2026년 시설관리 직원 채용 공고",
        )
        self.assertEqual(
            monitor.clean_job_title("2026년 시설관리 직원 채용 공고 new"),
            "2026년 시설관리 직원 채용 공고",
        )

    def test_rejects_results_and_navigation(self) -> None:
        self.assertFalse(monitor.is_job_title("채용공고"))
        self.assertFalse(monitor.is_job_title("2026년 상반기 공무직 채용 최종합격자 발표"))
        self.assertFalse(monitor.is_job_title("2026년 직원채용 서류전형 결과"))
        self.assertFalse(monitor.is_job_title("임직원 사칭으로 인한 사기피해 예방 2026.03.27"))
        self.assertFalse(monitor.is_job_title("[공고 제2026-14호] 외부 심사위원 모집공고"))
        self.assertFalse(monitor.is_job_title("2026년 공개채용 사무직 면접심사 공고"))
        self.assertFalse(monitor.is_job_title("2026년 시설관리 직원 채용 접수마감"))
        self.assertFalse(monitor.is_job_title("2026년 직원전용 커뮤니티 채용 안내"))
        self.assertFalse(monitor.is_job_title("2026년 직원 채용 응시접수 결과(경쟁률) 공지"))

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

    def test_url_normalization_removes_rotating_jsession_id(self) -> None:
        first = "https://example.com/recruit/view.do;jsessionid=AAA123?nttId=42&page=1"
        second = "https://example.com/recruit/view.do;JSESSIONID=BBB999?nttId=42&page=9"
        self.assertEqual(monitor.normalize_url(first), "https://example.com/recruit/view.do?nttId=42")
        self.assertEqual(monitor.normalize_url(first), monitor.normalize_url(second))

    def test_url_normalization_preserves_only_hrdms_detail_hash_routes(self) -> None:
        detail = "https://poma.hrdms.kr/#/recruitment/detail/7"
        self.assertEqual(monitor.normalize_url(detail), detail)
        self.assertEqual(
            monitor.normalize_url("https://poma.hrdms.kr/#section"),
            "https://poma.hrdms.kr/",
        )
        self.assertEqual(
            monitor.normalize_url("https://example.com/#/recruitment/detail/7"),
            "https://example.com/",
        )

    def test_parses_korean_recruitment_period_deadlines(self) -> None:
        current = datetime(2026, 7, 24, 23, 0, tzinfo=monitor.KST)
        open_document = """
        <span>접수기간 </span><span>: 2026. 7. 23.(목) ~ 2026. 8. 3.(월)
        23</span><span>시 59</span><span>분까지</span>
        """
        expired_document = "<p>접수기간 : 2026. 07. 13. (월) ~ 2026. 07. 24. (금) 18시까지</p>"
        inherited_year = "<p>접수기간 : 2026. 6. 26.(금) ~ 7. 8.(수) 18시까지</p>"
        self.assertTrue(monitor.recruitment_period_is_open(open_document, current=current))
        self.assertFalse(monitor.recruitment_period_is_open(expired_document, current=current))
        self.assertFalse(monitor.recruitment_period_is_open(inherited_year, current=current))
        self.assertIsNone(monitor.recruitment_period_is_open("<p>접수 일정은 첨부파일 참조</p>", current=current))

    def test_job_links_are_not_followed_as_discovery_pages(self) -> None:
        link = monitor.Link(
            text="2026년 서울 시설관리 정규직 채용 공고",
            href="/board/view?bd_id=recruit&wr_id=42",
        )
        self.assertFalse(monitor.is_discovery_link(link, "https://example.com/"))

    def test_root_query_is_treated_as_a_direct_page_not_a_homepage(self) -> None:
        self.assertTrue(monitor.is_homepage_url("https://example.com/"))
        self.assertFalse(monitor.is_homepage_url("https://example.com/?contentId=recruit"))

    def test_score_prefers_metro_target_and_entry(self) -> None:
        score, tags = monitor.job_score("서울 CCTV 통합관제 신입 계약직 채용")
        self.assertEqual(score, 9)
        self.assertEqual(tags, ["수도권", "관심직무", "전환친화"])

    def test_fingerprint_is_stable_across_tracking_queries(self) -> None:
        a = monitor.fingerprint("x", "직원 채용 공고", "https://example.com/view?wr_id=1&mode=view&page=2")
        b = monitor.fingerprint("x", "직원  채용 공고", "https://example.com/view?mode=view&page=9&wr_id=1")
        self.assertEqual(a, b)


class MonitorConfigurationTests(unittest.TestCase):
    def test_load_sources_accepts_a_dynamic_contiguous_source_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, make_sources(30))
            self.assertEqual([source["priority"] for source in monitor.load_sources(path)], list(range(1, 31)))

    def test_bundled_sectigo_intermediate_has_expected_fingerprint(self) -> None:
        path = monitor.ROOT / "certificates" / "sectigo-rsa-domain-validation-secure-server-ca.pem"
        der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
        self.assertEqual(
            hashlib.sha256(der).hexdigest(),
            "7fa4ff68ec04a99d7528d5085f94907f4d1dd1c5381bacdc832ed5c960214676",
        )

    def test_bundled_globalsign_intermediate_has_expected_fingerprint(self) -> None:
        path = monitor.ROOT / "certificates" / "globalsign-gcc-r6-alphassl-ca-2025.pem"
        der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
        self.assertEqual(
            hashlib.sha256(der).hexdigest(),
            "a883559231f8388daf35ce41c8101040ae8fd9b656434247b9475af592cc08ca",
        )

    def test_bundled_sectigo_dv_r36_intermediate_has_expected_fingerprint(self) -> None:
        path = (
            monitor.ROOT
            / "certificates"
            / "sectigo-public-server-authentication-ca-dv-r36.pem"
        )
        der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
        self.assertEqual(
            hashlib.sha256(der).hexdigest(),
            "8c54c334b66ba4e426772af4a3f9136c19a1aec729fdb28c535c07a5a4ef22e0",
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

    def test_load_sources_accepts_recruiter_response_adapter(self) -> None:
        sources = make_sources()
        sources[0]["post_request"] = {
            "url": "https://source-1.example.com/app/jobnotice/list.json",
            "form": {"currentPage": "1"},
            "response_adapter": "recruiter_jobnotice",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["post_request"]["response_adapter"], "recruiter_jobnotice")

    def test_load_sources_accepts_applyin_document_adapter(self) -> None:
        sources = make_sources()
        sources[0]["document_adapter"] = "applyin_recruit_collection"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["document_adapter"], "applyin_recruit_collection")

    def test_load_sources_accepts_swr_document_adapter(self) -> None:
        sources = make_sources()
        sources[0]["document_adapter"] = "swr_job_board"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["document_adapter"], "swr_job_board")

    def test_load_sources_accepts_saramin_document_adapter(self) -> None:
        sources = make_sources()
        sources[0]["document_adapter"] = "saramin_current_company"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["document_adapter"], "saramin_current_company")

    def test_load_sources_accepts_jobkorea_document_adapter(self) -> None:
        sources = make_sources()
        sources[0]["document_adapter"] = "jobkorea_current_company"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["document_adapter"], "jobkorea_current_company")

    def test_load_sources_accepts_hrdms_document_adapter(self) -> None:
        sources = make_sources()
        sources[0]["document_adapter"] = "hrdms_recruitment_list"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["document_adapter"], "hrdms_recruitment_list")

    def test_load_sources_accepts_deadline_filters(self) -> None:
        sources = make_sources()
        sources[0]["deadline_filter"] = "last_date"
        sources[1]["detail_deadline_filter"] = "korean_recruitment_period"
        sources[2]["active_window_days"] = 30
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sources.json"
            monitor.write_json(path, sources)
            loaded = monitor.load_sources(path)
        self.assertEqual(loaded[0]["deadline_filter"], "last_date")
        self.assertEqual(loaded[1]["detail_deadline_filter"], "korean_recruitment_period")
        self.assertEqual(loaded[2]["active_window_days"], 30)

    def test_load_sources_rejects_invalid_metadata_priority_and_url(self) -> None:
        cases: list[tuple[str, list[monitor.SourceConfig]]] = []
        cases.append(("empty", []))

        gapped_priority = make_sources()
        gapped_priority[-1]["priority"] = 21
        cases.append(("gapped priority", gapped_priority))

        duplicate = make_sources()
        duplicate[1]["id"] = duplicate[0]["id"]
        cases.append(("duplicate", duplicate))

        invalid_organization = make_sources()
        invalid_organization[0]["organization_type"] = "private"  # type: ignore[typeddict-item]
        cases.append(("invalid organization type", invalid_organization))

        invalid_fit = make_sources()
        invalid_fit[0]["job_fit"] = "maybe"  # type: ignore[typeddict-item]
        cases.append(("invalid job fit", invalid_fit))

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

        invalid_adapter = make_sources()
        invalid_adapter[0]["post_request"] = {
            "url": "https://source-1.example.com/data",
            "form": {"currentPage": "1"},
            "response_adapter": "unknown",  # type: ignore[typeddict-item]
        }
        cases.append(("invalid response adapter", invalid_adapter))

        invalid_document_adapter = make_sources()
        invalid_document_adapter[0]["document_adapter"] = "unknown"  # type: ignore[typeddict-item]
        cases.append(("invalid document adapter", invalid_document_adapter))

        invalid_deadline_filter = make_sources()
        invalid_deadline_filter[0]["deadline_filter"] = "first_date"  # type: ignore[typeddict-item]
        cases.append(("invalid deadline filter", invalid_deadline_filter))

        invalid_detail_deadline_filter = make_sources()
        invalid_detail_deadline_filter[0]["detail_deadline_filter"] = "guess"  # type: ignore[typeddict-item]
        cases.append(("invalid detail deadline filter", invalid_detail_deadline_filter))

        invalid_active_window = make_sources()
        invalid_active_window[0]["active_window_days"] = 0
        cases.append(("invalid active window", invalid_active_window))

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

    def test_fetch_adds_xhr_header_only_when_requested(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"result"
        response.headers.get_content_charset.return_value = "utf-8"
        with mock.patch("monitor.urlopen", return_value=response) as opener:
            monitor.fetch("https://example.com/data", retries=0, xhr=True)

        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("X-requested-with"), "XMLHttpRequest")

    def test_collect_source_uses_extra_ca_only_for_configured_source(self) -> None:
        source = make_sources()[0]
        source["tls_ca_file"] = "certificates/sectigo-rsa-domain-validation-secure-server-ca.pem"
        with mock.patch("monitor.fetch", return_value="<html><body>채용정보</body></html>") as fetcher:
            monitor.collect_source(source)

        fetcher.assert_called_once_with(
            source["urls"][0],
            extra_ca_file=monitor.ROOT / source["tls_ca_file"],
        )

    def test_collect_source_filters_certainly_expired_detail_deadline(self) -> None:
        source = make_sources(1)[0]
        source["detail_deadline_filter"] = "korean_recruitment_period"
        listing = """
        <p>채용공고</p>
        <a href="/jobs/open">2099년 시설관리 직원 채용 공고</a>
        <a href="/jobs/closed">2020년 전기직 직원 채용 공고</a>
        """
        responses = {
            source["urls"][0]: listing,
            f"{source['home']}jobs/open": "<p>접수기간: 2099. 1. 1. ~ 2099. 12. 31.</p>",
            f"{source['home']}jobs/closed": "<p>접수기간: 2020. 1. 1. ~ 2020. 1. 31.</p>",
        }
        with mock.patch("monitor.fetch", side_effect=lambda url, **_: responses[url]):
            result = monitor.collect_source(source)

        self.assertEqual(result.fetched_pages, 3)
        self.assertEqual(result.errors, [])
        self.assertEqual([job["title"] for job in result.jobs], ["2099년 시설관리 직원 채용 공고"])

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

    def test_collect_source_adapts_recruiter_json_to_job_link(self) -> None:
        source = make_sources()[0]
        source["urls"] = ["https://source-1.example.com/app/jobnotice/list"]
        source["post_request"] = {
            "url": "https://source-1.example.com/app/jobnotice/list.json",
            "form": {"currentPage": "1"},
            "response_adapter": "recruiter_jobnotice",
        }
        response = json.dumps(
            {
                "list": [
                    {
                        "jobnoticeName": "코레일네트웍스 2026년 하반기 공개채용",
                        "jobnoticeSn": 42,
                        "systemKindCode": "MRS2",
                    }
                ]
            }
        )
        with mock.patch("monitor.fetch", return_value=response):
            result = monitor.collect_source(source)

        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["title"], "코레일네트웍스 2026년 하반기 공개채용")
        self.assertEqual(
            result.jobs[0]["url"],
            "https://source-1.example.com/app/jobnotice/view?jobnoticeSn=42&systemKindCode=MRS2",
        )
        self.assertTrue(result.has_recruitment_marker)

    def test_recruiter_adapter_rejects_malformed_response(self) -> None:
        with self.assertRaisesRegex(monitor.JobRadarError, "invalid JSON"):
            monitor.adapt_post_response("not-json", "recruiter_jobnotice", "https://example.com/jobs")

    def test_recruiter_adapter_skips_closed_job(self) -> None:
        response = json.dumps(
            {
                "list": [
                    {
                        "jobnoticeName": "코레일네트웍스 2026년 상반기 수시채용",
                        "jobnoticeSn": 42,
                        "systemKindCode": "MRS2",
                        "receiptState": "접수마감",
                    }
                ]
            }
        )
        document = monitor.adapt_post_response(
            response,
            "recruiter_jobnotice",
            "https://example.com/app/jobnotice/list",
        )
        self.assertEqual(monitor.extract_links(document), [])
        self.assertIn("채용공고", document)

    def test_collect_source_adapts_applyin_embedded_json_and_skips_closed_job(self) -> None:
        source = make_sources()[0]
        source["urls"] = ["https://source-1.example.com/built-in/jobs"]
        source["document_adapter"] = "applyin_recruit_collection"
        payload = {
            "data": [
                {
                    "id": 42,
                    "title": "2026년 현장직 영천경마장 수시채용공고",
                    "status": {"code": "ing", "text": "접수중"},
                    "links": {"jobs.show": "https://source-1.example.com/jobs/42?v=1.2.0"},
                },
                {
                    "id": 41,
                    "title": "2026년 서울 현장직 정기채용공고",
                    "status": {"code": "close", "text": "종료"},
                    "links": {"jobs.show": "https://source-1.example.com/jobs/41?v=1.2.0"},
                },
            ]
        }
        response = (
            '<script type="application/json" id="recruit-collection">'
            + json.dumps(payload, ensure_ascii=False)
            + "</script>"
        )
        with mock.patch("monitor.fetch", return_value=response):
            result = monitor.collect_source(source)

        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["title"], "2026년 현장직 영천경마장 수시채용공고")
        self.assertEqual(result.jobs[0]["url"], "https://source-1.example.com/jobs/42?v=1.2.0")
        self.assertTrue(result.has_recruitment_marker)

    def test_applyin_adapter_rejects_missing_embedded_json(self) -> None:
        with self.assertRaisesRegex(monitor.JobRadarError, "missing its embedded recruitment data"):
            monitor.adapt_document_response(
                "<html><p>채용공고</p></html>",
                "applyin_recruit_collection",
                "https://example.com/built-in/jobs",
            )

    def test_saramin_adapter_keeps_each_current_job_once_and_skips_closed_jobs(self) -> None:
        document = """
        <section class="section_company_info section_recruit_ing">
          <h2>진행중 공고 <span class="count">1</span></h2>
          <div id="list_54509675" class="recruit_container list_link recruit">
            <a href="/job-search/view?rec_idx=54509675&amp;t_content=generic" class="link">
              <div class="list">
                <p class="tit">한국산업단지공단 자회사 현장직 채용 공고(영선원)</p>
                <div class="meta"><span>경남 창원시</span><span>D-2</span></div>
              </div>
            </a>
            <a href="/job-search/view?rec_idx=54509675" title="홈페이지 지원">지원</a>
          </div>
        </section>
        <section class="section_company_info section_recruit_cloased">
          <div id="list_54403327" class="recruit_container list_link recruit">
            <a href="/job-search/view?rec_idx=54403327" class="link" style="pointer-events: none">
              <div class="list">
                <p class="tit">㈜키콕스파트너스 대표이사 모집 공고</p>
                <span class="date">마감</span>
              </div>
            </a>
          </div>
        </section>
        """
        adapted = monitor.adapt_document_response(
            document,
            "saramin_current_company",
            "https://m.saramin.co.kr/job-search/company-info-view/recruit?csn=company",
        )
        self.assertEqual(
            monitor.extract_links(adapted),
            [
                monitor.Link(
                    text="한국산업단지공단 자회사 현장직 채용 공고(영선원)",
                    href="https://m.saramin.co.kr/job-search/view?rec_idx=54509675",
                )
            ],
        )

    def test_saramin_adapter_rejects_a_page_without_the_current_section(self) -> None:
        with self.assertRaisesRegex(monitor.JobRadarError, "current-recruitment section"):
            monitor.adapt_document_response(
                "<html><p>일시적인 안내 페이지</p></html>",
                "saramin_current_company",
                "https://m.saramin.co.kr/job-search/company-info-view/recruit?csn=company",
            )

    def test_jobkorea_adapter_keeps_current_jobs_once_and_canonicalizes_links(self) -> None:
        document = """
        <div class="recruitList listBx">
          <ul class="listItem">
            <li>
              <a href="/Recruit/GI_Read/49614372?PageGbn=MST&amp;sc=226">
                <span class="current"><span class="date">D-25</span></span>
                <span class="tit">한국국토정보공사 영덕지사 환경관리 계약직 모집/한국국토정보공사 자회사</span>
                <span class="desc">경북 영덕군</span>
              </a>
            </li>
            <li>
              <a href="/Recruit/GI_Read/49612597?PageGbn=MST">
                <span class="current">상시채용</span>
                <span class="tit">한국국토정보공사 거창지사 환경관리 정규직 모집/한국국토정보공사 자회사</span>
              </a>
            </li>
            <li>
              <a href="/Recruit/GI_Read/49612597?sc=another">
                <span class="tit">한국국토정보공사 거창지사 환경관리 정규직 모집/한국국토정보공사 자회사</span>
              </a>
            </li>
          </ul>
        </div>
        """
        adapted = monitor.adapt_document_response(
            document,
            "jobkorea_current_company",
            "https://m.jobkorea.co.kr/company/45814640/Recruit?Disp_Type=1",
        )
        self.assertEqual(
            monitor.extract_links(adapted),
            [
                monitor.Link(
                    text=(
                        "한국국토정보공사 영덕지사 환경관리 계약직 모집/"
                        "한국국토정보공사 자회사"
                    ),
                    href="https://m.jobkorea.co.kr/Recruit/GI_Read/49614372",
                ),
                monitor.Link(
                    text=(
                        "한국국토정보공사 거창지사 환경관리 정규직 모집/"
                        "한국국토정보공사 자회사"
                    ),
                    href="https://m.jobkorea.co.kr/Recruit/GI_Read/49612597",
                ),
            ],
        )

    def test_jobkorea_adapter_accepts_an_empty_current_list(self) -> None:
        adapted = monitor.adapt_document_response(
            '<div class="recruitList listBx"><p>진행 중인 채용공고가 없습니다.</p></div>',
            "jobkorea_current_company",
            "https://m.jobkorea.co.kr/company/45814640/Recruit?Disp_Type=1",
        )
        self.assertEqual(monitor.extract_links(adapted), [])
        self.assertIn("채용공고", adapted)

    def test_jobkorea_adapter_rejects_missing_or_malformed_current_list(self) -> None:
        cases = [
            "<html><p>일시적인 안내 페이지</p></html>",
            (
                '<div class="recruitList"><ul><li>'
                '<a href="/Recruit/GI_Read/not-number"><span class="tit">'
                "LX파트너스 시설관리 직원 채용</span></a></li></ul></div>"
            ),
            (
                '<div class="recruitList"><ul><li>'
                '<a href="//evil.example/Recruit/GI_Read/42"><span class="tit">'
                "LX파트너스 시설관리 직원 채용</span></a></li></ul></div>"
            ),
            (
                '<div class="recruitList"><ul><li>'
                '<a href="/Recruit/GI_Read/42">지원하기</a></li></ul></div>'
            ),
        ]
        for document in cases:
            with self.subTest(document=document), self.assertRaises(monitor.JobRadarError):
                monitor.adapt_document_response(
                    document,
                    "jobkorea_current_company",
                    "https://m.jobkorea.co.kr/company/45814640/Recruit?Disp_Type=1",
                )

    def test_hrdms_adapter_keeps_only_public_open_recruitments(self) -> None:
        payload = {
            "code": 200,
            "data": [
                {
                    "id": "7",
                    "title": "우체국시설관리단 현장직원 9차 통합 채용 공고",
                    "recruit_type": "R0",
                    "deadline_dt_time": "2099-08-03 18:00:00",
                    "progress_yn": "Y",
                    "state": "접수중",
                },
                {
                    "id": "6",
                    "title": "우체국시설관리단 현장직원 8차 통합 채용 공고",
                    "recruit_type": "R0",
                    "deadline_dt_time": "2026-07-01 18:00:00",
                    "progress_yn": "Y",
                    "state": "접수마감",
                },
                {
                    "id": "5",
                    "title": "우체국시설관리단 비공개 직원 채용 공고",
                    "recruit_type": "R2",
                    "deadline_dt_time": "2099-09-01 18:00:00",
                    "progress_yn": "Y",
                    "state": "접수중",
                },
                {
                    "id": "4",
                    "title": "우체국시설관리단 게시중지 직원 채용 공고",
                    "recruit_type": "R1",
                    "deadline_dt_time": "2099-09-01 18:00:00",
                    "progress_yn": "N",
                    "state": "접수중",
                },
            ],
        }
        adapted = monitor.adapt_document_response(
            json.dumps(payload, ensure_ascii=False),
            "hrdms_recruitment_list",
            "https://poma-manage.hrdms.kr/api/front/recruitment/list",
        )
        self.assertEqual(
            monitor.extract_links(
                adapted,
                filter_expired=True,
                today=datetime(2026, 7, 24).date(),
            ),
            [
                monitor.Link(
                    text="우체국시설관리단 현장직원 9차 통합 채용 공고",
                    href="https://poma.hrdms.kr/#/recruitment/detail/7",
                )
            ],
        )

    def test_hrdms_adapter_rejects_malformed_responses(self) -> None:
        valid_item = {
            "id": "7",
            "title": "우체국시설관리단 현장직원 9차 통합 채용 공고",
            "recruit_type": "R0",
            "deadline_dt_time": "2099-08-03 18:00:00",
            "progress_yn": "Y",
            "state": "접수중",
        }
        documents = [
            "not-json",
            json.dumps({"code": 500, "data": []}),
            json.dumps({"code": 200, "data": [{**valid_item, "id": "not-an-id"}]}),
            json.dumps({"code": 200, "data": [{**valid_item, "state": "알 수 없음"}]}),
            json.dumps({"code": 200, "data": [{**valid_item, "deadline_dt_time": "soon"}]}),
        ]
        for document in documents:
            with self.subTest(document=document), self.assertRaises(monitor.JobRadarError):
                monitor.adapt_document_response(
                    document,
                    "hrdms_recruitment_list",
                    "https://poma-manage.hrdms.kr/api/front/recruitment/list",
                )

    def test_collect_source_uses_hrdms_xhr_and_preserves_detail_hash_route(self) -> None:
        source = make_sources(1)[0]
        source["home"] = "https://poma.hrdms.kr/#/recruitment/list"
        source["urls"] = ["https://poma-manage.hrdms.kr/api/front/recruitment/list"]
        source["deadline_filter"] = "last_date"
        source["document_adapter"] = "hrdms_recruitment_list"
        response = json.dumps(
            {
                "code": 200,
                "data": [
                    {
                        "id": "7",
                        "title": "우체국시설관리단 현장직원 9차 통합 채용 공고",
                        "recruit_type": "R0",
                        "deadline_dt_time": "2099-08-03 18:00:00",
                        "progress_yn": "Y",
                        "state": "접수중",
                    }
                ],
            },
            ensure_ascii=False,
        )
        with mock.patch("monitor.fetch", return_value=response) as fetcher:
            result = monitor.collect_source(source)

        fetcher.assert_called_once_with(source["urls"][0], extra_ca_file=None, xhr=True)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(
            result.jobs[0]["url"],
            "https://poma.hrdms.kr/#/recruitment/detail/7",
        )

    def test_swr_adapter_rewrites_javascript_board_link(self) -> None:
        document = """
        <a href="javascript:goBoardView('5713')" title="2026년 하반기 기능인재 채용공고">
          2026년 하반기 기능인재 채용공고
        </a>
        """
        adapted = monitor.adapt_document_response(
            document,
            "swr_job_board",
            "https://swr.or.kr/cpage/board/job.do?menu_cd=C0003&srch_input1=00002",
        )
        self.assertEqual(
            monitor.extract_links(adapted),
            [
                monitor.Link(
                    text="2026년 하반기 기능인재 채용공고",
                    href=(
                        "https://swr.or.kr/cpage/board/job/view.do"
                        "?board_gb=job&board_seq=5713&menu_cd=C0003"
                    ),
                )
            ],
        )

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
                "source_id": "legacy-source-id",
                "company": "예전 회사명",
                "priority": 99,
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
        self.assertEqual(records[0]["source_id"], source["id"])
        self.assertEqual(records[0]["company"], source["name"])
        self.assertEqual(records[0]["priority"], source["priority"])

    def test_notification_filter_keeps_low_fit_jobs_dashboard_only(self) -> None:
        sources = make_sources(3)
        sources[1]["job_fit"] = "adjacent"
        sources[2]["job_fit"] = "low"
        jobs = [make_job(source) for source in sources]

        filtered = monitor.notification_jobs(jobs, sources)

        self.assertEqual([job["source_id"] for job in filtered], ["source-1", "source-2"])

    def test_notification_filter_suppresses_explicitly_unsuitable_title(self) -> None:
        sources = make_sources(1)
        for title in (
            "2026년 체험형 청년인턴 채용 공고",
            "2026년 미래내일 일경험(인턴형) 채용계획 공고",
            "2026년 생활체육 프로그램 시간강사 채용 공고",
        ):
            with self.subTest(title=title):
                job = make_job(sources[0])
                job["title"] = title
                self.assertEqual(monitor.notification_jobs([job], sources), [])

    def test_public_payload_keeps_all_active_jobs_before_inactive_history(self) -> None:
        source = make_sources(1)[0]
        state = monitor.default_state()
        bucket: dict[str, monitor.JobRecord] = {}
        for index in range(1_100):
            job = make_job(source, str(index))
            job.update(
                {
                    "first_seen": f"2026-07-20T00:{index % 60:02d}:00Z",
                    "last_seen": "2026-07-20T00:00:00Z",
                    "baseline": True,
                    "active": index < 900,
                }
            )
            bucket[job["id"]] = job
        state["known"] = {source["id"]: bucket}

        payload = monitor.public_payload(state, [source], "2026-07-21T00:00:00Z", 0)

        self.assertEqual(payload["stats"]["total"], 1_100)
        self.assertEqual(payload["stats"]["active_total"], 900)
        self.assertEqual(len(payload["jobs"]), monitor.PUBLIC_JOB_LIMIT)
        self.assertEqual(sum(job["active"] for job in payload["jobs"]), 900)
        self.assertEqual(payload["sources"][0]["organization_type"], "public_subsidiary")
        self.assertEqual(payload["sources"][0]["job_fit"], "core")

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

    def test_newly_added_source_builds_its_own_quiet_baseline(self) -> None:
        with (
            mock.patch("monitor.collect_source", side_effect=lambda source: result_for(source, "current")),
            mock.patch("monitor.telegram_send", return_value="skipped"),
        ):
            self.assertEqual(self.run_monitor(), 0)

        self.sources = make_sources(21)
        monitor.write_json(self.sources_path, self.sources)

        def after_expansion(source: monitor.SourceConfig) -> monitor.CollectionResult:
            if source["priority"] == 1:
                return result_for(source, "current", "new")
            if source["priority"] == 21:
                return result_for(source, "existing-a", "existing-b")
            return result_for(source, "current")

        with (
            mock.patch("monitor.collect_source", side_effect=after_expansion),
            mock.patch("monitor.telegram_send", return_value="sent") as notifier,
        ):
            self.assertEqual(self.run_monitor(), 0)

        notified = notifier.call_args.args[0]
        self.assertEqual([job["id"] for job in notified], [make_job(self.sources[0], "new")["id"]])
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        added_jobs = state["known"]["source-21"].values()
        self.assertTrue(all(job["baseline"] for job in added_jobs))
        payload = json.loads(self.public_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["stats"]["source_count"], 21)

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
        self.assertEqual(payload["stats"]["healthy_sources"], len(self.sources) - 1)
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
