#!/usr/bin/env python3
"""Daily official-career-page monitor for JobRadar.

Only Python's standard library is used so the daily Action has no dependency
installation step and remains cheap and predictable.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NotRequired, TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "config" / "sources.json"
STATE_FILE = ROOT / "data" / "state.json"
PUBLIC_FILE = ROOT / "public" / "data" / "jobs.json"
EXPECTED_SOURCE_COUNT = 20
STATE_SCHEMA_VERSION = 2

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134 Safari/537.36 "
    "JobRadar/1.0 (+GitHub Actions; public recruitment monitor)"
)

JOB_WORDS = (
    "채용",
    "모집",
    "공개채용",
    "직원",
    "사원",
    "공무직",
    "기간제",
    "계약직",
    "정규직",
    "인턴",
    "현장직",
)

OUTCOME_WORDS = (
    "합격",
    "합격자",
    "합격 발표",
    "합격발표",
    "서류전형",
    "면접전형",
    "면접심사",
    "서류심사",
    "필기시험",
    "필기전형",
    "인성검사",
    "평가전형",
    "응시현황",
    "전형 결과",
    "전형결과",
    "친인척",
    "채용비리",
    "임원 초빙",
    "임원 모집",
    "대표이사 공모",
    "비상임",
    "상임이사",
    "상임감사",
    "감사실장",
    "직원의소리",
    "직원고충",
    "직원 업무",
    "임직원 행동강령",
    "임직원 전용",
    "임직원 채용정보",
    "종합건강검진",
    "입문교육",
    "모집일",
    "필수조건 변경",
    "필수 조건 도입",
    "외부 심사위원",
    "심사위원 모집",
    "임직원 사칭",
    "첨부파일",
    "다운로드",
)

GENERIC_LABELS = {
    "채용",
    "채용공고",
    "채용정보",
    "채용안내",
    "인재채용",
    "인재경영",
    "recruit",
    "recruitment",
    "careers",
    "지원하기",
    "자세히보기",
    "더보기",
    "목록",
}

DISCOVERY_WORDS = ("채용공고", "채용정보", "인재채용", "recruit", "career", "employment", "job")

METRO_WORDS = (
    "서울",
    "경기",
    "인천",
    "수도권",
    "김포",
    "과천",
    "안양",
    "부천",
    "시흥",
    "성남",
    "고양",
    "용인",
    "수원",
    "화성",
    "파주",
    "남양주",
    "의정부",
    "광명",
    "하남",
    "영종",
)

TARGET_WORDS = (
    "시설",
    "영선",
    "통신",
    "전산",
    "it",
    "관제",
    "cctv",
    "보안",
    "경비",
    "미화",
    "환경",
    "안내",
    "주차",
    "역무",
    "운영",
    "고객센터",
    "방재",
    "소방",
)

ENTRY_WORDS = ("경력무관", "신입", "학력무관", "공무직", "기간제", "계약직", "인턴")

DROP_QUERY_KEYS = {
    "page",
    "pageindex",
    "offset",
    "category",
    "sca",
    "sfl",
    "sod",
    "sop",
    "sst",
    "stx",
    "sk",
    "sw",
    "pg",
    "cp",
    "txt",
    "cate",
    "page_num",
    "stype",
    "svalue",
    "sphoto",
    "searchtext",
    "searchselect",
    "sort",
    "rowsort",
    "itm",
    "main",
    "utm_source",
    "utm_medium",
    "utm_campaign",
}


class JobRadarError(RuntimeError):
    """Base exception for an actionable JobRadar failure."""


class ConfigurationError(JobRadarError):
    """Raised when committed configuration or state is invalid."""


class PostRequestConfig(TypedDict):
    url: str
    form: dict[str, str]


class SourceConfig(TypedDict):
    id: str
    name: str
    priority: int
    home: str
    urls: list[str]
    tls_ca_file: NotRequired[str]
    post_request: NotRequired[PostRequestConfig]


class JobRecord(TypedDict, total=False):
    id: str
    source_id: str
    company: str
    priority: int
    title: str
    url: str
    score: int
    tags: list[str]
    first_seen: str
    last_seen: str
    baseline: bool
    active: bool


@dataclass(frozen=True)
class CollectionResult:
    jobs: list[JobRecord]
    errors: list[str]
    fetched_pages: int
    has_recruitment_marker: bool


@dataclass(frozen=True)
class Link:
    text: str
    href: str


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        values = {key.lower(): value or "" for key, value in attrs}
        self._href = values.get("href") or values.get("data-href") or values.get("data-url") or ""
        self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = clean_text(" ".join(self._parts))
        if text:
            self.links.append(Link(text=text, href=self._href.strip()))
        self._href = None
        self._parts = []


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\u200b", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip(" \t\r\n-|•")


def decode_body(raw: bytes, declared: str | None = None) -> str:
    for encoding in (declared, "utf-8", "cp949", "euc-kr"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("utf-8", errors="replace")


def fetch(
    url: str,
    timeout: int = 25,
    retries: int = 2,
    *,
    extra_ca_file: Path | None = None,
    form_data: dict[str, str] | None = None,
    referer: str | None = None,
) -> str:
    last_error: Exception | None = None
    context = ssl.create_default_context()
    if extra_ca_file is not None:
        context.load_verify_locations(cafile=str(extra_ca_file))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.5",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.5",
    }
    body: bytes | None = None
    if form_data is not None:
        body = urlencode(form_data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
    if referer is not None:
        headers["Referer"] = referer
    for attempt in range(retries + 1):
        try:
            request = Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
            with urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read(4_000_000)
                return decode_body(raw, response.headers.get_content_charset())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{type(last_error).__name__}: {last_error}")


def extract_links(document: str) -> list[Link]:
    parser = LinkExtractor()
    parser.feed(document)
    parser.close()
    return parser.links


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    query = sorted(
        (
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in DROP_QUERY_KEYS
        ),
        key=lambda item: (item[0].lower(), item[1]),
    )
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(query), ""))


def same_site(left: str, right: str) -> bool:
    a = urlsplit(left).netloc.lower().removeprefix("www.")
    b = urlsplit(right).netloc.lower().removeprefix("www.")
    return bool(a and b and (a == b or a.endswith("." + b) or b.endswith("." + a)))


def is_discovery_link(link: Link, base_url: str) -> bool:
    combined = f"{link.text} {link.href}".lower()
    if not any(word in combined for word in DISCOVERY_WORDS):
        return False
    if is_job_title(link.text):
        return False
    target = urljoin(base_url, link.href)
    return target.startswith(("http://", "https://")) and same_site(base_url, target)


def is_job_title(title: str) -> bool:
    compact = re.sub(r"\s+", "", title).lower()
    if not title or len(compact) < 5 or len(compact) > 200 or compact in GENERIC_LABELS:
        return False
    lowered = title.lower()
    if any(word.lower() in lowered for word in OUTCOME_WORDS):
        return False
    # "직원" inside "임직원" is usually a notice addressed to employees,
    # not a recruitment signal.
    recruitment_text = lowered.replace("임직원", "")
    if not any(word.lower() in recruitment_text for word in JOB_WORDS):
        return False
    signals = sum(
        bool(re.search(pattern, lowered))
        for pattern in (
            r"20\d{2}",
            r"\d+차",
            r"신입|경력|직원|사원|근로자|인턴",
            r"정규|기간제|계약|공무직|현장",
            r"서울|경기|인천|공항|철도|본사|지사|사업소|센터",
        )
    )
    return signals >= 1 and len(compact) >= 7


def job_score(title: str) -> tuple[int, list[str]]:
    lowered = title.lower()
    score = 0
    tags: list[str] = []
    if any(word.lower() in lowered for word in METRO_WORDS):
        score += 4
        tags.append("수도권")
    if any(word.lower() in lowered for word in TARGET_WORDS):
        score += 3
        tags.append("관심직무")
    if any(word.lower() in lowered for word in ENTRY_WORDS):
        score += 2
        tags.append("전환친화")
    if "진행중" in lowered or "모집중" in lowered:
        score += 1
        tags.append("진행중")
    return score, tags


def fingerprint(source_id: str, title: str, url: str) -> str:
    normalized_title = re.sub(r"\s+", "", title).lower()
    payload = f"{source_id}\n{normalized_title}\n{normalize_url(url)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def collect_source(source: SourceConfig) -> CollectionResult:
    pages: list[tuple[str, str]] = []
    errors: list[str] = []
    visited: set[str] = set()
    extra_ca_file = ROOT / source["tls_ca_file"] if "tls_ca_file" in source else None
    post_request = source.get("post_request")

    for configured_url in source["urls"]:
        try:
            if post_request is None:
                document = fetch(configured_url, extra_ca_file=extra_ca_file)
            else:
                document = fetch(
                    post_request["url"],
                    extra_ca_file=extra_ca_file,
                    form_data=post_request["form"],
                    referer=configured_url,
                )
            pages.append((configured_url, document))
            visited.add(normalize_url(configured_url))
        except RuntimeError as exc:
            errors.append(f"{configured_url}: {exc}")

    # Homepages are allowed in the config. Follow a few same-site recruitment
    # links so a redesign of the navigation is less likely to break monitoring.
    discovered: list[str] = []
    for page_url, document in pages:
        for link in extract_links(document):
            if not is_discovery_link(link, page_url):
                continue
            target = normalize_url(urljoin(page_url, link.href))
            if target not in visited and target not in discovered:
                discovered.append(target)
            if len(discovered) >= 4:
                break
        if len(discovered) >= 4:
            break

    for target in discovered:
        try:
            pages.append((target, fetch(target, extra_ca_file=extra_ca_file)))
            visited.add(target)
        except RuntimeError as exc:
            errors.append(f"{target}: {exc}")

    candidates: dict[str, JobRecord] = {}
    for page_url, document in pages:
        for link in extract_links(document):
            title = clean_text(link.text)
            if not is_job_title(title):
                continue
            raw_href = link.href
            if raw_href.lower().startswith(("javascript:", "#", "mailto:")) or not raw_href:
                job_url = page_url
            else:
                job_url = urljoin(page_url, raw_href)
            if not job_url.startswith(("http://", "https://")):
                job_url = page_url
            job_url = normalize_url(job_url)
            score, tags = job_score(title)
            key = fingerprint(source["id"], title, job_url)
            candidates[key] = {
                "id": key,
                "source_id": source["id"],
                "company": source["name"],
                "priority": source["priority"],
                "title": title[:300],
                "url": job_url,
                "score": score,
                "tags": tags,
            }

    marker_words = tuple(word.lower() for word in (*DISCOVERY_WORDS, *JOB_WORDS))
    has_recruitment_marker = any(any(marker in document.lower() for marker in marker_words) for _, document in pages)
    return CollectionResult(
        jobs=list(candidates.values()),
        errors=errors,
        fetched_pages=len(pages),
        has_recruitment_marker=has_recruitment_marker,
    )


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in {display_path(path)} at line {exc.lineno}, column {exc.colno}"
        ) from exc


def validate_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{field} must be a non-empty URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(f"{field} must use http or https: {value!r}")
    return value


def validate_tls_ca_file(value: Any, source_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{source_id}.tls_ca_file must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ConfigurationError(f"{source_id}.tls_ca_file must stay inside the repository")
    resolved = (ROOT / relative).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise ConfigurationError(f"{source_id}.tls_ca_file must stay inside the repository") from exc
    if not resolved.is_file():
        raise ConfigurationError(f"{source_id}.tls_ca_file does not exist: {relative.as_posix()}")
    try:
        context = ssl.create_default_context()
        context.load_verify_locations(cafile=str(resolved))
    except (OSError, ssl.SSLError) as exc:
        raise ConfigurationError(f"{source_id}.tls_ca_file is not a valid CA certificate") from exc
    return relative.as_posix()


def validate_post_request(value: Any, source_id: str, source_urls: list[str]) -> PostRequestConfig:
    if not isinstance(value, dict) or set(value) != {"url", "form"}:
        raise ConfigurationError(f"{source_id}.post_request must contain only url and form")
    if len(source_urls) != 1:
        raise ConfigurationError(f"{source_id}.post_request requires exactly one display URL")
    request_url = validate_url(value["url"], f"{source_id}.post_request.url")
    if not same_site(request_url, source_urls[0]):
        raise ConfigurationError(f"{source_id}.post_request.url must use the same site as its display URL")
    form = value["form"]
    if not isinstance(form, dict) or not form:
        raise ConfigurationError(f"{source_id}.post_request.form must be a non-empty object")
    if not all(isinstance(key, str) and key and isinstance(item, str) for key, item in form.items()):
        raise ConfigurationError(f"{source_id}.post_request.form must map non-empty strings to strings")
    return {"url": request_url, "form": dict(form)}


def load_sources(path: Path | None = None) -> list[SourceConfig]:
    path = path or SOURCES_FILE
    raw = load_json(path, [])
    if not isinstance(raw, list):
        raise ConfigurationError("config/sources.json must contain a JSON array")
    if len(raw) != EXPECTED_SOURCE_COUNT:
        raise ConfigurationError(f"Expected {EXPECTED_SOURCE_COUNT} monitored sources, found {len(raw)}")

    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    seen_priorities: set[int] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigurationError(f"Source #{index + 1} must be an object")
        source_id = item.get("id")
        name = item.get("name")
        priority = item.get("priority")
        urls = item.get("urls")
        if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", source_id):
            raise ConfigurationError(f"Source #{index + 1} has an invalid id")
        if source_id in seen_ids:
            raise ConfigurationError(f"Duplicate source id: {source_id}")
        if not isinstance(name, str) or not name.strip():
            raise ConfigurationError(f"Source {source_id} has an invalid name")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 1:
            raise ConfigurationError(f"Source {source_id} has an invalid priority")
        if priority in seen_priorities:
            raise ConfigurationError(f"Duplicate source priority: {priority}")
        if not isinstance(urls, list) or not urls:
            raise ConfigurationError(f"Source {source_id} must define at least one URL")
        validated_urls = [validate_url(url, f"{source_id}.urls") for url in urls]
        source: SourceConfig = {
            "id": source_id,
            "name": name.strip(),
            "priority": priority,
            "home": validate_url(item.get("home"), f"{source_id}.home"),
            "urls": validated_urls,
        }
        if "tls_ca_file" in item:
            if any(urlsplit(url).scheme != "https" for url in validated_urls):
                raise ConfigurationError(f"{source_id}.tls_ca_file can only be used with HTTPS URLs")
            source["tls_ca_file"] = validate_tls_ca_file(item["tls_ca_file"], source_id)
        if "post_request" in item:
            source["post_request"] = validate_post_request(item["post_request"], source_id, validated_urls)
        sources.append(source)
        seen_ids.add(source_id)
        seen_priorities.add(priority)

    if seen_priorities != set(range(1, EXPECTED_SOURCE_COUNT + 1)):
        raise ConfigurationError("Source priorities must be exactly 1 through 20")
    return sorted(sources, key=lambda source: source["priority"])


def default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "initialized_at": None,
        "known": {},
        "source_status": {},
    }


def migrate_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Upgrade the only pre-release state shape without losing fingerprints."""
    if raw.get("schema_version") != 1:
        return raw

    migrated = copy.deepcopy(raw)
    migrated["schema_version"] = STATE_SCHEMA_VERSION
    known = migrated.get("known")
    if isinstance(known, dict):
        for source_jobs in known.values():
            if isinstance(source_jobs, dict):
                for job in source_jobs.values():
                    if isinstance(job, dict):
                        job.setdefault("active", True)
    source_status = migrated.get("source_status")
    if isinstance(source_status, dict):
        for status in source_status.values():
            if isinstance(status, dict):
                status.setdefault("initialized", bool(status.get("last_success")))
                status.setdefault("health", "healthy" if status.get("ok") else "error")
    return migrated


