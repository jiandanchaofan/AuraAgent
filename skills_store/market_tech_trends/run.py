"""market_tech_trends skill — searches Google News' public RSS feed for
recent new-technology/innovation headlines within a given market segment.
Standard library only (urllib + xml.etree), read-only, no API key.

Known limitation: Google News RSS is an unofficial-but-public integration
point (no formal API contract), so its exact response shape could change
without notice — errors are caught and reported as plain text rather than
letting the script crash.
"""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

QUERY_SUFFIX = "(new technology OR innovation OR breakthrough OR R&D)"
TIMEOUT_SECONDS = 10


def fetch_headlines(query: str, limit: int) -> list[dict]:
    encoded = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)
    items = root.findall("./channel/item")[:limit]
    return [
        {
            "title": item.findtext("title") or "",
            "source": item.findtext("source") or "",
            "published": item.findtext("pubDate") or "",
        }
        for item in items
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-json", required=True)
    args = json.loads(parser.parse_args().args_json)

    segment = args["segment"].strip()
    limit = max(1, min(int(args.get("limit", 8)), 15))
    query = f"{segment} {QUERY_SUFFIX}"

    try:
        headlines = fetch_headlines(query, limit)
    except (urllib.error.URLError, ET.ParseError) as exc:
        print(f"Error fetching tech-trend news for '{segment}': {exc}")
        return

    if not headlines:
        print(f"No technology-trend news found for market segment '{segment}'.")
        return

    print(f"Recent technology-trend news for the '{segment}' market ({len(headlines)} result(s)):")
    for h in headlines:
        line = f"- {h['title']}"
        if h["source"]:
            line += f" ({h['source']})"
        if h["published"]:
            line += f" [{h['published']}]"
        print(line)


if __name__ == "__main__":
    main()
