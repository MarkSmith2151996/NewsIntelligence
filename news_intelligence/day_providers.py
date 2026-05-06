"""Provider adapters for day-level NQ intelligence collection."""

from __future__ import annotations

import hashlib
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Iterable, List, Sequence
from xml.etree import ElementTree

import requests

from .day_models import DayEvent, MacroEvent, known_before_open, session_bucket_from_timestamp


def _stable_event_id(prefix: str, *parts: str) -> str:
    payload = "|".join(parts)
    return f"{prefix}_{hashlib.md5(payload.encode('utf-8')).hexdigest()[:16]}"


class DayEventProvider(ABC):
    name: str = "provider"

    @abstractmethod
    def fetch_day(self, date: str) -> List[DayEvent]:
        pass


class MacroEventProvider(ABC):
    name: str = "macro_provider"

    @abstractmethod
    def fetch_day_macro(self, date: str) -> List[MacroEvent]:
        pass


class BaseHttpProvider:
    def __init__(self, min_interval_seconds: float = 1.0, max_retries: int = 2, retry_sleep_seconds: float = 5.0):
        self.min_interval_seconds = min_interval_seconds
        self.max_retries = max_retries
        self.retry_sleep_seconds = retry_sleep_seconds
        self._last_request_ts = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "news-intelligence/1.0"})

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_ts
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, url: str, *, params: dict | None = None, timeout: int = 45) -> requests.Response:
        response = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            response = self.session.get(url, params=params, timeout=timeout)
            self._last_request_ts = time.time()
            if response.status_code != 429:
                break
            if attempt == self.max_retries:
                raise RuntimeError(f"{self.__class__.__name__} rate limited the request")
            time.sleep(self.retry_sleep_seconds * (attempt + 1))
        assert response is not None
        response.raise_for_status()
        return response


class GDELTDayEventProvider(BaseHttpProvider, DayEventProvider):
    name = "gdelt"
    BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, max_records: int = 50, min_interval_seconds: float = 6.0, max_retries: int = 3, retry_sleep_seconds: float = 15.0):
        super().__init__(min_interval_seconds=min_interval_seconds, max_retries=max_retries, retry_sleep_seconds=retry_sleep_seconds)
        self.max_records = max_records

    def fetch_day(self, date: str) -> List[DayEvent]:
        start = date.replace("-", "") + "000000"
        end = date.replace("-", "") + "235959"
        params = {
            "query": self._build_query(),
            "mode": "artlist",
            "maxrecords": str(self.max_records),
            "format": "json",
            "sort": "datedesc",
            "startdatetime": start,
            "enddatetime": end,
        }
        response = self._get(self.BASE_URL, params=params)
        articles = response.json().get("articles", [])
        return [self._article_to_event(date, article) for article in articles]

    def _build_query(self) -> str:
        terms: Iterable[str] = (
            'nasdaq', '"Nasdaq 100"', '"Nasdaq-100"', '"Federal Reserve"', 'fed', 'powell',
            'cpi', 'ppi', '"nonfarm payrolls"', '"jobless claims"', '"treasury yields"',
            'nvidia', 'apple', 'microsoft', 'amazon', 'meta', 'tesla',
        )
        return "(" + " OR ".join(terms) + ")"

    def _article_to_event(self, date: str, article: dict) -> DayEvent:
        url = article.get("url", "")
        title = article.get("title", "")
        seen = article.get("seendate", "")
        timestamp = self._format_timestamp(seen) if seen else f"{date}T00:00:00"
        source_country = article.get("sourcecountry", "")
        language = article.get("language", "")
        domain = article.get("domain", "")
        tags = [tag for tag in [domain, source_country, language] if tag]
        return DayEvent(
            event_id=_stable_event_id("gdelt", date, url, title),
            date=date,
            timestamp=timestamp,
            source_family="broad_event_news",
            provider=self.name,
            title=title,
            summary=title,
            url=url,
            author=domain,
            event_type=self._classify_event_type(title),
            importance=self._estimate_importance(title),
            session_bucket=session_bucket_from_timestamp(timestamp),
            known_before_open=known_before_open(timestamp),
            entities=self._extract_entities(title),
            tags=tags,
            raw_payload=article,
        )

    def _format_timestamp(self, seendate: str) -> str:
        try:
            dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ")
            return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        except ValueError:
            return seendate

    def _extract_entities(self, title: str) -> List[str]:
        known = ["Nasdaq", "Nasdaq 100", "Federal Reserve", "Powell", "CPI", "PPI", "Nvidia", "Apple", "Microsoft", "Amazon", "Meta", "Tesla"]
        return [entity for entity in known if entity.lower() in title.lower()]

    def _classify_event_type(self, title: str) -> str:
        lowered = title.lower()
        if any(term in lowered for term in ("fed", "powell", "federal reserve", "cpi", "ppi", "payrolls", "claims")):
            return "MACRO"
        if any(term in lowered for term in ("law", "court", "supreme court", "regulation", "regulator")):
            return "POLICY"
        if any(term in lowered for term in ("war", "attack", "tariff", "sanction", "election")):
            return "GEOPOLITICAL"
        if any(term in lowered for term in ("earnings", "guidance", "revenue", "forecast")):
            return "EARNINGS"
        return "MARKET"

    def _estimate_importance(self, title: str) -> float:
        lowered = title.lower()
        score = 0.3
        if any(term in lowered for term in ("fed", "powell", "cpi", "ppi", "payrolls", "court", "supreme court")):
            score += 0.4
        if any(term in lowered for term in ("nasdaq", "nvidia", "apple", "microsoft", "amazon", "meta", "tesla")):
            score += 0.2
        if any(term in lowered for term in ("surge", "plunge", "slump", "shock", "crisis")):
            score += 0.1
        return min(score, 1.0)