def load_state(path: Path | None = None) -> dict[str, Any]:
    path = path or STATE_FILE
    raw = load_json(path, default_state())
    if not isinstance(raw, dict):
        raise ConfigurationError("data/state.json must contain a JSON object")
    raw = migrate_state(raw)
    if raw.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ConfigurationError(f"Unsupported state schema version: {raw.get('schema_version')!r}")
    if raw.get("initialized_at") is not None and not isinstance(raw.get("initialized_at"), str):
        raise ConfigurationError("state.initialized_at must be a string or null")
    if not isinstance(raw.get("known"), dict):
        raise ConfigurationError("state.known must be an object")
    if not isinstance(raw.get("source_status"), dict):
        raise ConfigurationError("state.source_status must be an object")
    for source_id, source_jobs in raw["known"].items():
        if not isinstance(source_id, str) or not isinstance(source_jobs, dict):
            raise ConfigurationError("state.known entries must map source ids to objects")
        for job_id, job in source_jobs.items():
            if not isinstance(job_id, str) or not isinstance(job, dict):
                raise ConfigurationError(f"state.known.{source_id} contains an invalid job")
            if job.get("id") != job_id or job.get("source_id") != source_id:
                raise ConfigurationError(f"state.known.{source_id}.{job_id} has inconsistent identifiers")
            validate_url(job.get("url"), f"state.known.{source_id}.{job_id}.url")
            if not isinstance(job.get("title"), str) or not isinstance(job.get("company"), str):
                raise ConfigurationError(f"state.known.{source_id}.{job_id} has invalid text fields")
            for field in ("first_seen", "last_seen"):
                if not isinstance(job.get(field), str):
                    raise ConfigurationError(f"state.known.{source_id}.{job_id}.{field} must be a string")
            for field in ("baseline", "active"):
                if not isinstance(job.get(field), bool):
                    raise ConfigurationError(f"state.known.{source_id}.{job_id}.{field} must be boolean")
            for field in ("priority", "score"):
                value = job.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ConfigurationError(f"state.known.{source_id}.{job_id}.{field} is invalid")
            if not isinstance(job.get("tags"), list) or not all(isinstance(tag, str) for tag in job["tags"]):
                raise ConfigurationError(f"state.known.{source_id}.{job_id}.tags must be a string array")
    for source_id, status in raw["source_status"].items():
        if not isinstance(source_id, str) or not isinstance(status, dict):
            raise ConfigurationError("state.source_status entries must be objects")
        if not isinstance(status.get("initialized"), bool) or not isinstance(status.get("ok"), bool):
            raise ConfigurationError(f"state.source_status.{source_id} has invalid boolean fields")
        if status.get("health") not in {"healthy", "degraded", "error"}:
            raise ConfigurationError(f"state.source_status.{source_id}.health is invalid")
        found = status.get("found")
        if not isinstance(found, int) or isinstance(found, bool) or found < 0:
            raise ConfigurationError(f"state.source_status.{source_id}.found is invalid")
        for field in ("last_checked", "last_success", "error"):
            if status.get(field) is not None and not isinstance(status.get(field), str):
                raise ConfigurationError(f"state.source_status.{source_id}.{field} must be string or null")
    return raw


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def reconcile_state(state: dict[str, Any], sources: list[SourceConfig]) -> tuple[int, int]:
    """Remove stale config entries and re-key records after normalization changes."""
    old_known = state["known"]
    allowed_ids = {source["id"] for source in sources}
    removed = sum(
        len(bucket)
        for source_id, bucket in old_known.items()
        if source_id not in allowed_ids and isinstance(bucket, dict)
    )
    merged = 0
    normalized_known: dict[str, dict[str, Any]] = {}

    for source in sources:
        source_id = source["id"]
        bucket = old_known.get(source_id, {})
        normalized_bucket: dict[str, Any] = {}
        for job in bucket.values():
            if not isinstance(job, dict) or not is_job_title(job["title"]):
                removed += 1
                continue
            normalized_id = fingerprint(source_id, job["title"], job["url"])
            candidate = copy.deepcopy(job)
            candidate["id"] = normalized_id
            existing = normalized_bucket.get(normalized_id)
            if existing is None:
                normalized_bucket[normalized_id] = candidate
                continue
            merged += 1
            existing["first_seen"] = min(existing["first_seen"], candidate["first_seen"])
            existing["last_seen"] = max(existing["last_seen"], candidate["last_seen"])
            existing["baseline"] = bool(existing.get("baseline")) and bool(candidate.get("baseline"))
            existing["active"] = bool(existing.get("active")) or bool(candidate.get("active"))
        normalized_known[source_id] = normalized_bucket

    state["known"] = normalized_known
    state["source_status"] = {
        source_id: status for source_id, status in state["source_status"].items() if source_id in allowed_ids
    }
    return removed, merged


