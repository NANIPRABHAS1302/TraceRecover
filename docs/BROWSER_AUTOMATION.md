# Multi-Browser Intelligence & Safety Controls

`connectors/browser/engine.py` provides permission-controlled Playwright inspection across **Chromium**, **Firefox**, and **WebKit**.

## Browser Availability Detection

- **Chromium**: Uses Playwright's managed Chromium binary when cached, or detects installed system Chromium/Chrome/Edge executables cleanly.
- **Firefox / WebKit**: Detects Playwright binaries or system Firefox honestly; if not installed on the host, Connect Hub reports `unavailable` with installation instructions rather than faking execution.

## Guardrails & Limitations

- Only `http://` and `https://` URLs are permitted; cloud metadata hosts are blocked.
- Optional per-connector `allowed_domains` and `blocked_domains` are checked both before navigation and after HTTP redirects.
- File downloads are cancelled automatically unless explicitly enabled in connector permissions.
- Form submission is blocked unless `allow_form_submit=True` AND explicit human approval (`connector_id:submit_form`) is passed.
- Login walls, paywalls, and CAPTCHAs are reported honestly as `gated_warning` and are never bypassed.

## Supported Read-First Actions

- `navigate` & `read_content`: Extract page title, HTTP status, headings, links, `<meta>` tags, and bounded clean text.
- `inspect_section`: Read structured text under a CSS selector.
- `search_docs`: Filter extracted documentation lines against query terms.
- `screenshot`: Capture viewport screenshots into `data/connectors/browser_artifacts/`.
- `diagnostics`: Capture console errors/warnings, failed network requests, HTTP >= 400 responses, and DOM navigation timing.
