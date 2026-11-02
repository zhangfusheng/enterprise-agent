from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from enterprise_agent.tools.web.access_policy import validate_public_url, validate_redirect


class WebFetchError(RuntimeError):
    pass


def web_fetch(url: str, timeout: float = 10, max_bytes: int = 1_000_000, max_redirects: int = 3) -> dict:
    current = url
    validate_public_url(current)
    with httpx.Client(timeout=timeout, follow_redirects=False, headers={"User-Agent": "enterprise-agent/0.1"}) as client:
        response: httpx.Response | None = None
        for _ in range(max_redirects + 1):
            response = client.get(current)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise WebFetchError("redirect did not include location")
                current = validate_redirect(current, location)
                continue
            break
        if response is None or response.is_redirect:
            raise WebFetchError("maximum redirects exceeded")
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not any(item in content_type for item in ("text/html", "text/plain", "application/xhtml")):
            raise WebFetchError("unsupported content type")
        body = response.content
        if len(body) > max_bytes:
            raise WebFetchError("response exceeds size limit")
        soup = BeautifulSoup(body, "html.parser")
        title_tag = soup.title
        text = soup.get_text(" ", strip=True) if "html" in content_type else body.decode("utf-8", errors="replace")
        return {
            "original_url": url,
            "final_url": str(response.url),
            "title": title_tag.get_text(strip=True) if "html" in content_type and title_tag else "",
            "content": text,
            "fetched_at": datetime.now(UTC).isoformat(),
            "content_type": content_type,
        }

