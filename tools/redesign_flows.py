"""The AFTER flows for tools/redesign_capture.py - every module of the
round-3 frame, driven with real input on the live :8600 server.

Demo plans open fresh, so every detected screen here is detected live in
the run that captured it (nothing pre-baked).
"""
from __future__ import annotations

import time

BASE = "http://127.0.0.1:8600"


def _settle(page, ms=900):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(ms)


def open_demo(cap, plan):
    page = cap.page
    page.goto("about:blank")
    page.goto(BASE + "/#projects")
    _settle(page)
    tab = page.locator("#pvTabDemo")
    if tab.count() and tab.is_visible():
        tab.click()
        _settle(page, 400)
    page.locator(f'.pv-card[title*="{plan}"]').first.click()
    _settle(page, 1500)


def detect(cap, timeout_s=300):
    page = cap.page
    page.locator("#detectBtn").click()
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        page.wait_for_timeout(1000)
        busy = page.evaluate("() => { const f = document.getElementById('runFlag'); return !!(f && !f.hidden); }")
        n = page.evaluate("() => (document.getElementById('stylesCount')||{}).textContent||''")
        if not busy and n.strip():
            break
    _settle(page, 1200)
    print(f"  detect finished in {time.time() - t0:.0f}s")


def tab(cap, mod, ms=1500):
    cap.page.locator(f"#modTab-{mod}").click()
    _settle(cap.page, ms)


def run_workflow(cap, args):
    """A multi-sheet set end to end: sheets grouped by discipline, a page
    switch from the rail, Detect, Approve & export, then the Summary over
    every APPROVED sheet (read back from the server's approved export)."""
    page = cap.page
    open_demo(cap, "Belle Farms Structural Plans.pdf")
    _settle(page, 3000)                       # triage reads the sheet numbers
    tab(cap, "plans", 2500)
    cap.shot("w1-plans-multisheet")
    tab(cap, "lines")
    rows = page.locator("#sheetsBody .sh-row")
    print("  sheet rows:", rows.count())
    if rows.count() > 1:
        rows.nth(1).click()
        _settle(page, 2500)
    cap.shot("w2-lines-page2")
    detect(cap)
    cap.shot("w3-page2-detected")
    appr = page.locator("#approveBtn")
    if appr.is_enabled():
        appr.click()
        _settle(page, 4000)
    cap.shot("w4-approved")
    tab(cap, "summary", 2000)
    seg = page.locator(".seg-group .seg").nth(1)
    seg.click()
    _settle(page, 4000)
    cap.shot("w5-summary-approved")
    tab(cap, "plans", 4000)
    cap.shot("w6-plans-after-approve", marks=[
        ("#plTiles", "Trade tiles from the APPROVED sheet's own export - the same sums as the Summary", "ok"),
        ("#plMeta", "3 sheets, 1 detected, 1 approved - from the server", "ok"),
    ])


def run_looks(cap, args):
    """One sheet in a given look (theme / ground / accent / width): the
    workspace, the summary, the projects page and the settings choices."""
    page = cap.page
    page.goto(BASE + "/#projects")
    _settle(page)
    cap.shot("L1-projects")
    open_demo(cap, "C4.0-SITE-GRADING-PLAN-Rev.1.pdf")
    tab(cap, "lines")
    detect(cap)
    cap.shot("L2-lines-detected")
    tab(cap, "summary", 2500)
    cap.shot("L3-summary")
    tab(cap, "earthwork", 8000)
    cap.shot("L4-earthwork")
    tab(cap, "lines")
    page.locator("#settingsLink").click()
    _settle(page)
    page.locator("#svAccentChoice").scroll_into_view_if_needed()
    _settle(page, 300)
    cap.shot("L5-settings")


