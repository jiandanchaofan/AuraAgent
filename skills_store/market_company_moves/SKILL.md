---
name: market_company_moves
description: Research recent major-company-activity news within a specific market segment (e.g. "smartphones", "electric vehicles", "AI chips") via Google News' public RSS search feed (official subscription mechanism, not scraping; no API key required). Returns recent headlines about acquisitions, partnerships, earnings, leadership changes, and strategy shifts among companies in that segment.
input_schema:
  type: object
  properties:
    segment:
      type: string
      description: The market segment to research, e.g. "smartphone", "electric vehicle", "AI chip".
    limit:
      type: integer
      description: Max number of headlines to return (1-15). Defaults to 8.
  required:
    - segment
---

# market_company_moves

Searches Google News' public RSS feed for recent headlines about major
company activity (acquisitions, partnerships, earnings, leadership,
strategy) within a given market segment. Read-only, no API key, no side
effects beyond one outbound HTTPS GET.

Invocation: `python run.py --args-json '{"segment": "smartphone", "limit": 8}'`
