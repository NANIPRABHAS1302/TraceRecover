"""Multi-browser Playwright automation engine with domain allowlists, download blocking, and diagnostics."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from connectors.permissions import PermissionEngine
from connectors.schemas import ConnectorConfig

_SYSTEM_CHROMIUM_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

_SYSTEM_FIREFOX_PATHS = [
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    "/usr/bin/firefox",
]

_BROWSER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tracerecover-browser")


def _find_existing_path(candidates: list[str]) -> str:
    for path in candidates:
        if Path(path).exists():
            return path
    return ""


class BrowserIntelligenceManager:
    """Executes safe read-first multi-browser navigation, inspection, screenshot, and diagnostics."""

    SUPPORTED_ENGINES = ("chromium", "firefox", "webkit")

    def __init__(self, permission_engine: PermissionEngine, artifacts_dir: Path | str) -> None:
        self.permissions = permission_engine
        self.artifacts_dir = Path(artifacts_dir)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._history: list[dict[str, Any]] = []
        self._engines_cache: tuple[float, list[dict[str, Any]]] | None = None

    def detect_engines(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Probe real availability of Chromium, Firefox, and WebKit (30s TTL cache avoids repeated disk scans)."""
        now = time.monotonic()
        if not force_refresh and self._engines_cache is not None and (now - self._engines_cache[0]) < 30.0:
            return [dict(item) for item in self._engines_cache[1]]
        try:
            import playwright  # noqa: F401
            pw_installed = True
        except Exception:
            pw_installed = False

        pw_cache = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright" if os.name == "nt" else Path.home() / ".cache" / "ms-playwright"
        pw_dirs = [p for p in pw_cache.iterdir() if p.is_dir()] if pw_cache.exists() else []

        def _non_stub_which(cmd: str) -> str:
            found = shutil.which(cmd) or ""
            return "" if "windowsapps" in found.lower() else found

        chrome_exe = _find_existing_path(_SYSTEM_CHROMIUM_PATHS) or (_non_stub_which("google-chrome") or _non_stub_which("msedge") or "")

        engines: list[dict[str, Any]] = []
        for engine in self.SUPPORTED_ENGINES:
            matched_dir = next((d for d in pw_dirs if d.name.lower().startswith(engine)), None)
            cached = bool(matched_dir and any(matched_dir.iterdir()))
            # Firefox/WebKit require genuine Playwright managed binaries; avoid unpatched WindowsApps stubs
            sys_exe = chrome_exe if engine == "chromium" else ""
            available = bool(pw_installed and (cached or sys_exe))
            if not pw_installed:
                reason = "Playwright Python package is not installed in the runtime."
            elif cached:
                reason = "Playwright managed browser binary installed."
            elif sys_exe:
                reason = f"Available via system browser executable: {Path(sys_exe).name}."
            else:
                reason = f"Playwright {engine} runtime binary is not installed locally (install with playwright install {engine})."
            engines.append(
                {
                    "engine": engine,
                    "available": available,
                    "playwright_installed": pw_installed,
                    "playwright_cached": cached,
                    "system_executable": sys_exe,
                    "status": "available" if available else "unavailable",
                    "reason": reason,
                }
            )
        self._engines_cache = (now, [dict(item) for item in engines])
        return engines

    def get_history(self, limit: int = 30) -> list[dict[str, Any]]:
        return list(reversed(self._history[-limit:]))

    def _default_connector(self, engine: str, allowed_domains: list[str] | None = None) -> ConnectorConfig:
        cfg = ConnectorConfig(
            id=f"browser-{engine}",
            name=f"Playwright ({engine.title()})",
            category="browser",
            transport="browser_engine",
            enabled=True,
            browser_engine=engine,
        )
        if allowed_domains:
            cfg.permissions.allowed_domains = [d.lower() for d in allowed_domains]
        return cfg

    def _resolve_launch_kwargs(self, engine: str) -> tuple[bool, dict[str, Any], str]:
        info = next((item for item in self.detect_engines() if item["engine"] == engine), None)
        if not info or not info["available"]:
            msg = info["reason"] if info else f"Unsupported engine '{engine}'"
            return False, {}, msg
        kwargs: dict[str, Any] = {"headless": True}
        if not info["playwright_cached"] and info["system_executable"] and engine == "chromium":
            kwargs["executable_path"] = info["system_executable"]
        elif not info["playwright_cached"] and info["system_executable"] and engine == "firefox":
            kwargs["executable_path"] = info["system_executable"]
        return True, kwargs, ""

    async def _run_browser_action(
        self,
        *,
        action: str,
        url: str,
        engine: str = "chromium",
        connector: ConnectorConfig | None = None,
        selector: str = "",
        query: str = "",
        timeout_ms: int = 15000,
        approved_actions: set[str] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        target_engine = (engine or "chromium").lower()
        if target_engine not in self.SUPPORTED_ENGINES:
            return {"ok": False, "error": f"Unsupported browser engine '{engine}'. Choose from chromium, firefox, webkit."}

        cfg = connector or self._default_connector(target_engine)
        ok_url, url_reason = self.permissions.check_url(cfg, url)
        if not ok_url:
            return {"ok": False, "blocked": True, "error": url_reason, "engine": target_engine, "url": url}

        if action == "submit_form":
            if not cfg.permissions.allow_form_submit:
                return {
                    "ok": False,
                    "blocked": True,
                    "requires_approval": True,
                    "error": "Form submission is blocked unless allow_form_submit=True and explicitly approved.",
                }
            if not approved_actions or f"{cfg.id}:submit_form" not in approved_actions:
                return {
                    "ok": False,
                    "blocked": True,
                    "requires_approval": True,
                    "error": "Form submission requires explicit human approval.",
                }

        launchable, launch_kwargs, unavailable_reason = self._resolve_launch_kwargs(target_engine)
        if not launchable:
            return {
                "ok": False,
                "status": "unavailable",
                "engine": target_engine,
                "url": url,
                "error": unavailable_reason,
            }

        from playwright.async_api import async_playwright

        console_errors: list[str] = []
        failed_requests: list[dict[str, Any]] = []
        http_errors: list[dict[str, Any]] = []
        blocked_downloads: list[str] = []
        blocked_subrequests: list[dict[str, str]] = []
        redirect_chain: list[str] = []

        try:
            async with async_playwright() as p:
                launcher = getattr(p, target_engine)
                browser = await launcher.launch(**launch_kwargs)
                context = await browser.new_context(accept_downloads=bool(cfg.permissions.allow_downloads))
                page = await context.new_page()

                async def on_route(route: Any) -> None:
                    req_url = route.request.url
                    ok_req, req_reason = self.permissions.check_url(cfg, req_url)
                    if not ok_req:
                        blocked_subrequests.append({"url": req_url, "reason": req_reason})
                        await route.abort("blockedbyclient")
                        return
                    await route.continue_()

                await page.route("**/*", on_route)

                def on_console(msg: Any) -> None:
                    if getattr(msg, "type", "") in ("error", "warning"):
                        console_errors.append(f"[{msg.type}] {msg.text}")

                def on_request_failed(req: Any) -> None:
                    failed_requests.append({"url": req.url, "method": req.method, "failure": req.failure})

                def on_response(resp: Any) -> None:
                    if getattr(resp, "status", 0) >= 400:
                        http_errors.append({"url": resp.url, "status": resp.status})

                def on_download(dl: Any) -> None:
                    blocked_downloads.append(getattr(dl, "suggested_filename", "unknown"))
                    if not cfg.permissions.allow_downloads:
                        asyncio.create_task(dl.cancel())

                page.on("console", on_console)
                page.on("requestfailed", on_request_failed)
                page.on("response", on_response)
                page.on("download", on_download)

                resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                final_url = page.url
                if resp is not None:
                    chain_req = resp.request
                    while chain_req is not None:
                        redirect_chain.insert(0, chain_req.url)
                        chain_req = chain_req.redirected_from
                ok_final, final_reason = self.permissions.check_url(cfg, final_url)
                if not ok_final:
                    await context.close()
                    await browser.close()
                    return {
                        "ok": False,
                        "blocked": True,
                        "error": f"Redirect target blocked by policy: {final_reason}",
                        "engine": target_engine,
                        "url": final_url,
                        "redirect_chain": redirect_chain,
                        "blocked_subrequests": blocked_subrequests,
                    }

                title = (await page.title()) or ""
                # Detect login/captcha walls honestly without bypassing
                body_text = (await page.locator("body").inner_text(timeout=5000)) if await page.locator("body").count() else ""
                lowered_body = body_text.lower()
                gated_signals = [
                    s for s in ("captcha", "verify you are human", "cloudflare", "sign in to continue", "access denied")
                    if s in lowered_body
                ]

                result: dict[str, Any] = {
                    "ok": True,
                    "engine": target_engine,
                    "action": action,
                    "requested_url": url,
                    "final_url": final_url,
                    "redirect_chain": redirect_chain,
                    "http_status": resp.status if resp else None,
                    "title": title.strip(),
                    "gated_warning": (
                        f"Page may be gated ({', '.join(gated_signals)}); TraceRecover will not bypass authentication or CAPTCHA."
                        if gated_signals
                        else ""
                    ),
                    "downloads_blocked": blocked_downloads,
                    "blocked_subrequests": blocked_subrequests,
                    "console_events": console_errors[:25],
                    "failed_requests": failed_requests[:25],
                    "http_errors": http_errors[:25],
                }

                if action in ("navigate", "read_content"):
                    headings = await page.locator("h1, h2, h3").all_inner_texts()
                    links = await page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href]')).slice(0, 25).map(a => ({
                            text: (a.innerText || '').trim().slice(0, 120),
                            href: a.href
                        })).filter(x => x.href.startsWith('http'))"""
                    )
                    meta = await page.evaluate(
                        """() => {
                            const out = {};
                            document.querySelectorAll('meta[name], meta[property]').forEach(m => {
                                const k = m.getAttribute('name') || m.getAttribute('property');
                                const v = m.getAttribute('content');
                                if (k && v && Object.keys(out).length < 20) out[k] = v.slice(0, 300);
                            });
                            return out;
                        }"""
                    )
                    framed = self.permissions.frame_untrusted_output(f"browser:{target_engine}:{final_url}", body_text[:6000])
                    result["headings"] = [h.strip() for h in headings if h.strip()][:20]
                    result["links"] = links
                    result["metadata"] = meta
                    result["content_excerpt"] = body_text[:4000]
                    result["framed_output"] = framed
                elif action == "inspect_section":
                    loc = page.locator(selector or "main, article, body").first
                    text = await loc.inner_text(timeout=5000) if await loc.count() else ""
                    result["selector"] = selector or "main, article, body"
                    result["section_text"] = text[:4000]
                    result["framed_output"] = self.permissions.frame_untrusted_output(f"browser:{target_engine}:{final_url}", text[:4000])
                elif action == "search_docs":
                    q_terms = [t.lower() for t in (query or "").split() if len(t) >= 2]
                    lines = [ln.strip() for ln in body_text.splitlines() if ln.strip()]
                    matches = [ln for ln in lines if any(term in ln.lower() for term in q_terms)][:12]
                    result["query"] = query
                    result["matches"] = matches
                    result["framed_output"] = self.permissions.frame_untrusted_output(
                        f"browser:{target_engine}:{final_url}", "\n".join(matches)
                    )
                elif action == "screenshot":
                    fname = f"shot_{target_engine}_{int(time.time() * 1000)}.png"
                    shot_path = self.artifacts_dir / fname
                    await page.screenshot(path=str(shot_path), full_page=False)
                    result["screenshot_path"] = str(shot_path)
                    result["screenshot_filename"] = fname
                elif action == "diagnostics":
                    timing = await page.evaluate(
                        """() => {
                            const nav = performance.getEntriesByType('navigation')[0];
                            return nav ? {
                                domContentLoadedMs: Math.round(nav.domContentLoadedEventEnd),
                                loadEventMs: Math.round(nav.loadEventEnd),
                                transferSize: nav.transferSize || 0
                            } : {};
                        }"""
                    )
                    result["console_events"] = console_errors[:30]
                    result["failed_requests"] = failed_requests[:30]
                    result["http_errors"] = http_errors[:30]
                    result["timing"] = timing

                await context.close()
                await browser.close()
        except Exception as exc:
            result = {
                "ok": False,
                "engine": target_engine,
                "action": action,
                "url": url,
                "error": str(exc),
                "console_events": console_errors[:20],
                "failed_requests": failed_requests[:20],
            }

        result["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        self._history.append(
            {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "engine": target_engine,
                "action": action,
                "url": result.get("final_url") or url,
                "ok": bool(result.get("ok")),
                "title": result.get("title", ""),
                "latency_ms": result["latency_ms"],
                "error": result.get("error", ""),
            }
        )
        return result

    async def execute_async(self, **kwargs: Any) -> dict[str, Any]:
        return await self._run_browser_action(**kwargs)

    def execute_sync(self, **kwargs: Any) -> dict[str, Any]:
        """Thread-safe synchronous entrypoint works both inside and outside active event loops."""
        try:
            asyncio.get_running_loop()
            running_loop = True
        except RuntimeError:
            running_loop = False

        if running_loop:
            fut = _BROWSER_EXECUTOR.submit(lambda: asyncio.run(self._run_browser_action(**kwargs)))
            return fut.result(timeout=float(kwargs.get("timeout_ms", 15000)) / 1000.0 + 15.0)
        return asyncio.run(self._run_browser_action(**kwargs))