class AlpacaDayEventProvider(BaseHttpProvider, DayEventProvider):
    name = "alpaca"
    BASE_URL = "https://data.alpaca.markets/v1beta1/news"

    def __init__(self, max_records: int = 50, symbols: Sequence[str] | None = None):
        super().__init__(min_interval_seconds=0.4, max_retries=2, retry_sleep_seconds=3.0)
        self.max_records = max_records
        self.symbols = list(symbols or ["QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "AVGO"])
        key_id = os.environ.get("APCA_API_KEY_ID")
        secret = os.environ.get("APCA_API_SECRET_KEY")
        if not key_id or not secret:
            raise EnvironmentError("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY for Alpaca news ingestion")
        self.session.headers.update({"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret})

    def fetch_day(self, date: str) -> List[DayEvent]:
        params = {
            "start": f"{date}T00:00:00Z",
            "end": f"{date}T23:59:59Z",
            "limit": str(self.max_records),
            "sort": "asc",
            "symbols": ",".join(self.symbols),
            "include_content": "true",
        }
        response = self._get(self.BASE_URL, params=params)
        articles = response.json().get("news", response.json().get("articles", []))
        return [self._article_to_event(date, article) for article in articles]

    def _article_to_event(self, date: str, article: dict) -> DayEvent:
        headline = article.get("headline", article.get("title", ""))
        timestamp = article.get("created_at", article.get("updated_at", f"{date}T00:00:00Z")).replace("Z", "+00:00")
        summary = article.get("summary", article.get("content", ""))
        url = article.get("url", "")
        symbols = article.get("symbols", []) or []
        source = article.get("source", article.get("author", "alpaca"))
        return DayEvent(
            event_id=_stable_event_id("alpaca", date, url, headline),
            date=date,
            timestamp=timestamp,
            source_family="market_news",
            provider=self.name,
            title=headline,
            summary=summary[:1000],
            raw_text=article.get("content", "")[:4000],
            url=url,
            author=source,
            event_type=self._classify_event_type(headline, symbols),
            importance=self._estimate_importance(headline, symbols),
            session_bucket=session_bucket_from_timestamp(timestamp),
            known_before_open=known_before_open(timestamp),
            entities=list(symbols),
            tags=list(symbols),
            raw_payload=article,
        )

    def _classify_event_type(self, title: str, symbols: Sequence[str]) -> str:
        lowered = title.lower()
        if any(term in lowered for term in ("earnings", "guidance", "revenue", "forecast")):
            return "EARNINGS"
        if any(term in lowered for term in ("fed", "powell", "cpi", "nfp", "rate")):
            return "MACRO"
        if any(term in lowered for term in ("doj", "sec", "regulation", "court", "tariff", "sanction")):
            return "POLICY"
        if symbols:
            return "MARKET"
        return "NEWS"

    def _estimate_importance(self, title: str, symbols: Sequence[str]) -> float:
        score = 0.4 if symbols else 0.25
        lowered = title.lower()
        if any(term in lowered for term in ("earnings", "guidance", "fomc", "cpi", "nfp", "tariff", "antitrust")):
            score += 0.3
        if any(sym in {"NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "GOOGL"} for sym in symbols):
            score += 0.2
        return min(score, 1.0)


class FedRssDayEventProvider(BaseHttpProvider, DayEventProvider):
    name = "fed_rss"
    FEEDS = ("https://www.federalreserve.gov/feeds/press_all.xml", "https://www.federalreserve.gov/feeds/speeches.xml")

    def __init__(self, max_records: int = 25):
        super().__init__(min_interval_seconds=1.0, max_retries=2, retry_sleep_seconds=3.0)
        self.max_records = max_records

    def fetch_day(self, date: str) -> List[DayEvent]:
        events: List[DayEvent] = []
        for feed_url in self.FEEDS:
            response = self._get(feed_url, timeout=30)
            root = ElementTree.fromstring(response.text)
            for item in root.findall("./channel/item"):
                pub_date = item.findtext("pubDate", default="")
                timestamp = self._rss_pubdate_to_iso(pub_date)
                if not timestamp.startswith(date):
                    continue
                title = item.findtext("title", default="")
                link = item.findtext("link", default="")
                desc = item.findtext("description", default="")
                events.append(DayEvent(
                    event_id=_stable_event_id("fedrss", date, link, title),
                    date=date,
                    timestamp=timestamp,
                    source_family="macro_calendar",
                    provider=self.name,
                    title=title,
                    summary=unescape(desc),
                    url=link,
                    author="Federal Reserve",
                    event_type="MACRO",
                    importance=self._importance(title),
                    session_bucket=session_bucket_from_timestamp(timestamp),
                    known_before_open=known_before_open(timestamp),
                    entities=["Federal Reserve"],
                    tags=["fed"],
                    raw_payload={"feed_url": feed_url, "pubDate": pub_date},
                ))
        return events[: self.max_records]

    def _rss_pubdate_to_iso(self, value: str) -> str:
        try:
            dt = parsedate_to_datetime(value)
            return dt.isoformat()
        except (TypeError, ValueError):
            return value

    def _importance(self, title: str) -> float:
        lowered = title.lower()
        if any(term in lowered for term in ("fomc", "statement", "interest rate", "monetary policy", "powell")):
            return 0.9
        if any(term in lowered for term in ("minutes", "speech", "beige book")):
            return 0.7
        return 0.5


class FederalRegisterDayEventProvider(BaseHttpProvider, DayEventProvider):
    name = "federal_register"
    BASE_URL = "https://www.federalregister.gov/api/v1/documents.json"

    def __init__(self, max_records: int = 25):
        super().__init__(min_interval_seconds=1.0, max_retries=2, retry_sleep_seconds=3.0)
        self.max_records = max_records

    def fetch_day(self, date: str) -> List[DayEvent]:
        params = {
            "per_page": str(self.max_records),
            "order": "newest",
            "conditions[publication_date][is]": date,
            "conditions[type][]": ["PRESDOCU", "RULE", "NOTICE"],
            "fields[]": ["title", "abstract", "html_url", "publication_date", "type", "agencies", "document_number"],
        }
        response = self._get(self.BASE_URL, params=params, timeout=45)
        results = response.json().get("results", [])
        events = []
        for item in results:
            title = item.get("title", "")
            timestamp = f"{date}T08:45:00"
            agencies = [agency.get("name", "") for agency in item.get("agencies", []) if agency.get("name")]
            events.append(DayEvent(
                event_id=_stable_event_id("fedreg", date, item.get("document_number", ""), title),
                date=date,
                timestamp=timestamp,
                source_family="policy_legal",
                provider=self.name,
                title=title,
                summary=item.get("abstract", "")[:1000],
                url=item.get("html_url", ""),
                author=", ".join(agencies),
                event_type="POLICY",
                importance=self._importance(title, item.get("type", "")),
                session_bucket=session_bucket_from_timestamp(timestamp),
                known_before_open=known_before_open(timestamp),
                entities=agencies,
                tags=[item.get("type", "")],
                raw_payload=item,
            ))
        return events

    def _importance(self, title: str, document_type: str) -> float:
        score = 0.5 if document_type == "PRESDOCU" else 0.4
        lowered = title.lower()
        if any(term in lowered for term in ("tariff", "trade", "national emergency", "executive order", "sanction")):
            score += 0.3
        return min(score, 1.0)


class WikipediaCurrentEventsProvider(BaseHttpProvider, DayEventProvider):
    name = "wikipedia_current_events"
    BASE_URL = "https://en.wikipedia.org/w/api.php"

    def __init__(self, max_records: int = 25):
        super().__init__(min_interval_seconds=0.5, max_retries=2, retry_sleep_seconds=2.0)
        self.max_records = max_records

    def fetch_day(self, date: str) -> List[DayEvent]:
        page_title = self._page_title(date)
        params = {
            "action": "query",
            "prop": "revisions",
            "titles": page_title,
            "rvslots": "main",
            "rvprop": "content",
            "formatversion": "2",
            "format": "json",
        }
        response = self._get(self.BASE_URL, params=params, timeout=30)
        pages = response.json().get("query", {}).get("pages", [])
        if not pages:
            return []
        revision = (pages[0].get("revisions") or [{}])[0]
        content = revision.get("slots", {}).get("main", {}).get("content", "")
        bullets = self._extract_bullets(content)
        events = []
        for index, bullet in enumerate(bullets[: self.max_records]):
            title = self._clean_wikitext(bullet)
            timestamp = f"{date}T12:{index:02d}:00"
            events.append(DayEvent(
                event_id=_stable_event_id("wiki", date, str(index), title),
                date=date,
                timestamp=timestamp,
                source_family="broad_event_news",
                provider=self.name,
                title=title,
                summary=title,
                url=f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}",
                author="Wikipedia Current Events",
                event_type=self._classify_event_type(title),
                importance=self._importance(title),
                session_bucket=session_bucket_from_timestamp(timestamp),
                known_before_open=known_before_open(timestamp),
                entities=[],
                tags=["wikipedia"],
                raw_payload={"page": page_title, "raw_bullet": bullet},
            ))
        return events

    def _page_title(self, date: str) -> str:
        dt = datetime.strptime(date, "%Y-%m-%d")
        return f"Portal:Current events/{dt.year}_{dt.strftime('%B')}_{dt.day}"

    def _extract_bullets(self, content: str) -> List[str]:
        return [line.lstrip("* ").strip() for line in content.splitlines() if line.startswith("* ") or line.startswith("** ")]

    def _clean_wikitext(self, text: str) -> str:
        cleaned = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", text)
        cleaned = re.sub(r"\{\{[^}]+\}\}", "", cleaned)
        cleaned = re.sub(r"<ref[^>]*>.*?</ref>", "", cleaned)
        cleaned = re.sub(r"'''?|''", "", cleaned)
        cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()

    def _classify_event_type(self, title: str) -> str:
        lowered = title.lower()
        if any(term in lowered for term in ("election", "president", "government", "parliament", "court", "law")):
            return "POLICY"
        if any(term in lowered for term in ("war", "attack", "military", "missile", "sanction", "protest")):
            return "GEOPOLITICAL"
        if any(term in lowered for term in ("economy", "inflation", "interest rate", "gdp", "fed")):
            return "MACRO"
        return "MARKET"

    def _importance(self, title: str) -> float:
        lowered = title.lower()
        score = 0.4
        if any(term in lowered for term in ("war", "president", "supreme court", "interest rate", "inflation")):
            score += 0.2
        return min(score, 1.0)


class FedCalendarMacroProvider(BaseHttpProvider, MacroEventProvider):
    name = "fed_calendar"
    CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"

    def __init__(self):
        super().__init__(min_interval_seconds=1.0, max_retries=2, retry_sleep_seconds=3.0)

    def fetch_day_macro(self, date: str) -> List[MacroEvent]:
        response = self._get(self.CALENDAR_URL, timeout=30)
        if date not in response.text:
            return []
        return [MacroEvent(
            event_id=_stable_event_id("fedcal", date, "fomc_calendar"),
            date=date,
            timestamp=f"{date}T14:00:00",
            provider=self.name,
            event_name="FOMC calendar date",
            category="FOMC",
            scheduled_time="14:00:00",
            importance=1.0,
            known_before_open=True,
            raw_payload={"source_url": self.CALENDAR_URL},
        )]


def build_provider(name: str, max_records: int = 25) -> DayEventProvider | MacroEventProvider:
    normalized = name.strip().lower()
    if normalized == "gdelt":
        return GDELTDayEventProvider(max_records=max_records)
    if normalized == "alpaca":
        return AlpacaDayEventProvider(max_records=max_records)
    if normalized == "fed_rss":
        return FedRssDayEventProvider(max_records=max_records)
    if normalized == "federal_register":
        return FederalRegisterDayEventProvider(max_records=max_records)
    if normalized == "wikipedia":
        return WikipediaCurrentEventsProvider(max_records=max_records)
    if normalized == "fed_calendar":
        return FedCalendarMacroProvider()
    raise ValueError(f"Unknown provider: {name}")


DEFAULT_FREE_PROVIDERS = ["fed_rss", "federal_register", "wikipedia", "gdelt"]
