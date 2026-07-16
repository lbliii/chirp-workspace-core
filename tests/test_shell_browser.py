"""Real-browser accessibility and keyboard smoke for the shared shell."""

import os
from pathlib import Path

import pytest
from chirp import App, AppConfig, Template
from playwright.sync_api import BrowserType, sync_playwright

from chirp_workspace_core import (
    Breadcrumb,
    NavigationItem,
    ShellCommand,
    ShellContext,
    UserId,
    WorkspaceChoice,
    WorkspaceId,
    workspace_templates_dir,
)


def _browser_executable(browser_type: BrowserType) -> str | None:
    configured = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if configured:
        return configured
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.is_file():
        return str(mac_chrome)
    return None


def _render_shell() -> str:
    shell = ShellContext(
        workspace_id=WorkspaceId("workspace-1"),
        user_id=UserId("user-1"),
        workspace_name="Example Workspace",
        user_display_name="Avery Example",
        product_name="Board",
        primary_navigation=(NavigationItem("Board", "/board", "board", active=True),),
        product_navigation=(NavigationItem("Issues", "/board/issues", "board"),),
        workspace_choices=(
            WorkspaceChoice(WorkspaceId("workspace-1"), "Example Workspace", "/w/one", True),
        ),
        breadcrumbs=(Breadcrumb("Board", "/board"), Breadcrumb("Issues")),
        commands=(ShellCommand("new-issue", "Create issue", "/board/issues/new", shortcut="N"),),
        commands_url="/commands",
    )
    app = App(config=AppConfig(template_dir=workspace_templates_dir()))
    return app.render(Template("workspace_core/shell.html", shell=shell))


@pytest.mark.issue(765)
def test_shell_keyboard_focus_mobile_reduced_motion_and_contrast_in_chromium() -> None:
    assets = workspace_templates_dir().parent / "assets"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=_browser_executable(playwright.chromium),
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.set_content(_render_shell())
        page.add_style_tag(path=assets / "shell.css")
        page.add_script_tag(path=assets / "shell.js")

        assert page.get_by_role("banner").count() == 1
        assert page.get_by_role("main").count() == 1
        assert page.get_by_role("navigation", name="Workspace products").count() == 1
        assert page.get_by_role("link", name="Commands ⌘K").get_attribute("href") == "/commands"
        assert page.get_by_role("link", name="Skip to content").count() == 1

        opener = page.get_by_role("link", name="Commands ⌘K")
        opener.focus()
        page.keyboard.press("Control+K")
        palette = page.locator("#workspace-command-palette")
        assert page.get_by_role("dialog", name="Commands").count() == 1
        assert palette.evaluate("element => element.open") is True
        assert page.get_by_role("searchbox", name="Search commands").evaluate(
            "element => element === document.activeElement"
        )
        page.keyboard.press("Escape")
        assert opener.evaluate("element => element === document.activeElement")

        page.keyboard.press("/")
        search = page.get_by_role("searchbox", name="Search commands")
        search.fill("missing")
        assert page.get_by_text("No matching commands.").is_visible()
        page.keyboard.press("Escape")
        assert palette.evaluate("element => element.open") is False

        page.set_viewport_size({"width": 600, "height": 800})
        toggle = page.get_by_role("button", name="Toggle navigation")
        toggle.click()
        assert toggle.get_attribute("aria-expanded") == "true"
        assert page.locator("#workspace-sidebar").get_attribute("data-workspace-open") == ""

        page.emulate_media(reduced_motion="reduce")
        assert (
            page.locator("#workspace-sidebar").evaluate(
                "element => getComputedStyle(element).transitionDuration"
            )
            == "0s"
        )

        contrast = page.evaluate(
            """
            () => {
              const parse = (value) => value.match(/[0-9.]+/g).slice(0, 3).map(Number);
              const luminance = (rgb) => {
                const values = rgb.map((value) => {
                  const channel = value / 255;
                  return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
                });
                return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2];
              };
              const style = getComputedStyle(document.body);
              const foreground = luminance(parse(style.color));
              const background = luminance(parse(style.backgroundColor));
              return (Math.max(foreground, background) + 0.05) /
                (Math.min(foreground, background) + 0.05);
            }
            """
        )
        assert contrast >= 4.5

        page.evaluate(
            """
            () => {
              const main = document.getElementById('workspace-main');
              document.body.dispatchEvent(new CustomEvent('htmx:beforeRequest', {
                detail: {elt: document.querySelector('[href="/board"]')}
              }));
              document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', {
                detail: {target: main}
              }));
            }
            """
        )
        assert page.locator("[data-workspace-page-heading]").evaluate(
            "element => element === document.activeElement"
        )

        page.evaluate("document.body.dispatchEvent(new CustomEvent('chirp:sse:disconnected'))")
        assert page.locator("[data-workspace-sse-status]").text_content() == (
            "Notifications disconnected; reconnecting."
        )
        browser.close()