def public_payload(
    state: dict[str, Any],
    sources: list[SourceConfig],
    scan_time: str,
    new_count: int,
) -> dict[str, Any]:
    statuses = state.get("source_status", {})
    source_rows: list[dict[str, Any]] = []
    healthy = 0
    degraded = 0
    failed = 0
    for source in sources:
        status = statuses.get(source["id"], {})
        is_healthy = status.get("ok", False)
        health = status.get("health", "healthy" if is_healthy else "error")
        healthy += int(health == "healthy")
        degraded += int(health == "degraded")
        failed += int(health == "error")
        source_rows.append(
            {
                "id": source["id"],
                "name": source["name"],
                "priority": source["priority"],
                "home": source["home"],
                "ok": is_healthy,
                "health": health,
                "last_checked": status.get("last_checked"),
                "last_success": status.get("last_success"),
                "found": status.get("found", 0),
                "error": status.get("error"),
            }
        )

    jobs: list[JobRecord] = []
    for source_jobs in state.get("known", {}).values():
        jobs.extend(source_jobs.values())
    jobs.sort(key=lambda item: (item.get("first_seen", ""), -item.get("priority", 99)), reverse=True)
    active_total = sum(bool(job.get("active")) for job in jobs)

    return {
        "generated_at": scan_time,
        "baseline_ready": bool(state.get("initialized_at")),
        "stats": {
            "total": len(jobs),
            "active_total": active_total,
            "new_today": new_count,
            "healthy_sources": healthy,
            "degraded_sources": degraded,
            "failed_sources": failed,
            "source_count": len(sources),
        },
        "sources": source_rows,
        "jobs": jobs[:750],
    }


