"""Real-browser proof for the conformance app's pinned htmx SSE assets."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import BrowserType, sync_playwright

VENDOR = Path(__file__).resolve().parents[1] / "conformance" / "static" / "vendor"


def _browser_executable(browser_type: BrowserType) -> str | None:
    configured = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if configured:
        return configured
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.is_file():
        return str(mac_chrome)
    return None


@pytest.mark.issue(772)
def test_pinned_htmx_sse_extension_connects_and_swaps_in_chromium() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=_browser_executable(playwright.chromium),
            headless=True,
        )
        page = browser.new_page()
        page.set_content("<main></main>")
        page.evaluate(
            """
            () => {
              window.__eventSources = [];
              window.EventSource = class MockEventSource {
                constructor(url) {
                  this.url = url;
                  this.listeners = new Map();
                  window.__eventSources.push(this);
                }
                addEventListener(name, listener) {
                  const listeners = this.listeners.get(name) || [];
                  listeners.push(listener);
                  this.listeners.set(name, listeners);
                }
                removeEventListener(name, listener) {
                  const listeners = this.listeners.get(name) || [];
                  this.listeners.set(name, listeners.filter(item => item !== listener));
                }
                close() {}
                emit(name, data) {
                  const event = new MessageEvent(name, {data});
                  for (const listener of this.listeners.get(name) || []) listener(event);
                }
              };
            }
            """
        )
        page.add_script_tag(path=VENDOR / "htmx-2.0.10.min.js")
        page.add_script_tag(path=VENDOR / "htmx-ext-sse-2.2.4.min.js")
        page.locator("body").evaluate(
            "(element, markup) => { element.innerHTML = markup; }",
            """
            <main
              hx-ext="sse"
              sse-connect="https://example.test/notifications/events"
            >
              <ol id="notifications">
                <li sse-swap="message" hx-target="#notifications" hx-swap="afterbegin"></li>
              </ol>
            </main>
            """,
        )
        page.evaluate("htmx.process(document.body)")

        page.wait_for_function("window.__eventSources.length === 1", timeout=5_000)
        assert page.evaluate("window.__eventSources[0].url") == (
            "https://example.test/notifications/events"
        )
        page.evaluate(
            """
            window.__eventSources[0].emit(
              "message",
              '<li data-notification-id="notification-browser">Delivered by SSE</li>'
            )
            """
        )
        page.get_by_text("Delivered by SSE").wait_for()
        assert page.locator('[data-notification-id="notification-browser"]').count() == 1
        browser.close()