def run_priced(cap, args):
    """Prices flow through: two TEST unit prices typed into the right rail
    (exactly where an estimator types them), then the Summary's extended
    and total. The prices are test inputs for this check, not data."""
    page = cap.page
    open_demo(cap, "C4.0-SITE-GRADING-PLAN-Rev.1.pdf")
    tab(cap, "lines")
    detect(cap)
    prices = page.locator('#itemized input[name^="price-"]')
    typed = []
    for i, v in enumerate(["18.50", "2.10"]):
        if prices.count() > i:
            el = prices.nth(i)
            el.fill(v)
            el.press("Tab")
            typed.append((el.get_attribute("name"), v))
            _settle(page, 600)
    print("  typed test prices:", typed)
    cap.shot("p1-rail-priced", marks=[
        ("#cardTakeoff", "Two TEST unit prices typed in the rail ($18.50, $2.10 per ft) - the row's ext $ and the trade subtotal follow", "diff"),
        ("#tallyBar", "The tally carries the priced total", "ok"),
    ])
    tab(cap, "summary", 2500)
    cap.shot("p2-summary-priced", marks=[
        (".sm-table tbody tr.sm-item", "Unit price x qty = extended, from the same rows the CSV and Approve use", "ok"),
        (".sm-table tfoot", "Total = the priced rows (TEST prices typed for this check)", "diff"),
        (".rd-card", "Unit prices still 'missing': 38 of 40 rows have no price - the total says so", "ok"),
    ])


def _mouse_drag(page, src_sel, to_xy, steps=14):
    """Press on a panel's head, move past the 6px threshold, carry it to
    to_xy and let go - real pointer input, the way a person drags."""
    b = page.locator(src_sel).first.bounding_box()
    sx, sy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    page.mouse.move(sx, sy)
    page.mouse.down()
    page.mouse.move(sx + 18, sy + 8, steps=4)
    page.mouse.move(to_xy[0], to_xy[1], steps=steps)
    page.wait_for_timeout(250)
    return sx, sy