def telegram_send(new_jobs: list[JobRecord]) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if bool(token) != bool(chat_id):
        raise ConfigurationError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must either both be set or both be omitted")
    if not token:
        return "텔레그램 secret 미설정 - 알림 생략"
    if not new_jobs:
        return "새 공고 없음"

    ordered = sorted(new_jobs, key=lambda item: (-item["score"], item["priority"]))
    lines = [f"🔔 <b>JobRadar 새 채용공고 {len(ordered)}건</b>", ""]
    for job in ordered:
        tag_text = " · ".join(job["tags"]) or "일반"
        lines.extend(
            [
                f"<b>{html.escape(job['company'])}</b> · {html.escape(tag_text)}",
                f'<a href="{html.escape(job["url"], quote=True)}">{html.escape(job["title"])}</a>',
                "",
            ]
        )

    chunks: list[str] = []
    current = ""
    for line in lines:
        addition = line + "\n"
        if current and len(current) + len(addition) > 3500:
            chunks.append(current.rstrip())
            current = ""
        current += addition
    if current:
        chunks.append(current.rstrip())

    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in chunks:
        body = urlencode(
            {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = Request(endpoint, data=body, method="POST")
        try:
            with urlopen(request, timeout=20) as response:
                if response.status >= 300:
                    raise JobRadarError(f"Telegram HTTP {response.status}")
                try:
                    api_result = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise JobRadarError("Telegram returned an invalid response") from exc
                if not isinstance(api_result, dict) or api_result.get("ok") is not True:
                    raise JobRadarError("Telegram API rejected the notification")
        except HTTPError as exc:
            raise JobRadarError(f"Telegram HTTP {exc.code}") from exc
        except URLError as exc:
            reason = clean_text(str(exc.reason))[:200]
            raise JobRadarError(f"Telegram network failure: {reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise JobRadarError(f"Telegram network failure: {type(exc).__name__}") from exc
    return f"텔레그램 {len(ordered)}건 전송"


def append_github_summary(payload: dict[str, Any], new_jobs: list[JobRecord]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    stats = payload["stats"]
    lines = [
        "## JobRadar 실행 결과",
        "",
        f"- 수집 성공: {stats['healthy_sources']}/{stats['source_count']}곳",
        f"- 점검 필요: {stats['degraded_sources']}곳",
        f"- 수집 실패: {stats['failed_sources']}곳",
        f"- 저장된 공고: {stats['total']}건",
        f"- 이번 실행 신규: {len(new_jobs)}건",
        "",
    ]
    if new_jobs:
        lines.extend(["| 회사 | 공고 | 점수 |", "|---|---|---:|"])
        for job in new_jobs:
            safe_title = job["title"].replace("|", "\\|")
            lines.append(f"| {job['company']} | [{safe_title}]({job['url']}) | {job['score']} |")
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(
    dry_run: bool = False,
    alert_on_first_run: bool = False,
    reset_baseline: bool = False,
    *,
    sources_path: Path = SOURCES_FILE,
    state_path: Path = STATE_FILE,
    public_path: Path = PUBLIC_FILE,
) -> int:
    if alert_on_first_run and reset_baseline:
        raise ConfigurationError("--alert-on-first-run and --reset-baseline cannot be used together")
    sources = load_sources(sources_path)
    state = load_state(state_path)
    removed_jobs, merged_jobs = reconcile_state(state, sources)
    if removed_jobs or merged_jobs:
        print(f"State cleanup: removed {removed_jobs} invalid and merged {merged_jobs} duplicate records")
    scan_time = now_iso()
    new_jobs: list[JobRecord] = []
    available_sources = 0

    print(f"JobRadar scan {scan_time} - {len(sources)} sources")
    for source in sources:
        source_id = source["id"]
        previous_status = state.setdefault("source_status", {}).get(source_id, {})
        source_initialized = bool(previous_status.get("initialized"))
        try:
            result = collect_source(source)
            if not result.fetched_pages:
                detail = "; ".join(result.errors[:2]) or "No configured page could be fetched"
                raise JobRadarError(detail)
            available_sources += 1

            warnings = list(result.errors[:2])
            if not result.has_recruitment_marker:
                warnings.append("Recruitment markers were not found in fetched pages")
            previous_found = previous_status.get("found", 0)
            if isinstance(previous_found, int) and previous_found >= 5 and not result.jobs:
                warnings.append(f"Candidate count unexpectedly dropped from {previous_found} to 0")
            health = "degraded" if warnings else "healthy"

            bucket = state.setdefault("known", {}).setdefault(source_id, {})
            if not isinstance(bucket, dict):
                raise ConfigurationError(f"state.known.{source_id} must be an object")
            for known_job in bucket.values():
                if isinstance(known_job, dict):
                    known_job["active"] = False

            for job in result.jobs:
                existing = bucket.get(job["id"])
                if existing:
                    existing.update({key: value for key, value in job.items() if key != "first_seen"})
                    existing["last_seen"] = scan_time
                    existing["active"] = True
                    continue
                job["first_seen"] = scan_time
                job["last_seen"] = scan_time
                job["baseline"] = not source_initialized
                job["active"] = True
                bucket[job["id"]] = job
                if source_initialized or alert_on_first_run:
                    new_jobs.append(job)

            state["source_status"][source_id] = {
                "initialized": True,
                "ok": True,
                "health": health,
                "last_checked": scan_time,
                "last_success": scan_time,
                "found": len(result.jobs),
                "error": "; ".join(warnings) if warnings else None,
            }
            label = "WARN" if warnings else "OK  "
            print(f"  {label} {source['priority']:>2}. {source['name']}: {len(result.jobs)} candidates")
        except ConfigurationError:
            raise
        except Exception as exc:  # one broken company must not stop the other 19
            state["source_status"][source_id] = {
                "initialized": source_initialized,
                "ok": False,
                "health": "error",
                "last_checked": scan_time,
                "last_success": previous_status.get("last_success"),
                "found": previous_status.get("found", 0),
                "error": clean_text(str(exc))[:500],
            }
            print(f"  ERR {source['priority']:>2}. {source['name']}: {exc}", file=sys.stderr)

    if not state.get("initialized_at") and any(
        status.get("initialized") for status in state.get("source_status", {}).values()
    ):
        state["initialized_at"] = scan_time

    if reset_baseline:
        baseline_count = 0
        for source_jobs in state["known"].values():
            for job in source_jobs.values():
                job["baseline"] = True
                baseline_count += 1
        new_jobs.clear()
        print(f"Baseline reset: {baseline_count} existing records will not trigger alerts")

    payload = public_payload(state, sources, scan_time, len(new_jobs))
    if dry_run:
        print("Dry run: state, dashboard, and notifications were not changed.")
    else:
        # Notify before persisting the new fingerprints. If delivery fails, the
        # state remains unchanged and the next run retries rather than silently
        # losing the alert. This deliberately provides at-least-once delivery.
        notification_result = telegram_send(new_jobs)
        write_json(state_path, state)
        write_json(public_path, payload)
        print(notification_result)
        append_github_summary(payload, new_jobs)

    print(
        f"Done: {payload['stats']['healthy_sources']}/{payload['stats']['source_count']} healthy, "
        f"{len(new_jobs)} new, {payload['stats']['total']} stored"
    )
    return 0 if available_sources else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor official recruitment pages")
    parser.add_argument("--dry-run", action="store_true", help="fetch only; do not write or notify")
    parser.add_argument("--alert-on-first-run", action="store_true", help="alert while establishing the baseline")
    parser.add_argument("--reset-baseline", action="store_true", help="mark every currently known job as baseline")
    args = parser.parse_args()
    try:
        return run(
            dry_run=args.dry_run,
            alert_on_first_run=args.alert_on_first_run,
            reset_baseline=args.reset_baseline,
        )
    except JobRadarError as exc:
        print(f"JobRadar error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
