# Parsers

`pf_core.parsers` holds framework-level pieces for content ingestion pipelines — code that fetches from external sources (RSS feeds, sitemaps, web archives) and hands structured records to an orchestrator.

The package is intentionally small: the two exception types and the HTML body extractor below. Per-source parsers and the record types an orchestrator consumes stay in the project — see "Scope" below.

## What's here

### Exception types

```python
from pf_core.parsers import ParseError, PaywalledPost
```

Two exception classes form the contract between per-source parser modules and the consumer's ingest orchestrator. Both subclass `pf_core.exceptions.AppError` so they participate in the standard `log_exception()` flow.

| Exception | When to raise | Orchestrator response |
|---|---|---|
| `ParseError` | Network failure, shape mismatch, empty body — anything that means "this source failed for this run" | Skip the source, log a WARNING, count it as a failure. A single source failing should not fail the whole ingest. |
| `PaywalledPost` | A specific post is paid-only and cannot be extracted | Skip the post, count the skip apart from failures, log at WARNING. Distinct from `ParseError` so paywall skips don't count as parser failures. |

Individual parsers may subclass these for source-specific signals; orchestrators only need to catch the two base classes (and a generic `Exception` fallback).

### HTML body extractor

```python
from pf_core.parsers import parse_body_html, BodyExtractor, PostLink

text, links = parse_body_html(post_html)
# text: str — paragraphs preserved, blank lines collapsed
# links: list[PostLink] — each with url + anchor_text + surrounding_text
```

Pure-stdlib (`html.parser.HTMLParser`) walker that turns post body HTML into a normalized plain-text rendering plus a list of inline links with surrounding-text context. Designed for the content ingestion pattern: the LLM gets clean prose for record extraction and per-link context for "which claim does this link support?" disambiguation.

| Symbol | Purpose |
|---|---|
| `parse_body_html(html, *, context_window_chars=120) -> (text, links)` | High-level entry point. Raises `ParseError` if the parser crashes; empty input returns `("", [])`. |
| `BodyExtractor` | The `HTMLParser` subclass. Subclass for custom tag handling; expose `text_parts` + `link_records` after `feed()`+`close()`. A subclass that emits text must call `self._emit(chunk)` rather than appending to `text_parts` directly, or recorded link offsets drift. |
| `normalize_plain_text(text) -> str` | Strip leading/trailing whitespace; collapse 3+ consecutive blank lines to one. Idempotent. |
| `BLOCK_TAGS` | `frozenset` of tags whose open/close emits a paragraph break in the buffer (`p`, `div`, `li`, headers, `blockquote`, …). |
| `SKIP_TAGS` | `frozenset` of tags whose inner text is dropped (`script`, `style`). |
| `DEFAULT_CONTEXT_WINDOW_CHARS` | Default ± window for `PostLink.surrounding_text` (120). |
| `PostLink` (`pf_core.parsers.types`) | Dataclass: `url`, `anchor_text`, `surrounding_text`. |

Empty-href and empty-anchor `<a>` tags are filtered automatically (many sites sprinkle these for layout). Whitespace inside `surrounding_text` is collapsed to single spaces so the LLM sees clean prose.

### Example use

```python
# In a per-source parser module:
from pf_core.parsers import ParseError, PaywalledPost


def fetch(ref):
    resp = httpx.get(ref.url)
    if resp.status_code != 200:
        raise ParseError(f"fetch failed: {resp.status_code}", context={"url": ref.url})
    if "subscribe to read" in resp.text.lower():
        raise PaywalledPost(ref.url)
    return _parse(resp.text)


# In the orchestrator:
from pf_core.parsers import ParseError, PaywalledPost

for source in sources:
    try:
        refs = parser.list_refs(source, since, until)
    except ParseError as e:
        log_exception(e, message_prepend="parser failed, skipping source")
        continue
    for ref in refs:
        try:
            post = parser.fetch(ref)
        except PaywalledPost:
            paywalled_count += 1
            continue
        except ParseError as e:
            parse_errors += 1
            log_exception(e)
            continue
        # ... handle `post` ...
```

## Scope

An addition belongs here only when it is policy-free: no source list, no source ranking, no business logic. Per-source parser modules and the parser-to-orchestrator record types stay in the project, since each orchestrator wants its own shape.