def run_dock(cap, args):
    """The rails as a dock (dock.js): Koby asked for closable Sheets and
    module panels, Line styles taking most of the rail, panels dragged on
    top of one another or to either side, and a layout that loads as he
    left it. Every step below is real input on the live page."""
    page = cap.page
    page.goto(BASE + "/#projects")
    _settle(page)
    page.evaluate("() => { for (const k of ['core-dock-v1','core-rail-r','core-start-module']) localStorage.removeItem(k);"
                  " localStorage.setItem('core-module','lines'); }")
    open_demo(cap, "C4.0-SITE-GRADING-PLAN-Rev.1.pdf")
    tab(cap, "lines")
    detect(cap)
    cap.shot("k1-default", marks=[
        ("#sheetsCard .dk-ctl", "Every panel: options (...) and close (x) in its head", "ok"),
        ("#cardStyles", "Line styles FILLS the left rail (the others keep their own height)", "ok"),
        ("#cardTakeoff", "Takeoff fills the right rail the same way", "ok"),
        ("#dkPanelsBtn", "Panels: show / hide any panel, reset the layout", "ok"),
    ])
    page.locator("#sheetsCard .dk-x").click()
    page.wait_for_timeout(350)
    page.locator("#modCard .dk-x").click()
    page.wait_for_timeout(450)
    cap.shot("k2-closed", marks=[
        ("#cardStyles", "Sheets and the Lines tools closed: Line styles takes the whole rail", "ok"),
        (".dk-toast", "Says what happened, how to get it back, and offers Undo", "ok"),
    ])
    t = page.locator("#cardTakeoff").bounding_box()
    _mouse_drag(page, "#stylesHead h2", (t["x"] + t["width"] / 2, t["y"] + 10))
    cap.shot("k3-dragging", marks=[
        (".dk-ghost", "The panel in the air, by name", "ok"),
        (".dk-line", "Where it will land: above Takeoff, in the right rail", "ok"),
    ])
    page.mouse.up()
    page.wait_for_timeout(600)
    cap.shot("k4-dropped-right", marks=[
        ("#railScroll #cardStyles", "Line styles now sits on the right, above Takeoff - the two share the rail", "ok"),
        ("#stage", "The empty left rail steps aside; the sheet takes the width", "ok"),
    ])
    page.locator("#dkPanelsBtn").click()
    page.wait_for_timeout(300)
    cap.shot("k5-panels-menu", marks=[
        (".dk-menu", "Every panel with its side; closed ones unchecked; 'nothing to show here yet' where the sheet has none", "ok"),
    ])
    page.locator(".dk-menu .dk-mi", has_text="Sheets").first.click()
    page.wait_for_timeout(400)
    before = page.evaluate("() => JSON.stringify(window.coreDock.layout())")
    page.reload()
    _settle(page, 2500)
    after = page.evaluate("() => JSON.stringify(window.coreDock.layout())")
    print("  layout survives a reload:", before == after)
    open_demo(cap, "C4.0-SITE-GRADING-PLAN-Rev.1.pdf")
    tab(cap, "lines")
    detect(cap)
    cap.shot("k6-after-reload", marks=[
        ("#railScroll #cardStyles", "Reloaded and reopened: the layout is where it was left (saved in this browser)", "ok"),
        ("#sheetsCard", "Sheets back from the Panels menu", "ok"),
    ])
    page.locator("#cardTakeoff .dk-more").click()
    page.wait_for_timeout(300)
    cap.shot("k7-panel-menu", marks=[
        (".dk-menu", "The same moves from the keyboard: other rail, up, down, fill, close", "ok"),
    ])
    page.keyboard.press("Escape")
    g = page.locator(".dk-rgrip").bounding_box()
    page.mouse.move(g["x"] + 3, g["y"] + 250)
    page.mouse.down()
    page.mouse.move(g["x"] - 110, g["y"] + 250, steps=8)
    page.mouse.up()
    page.wait_for_timeout(400)
    cap.shot("k8-rail-wider", marks=[
        ("#railR", "The right rail widened by its inner edge (double-click resets)", "ok"),
    ])
    st = page.locator("#stage").bounding_box()
    head = page.locator("#miniLegend .ml-head span").first.bounding_box()
    if st and head:
        page.mouse.move(head["x"] + 10, head["y"] + head["height"] / 2)
        page.mouse.down()
        page.mouse.move(st["x"] + st["width"] - 180, st["y"] + 40, steps=12)
        page.mouse.up()
        page.wait_for_timeout(300)
    cap.shot("k8b-legend-moved", marks=[
        ("#miniLegend", "The legend over the sheet dragged off the drawing to the top right; kept for next time, Reset puts it back", "ok"),
    ])
    page.locator("#dkPanelsBtn").click()
    page.wait_for_timeout(200)
    page.locator(".dk-menu .dk-mi", has_text="Reset panels").click()
    page.wait_for_timeout(500)
    tab(cap, "counts", 4000)
    cap.shot("k9-counts-default", marks=[
        ("#toolDock", "An open tool panel sits beside its tab's tools; the fill gives way to it", "ok"),
        ("#sheetsCard", "Short panels keep all of their content", "ok"),
    ])
    # below 1440 the gear lives in the account menu (responsive.css), so
    # Settings is opened by its address, which works at every width
    page.evaluate("() => { location.hash = '#settings'; }")
    _settle(page)
    page.locator("#svWorkspace").scroll_into_view_if_needed()
    _settle(page, 300)
    cap.shot("k10-settings-workspace", marks=[
        ("#svStartModule", "The tab a project opens on - the 'default loading screen'", "ok"),
        ("#svDockReset", "Reset the panels from Settings too", "ok"),
    ])


def run(cap, args):
    if getattr(args, "set", "") == "dock":
        return run_dock(cap, args)
    if getattr(args, "set", "") == "workflow":
        return run_workflow(cap, args)
    if getattr(args, "set", "") == "priced":
        return run_priced(cap, args)
    if getattr(args, "set", "") == "looks":
        return run_looks(cap, args)
    page = cap.page
    page.goto(BASE + "/#projects")
    _settle(page)
    cap.shot("01-projects", marks=[
        (".pv-tabs", "Demo plans / Your projects as the mockup's left filter rail - the same two lists and counts", "ok"),
        ("#uploadBtn", "+ New project: the one filled action, same upload handler", "ok"),
        ("#pvGrid .pv-card", "Thumbnail beside the facts. No trade chips or quantities on the card: /api/projects has none, so none are invented", "diff"),
    ])

    open_demo(cap, "C4.0-SITE-GRADING-PLAN-Rev.1.pdf")
    tab(cap, "lines")
    cap.shot("02-lines-undetected")
    detect(cap)
    cap.shot("03-lines-detected", marks=[
        ("#modTabs", "Module tabs: Plans, Lines, Counts, Areas, Earthwork, Trenching, Summary, 3D", "ok"),
        ("#ribbon .toolkit", "The old floating icon rail as a labelled ribbon: same buttons, ids, handlers and keys", "ok"),
        ("#sheetsCard", "Sheets grouped by discipline from the sheet number (C = civil). No titles: the app has no title reader", "diff"),
        ("#modCard", "This module's tools: they click the real Tools-menu buttons; pressed while the panel is open", "ok"),
        ("#cardTakeoff", "Takeoff by trade with the app's REAL trades (not the mockup's five); qty and $ still editable; trades fold", "diff"),
        ("#railFoot", "Approve & export pinned at the foot of the right rail", "ok"),
        ("#sheetFacts", "How the scale was read stays on screen (load-bearing disclosure)", "ok"),
        ("#betaBanner", "accuracy flag kept, in every view", "ok"),
    ])

    tab(cap, "plans")
    cap.shot("04-plans", marks=[
        (".pl-group", "Sheets grouped by discipline (inferred from the sheet number, and says so on hover)", "diff"),
        (".pl-card", "Thumbnail, sheet number, page and the live detect status; a click opens it in Lines", "ok"),
        ("#plMeta", "Sheets / detected / approved counts from the server", "ok"),
        (".vw-title h1", "No Team or Activity panels: no per-project members exist and the ledger records no user - left out, not faked", "diff"),
    ])
    tab(cap, "summary", 2500)
    cap.shot("05-summary", marks=[
        (".sm-table", "Qty, unit, unit price, extended, source from computeTakeoffRows() - the pass the CSV and Approve use", "ok"),
        (".seg-group", "This sheet, or every APPROVED sheet read back from the server's approved exports", "ok"),
        (".rd-card", "Bid readiness rebuilt: supplied / assumed / missing only where the app has a real signal", "ok"),
        (".sm-acts", "Export CSV, Report PDF, Bid sheet CSV, Print (PDF and bid sheet wait for an approved sheet)", "ok"),
    ])
    cap.shot("05b-summary-full", full=True)

    tab(cap, "trenching")
    cap.shot("06-trenching", marks=[
        ("#trPanel", "Pipe trench, opened by the tab through its own button (its saved inputs load)", "ok"),
        ("#trPanel .hint-more", "The long how-to folded to three lines - 'How it works' opens it; nothing deleted", "diff"),
        ("#stage", "No profile strip: the app does not read depths off profile sheets yet (typed depths only) - not faked", "diff"),
    ])
    tab(cap, "areas")
    cap.shot("07-areas", marks=[
        ("#flPanel", "Flooring rooms by floor type, auto-opened; its tools now sit above the fold", "ok"),
        ("#modCard", "Pressed = its panel is open", "ok"),
    ])
    tab(cap, "counts", 4000)
    cap.shot("08-counts", marks=[
        ("#symPanel", "Count symbols: repeated shapes on this sheet; row Adds outlined, the bulk add is the filled one", "ok"),
        ("#modCard", "Whole job and Across sheets one click away", "ok"),
    ])

    tab(cap, "earthwork", 8000)
    cap.shot("09-earthwork-cutfill", marks=[
        (".cf-bar", "The host's bar: the engine's totals and Add to takeoff (the consent step)", "ok"),
        ("#cfFrame", "The cut / fill engine itself, unchanged - restyled from outside by an injected skin", "ok"),
    ])
    tab(cap, "3d")
    cap.shot("10-3d", marks=[
        ("#cfFrame", "3D tab: the engine's own 3D view, cut red / fill blue", "ok"),
    ])

    tab(cap, "lines")
    page.locator("#toolsBtn").click()
    _settle(page, 400)
    cap.shot("11-tools-menu", marks=[
        ("#toolsMenu", "All ten tools still in one menu (the tests pin it)", "ok"),
    ])
    page.keyboard.press("Escape")

    page.locator("#settingsLink").click()
    _settle(page)
    page.locator("#svAccentChoice").scroll_into_view_if_needed()
    _settle(page, 300)
    cap.shot("12-settings", marks=[
        ("#svAccentChoice", "Accent: Green (default) or Yellow - no orange", "ok"),
        ("#svPaletteChoice", "Ground: Steel or Graphite", "ok"),
        ("#svThemeChoice", "Dark or Paper - app.js's own theme switch, unchanged", "ok"),
    ])
    cap.shot("12b-settings-full", full=True)
