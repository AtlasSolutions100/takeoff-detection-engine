# CORE Takeoff - the round-3 redesign (core-redesign/)

A standalone copy of the core + cut/fill merge, redesigned to the round-3
"multi-trade" mockups. **Not the product.** The live product is
`detection-engine/` (23+ clients); the merge fork is `core-cutfill/` (the
cut/fill session develops there). Nothing here is deployed.

| | |
|---|---|
| Source | `core-cutfill` dev @ `5f6fdc2` (2026-09-25), cloned with `core.autocrlf=false` |
| Branch | `redesign` (branch `dev` = the fork as cloned) |
| Remote | `fork` -> `../core-cutfill`, **fetch only** (push URL disabled) |
| Port / store | `:8600`, `C:/Users/kobys/core-redesign-jobs` (ledger, accounts, branding and cut/fill projects all live under it) |
| Start | `bash core-redesign/run-service.sh`, or launch.json entry `core-redesign` |
| .env | fresh, **no secrets** (no password = no sign-in on this laptop; Analyze reports "not configured") |

## Mockups built to

`design-mockups/2026-09-25/` round 3 (README there): `8-multitrade-steel/`
(71-80) is the base; Koby's download (`Downloads/archive`) is those ten plus
three night-crew alternates (62-counts, 65-lines, 67-summary) and the slate
cut/fill line-review screen (52-lines).

## Design system (webui/tokens.css)

* **Two independent switches** in Settings -> Appearance, stored in this
  browser: the ACCENT `html[data-accent=green|yellow]` (`core-accent`,
  default green) and the GROUND `html[data-palette=steel|nightcrew]`
  (`core-palette`, default steel blue-black `#0a1219` chrome / `#0f1922`
  cards; "Graphite" = the night-crew ground with uppercase titles), beside
  app.js's own dark / paper theme (`core-theme`, the ◐ button).
* **No orange.** The steel mockups' safety orange was the first build's
  accent; Koby (2026-09-26): "Why are we going orange on the accent
  buttons? Go green or the yellow in the mockups." Green `#3fa873` (the
  brand the product already wears) is the default, equipment yellow
  `#f5c400` the alternative.
* Grounds tuned to the RENDERED mockups (sampled pixels), which are bluer
  and darker than the README hexes.
* **Cut red `#e5533d` / fill blue `#3b82f6`** - flipped from the old tokens.
* **One accent**, only on the primary action, the active tab/tool, the
  selected thing. Status colours are separate: supplied / verified = TEAL
  `#2fb5a2` (not the accent green, so "verified" never reads as
  "selected"), assumed = amber, missing = red.
* **Text on the accent is dark ink** (green 5.7:1, yellow 11.5:1). White on
  the green is 2.97:1 - below WCAG AA at button size. `--t-accent-ink` is
  the one-line switch if white is wanted anyway. Paper theme: deep green
  `#1f7a4d` with white text (5.3:1).
* IBM Plex Sans (labels) + IBM Plex Mono (every quantity, tabular), already
  self-hosted - no new font files, works offline.

## The frame (shell.css + shell.js)

```
+--------------------------------------------------------------------------+
| CORE Takeoff | project . sheet v | Plans Lines Counts Areas Earthwork     |
|              |                   | Trenching Summary 3D   [Detect] ...   |
+--------------------------------------------------------------------------+
| Select  Measure | Draw Extend Split Cut Flatten Join Trim | Name ...      |  ribbon
+-------------+--------------------------------------------+---------------+
| Sheets      |                                            | Sheet         |
|  C  Civil   |              the plan sheet                | Takeoff       |
|   C4.0 ...  |           (light, on the dark table)       |  by trade     |
| <module     |                                            | Questions     |
|  card>      |                                            | Approve       |
+-------------+--------------------------------------------+---------------+
| status: sheet facts (how the scale was read) . messages . legal          |
+--------------------------------------------------------------------------+
```

Every existing node keeps its id and handler; the redesign MOVES nodes, it
does not rebuild behaviour. What each module shows:

| Tab | Left rail | Centre | Right rail |
|---|---|---|---|
| Plans | - | sheet grid of this document (numbers, thumbnails, status, Detect all) | - |
| Lines | Sheets; Line styles; Sheet legend; Callouts | canvas + ribbon | Sheet, Takeoff, Questions |
| Counts | Sheets; Count symbols; Whole job; Across sheets | canvas | same |
| Areas | Sheets; Flooring; Wall panels; Areas (square feet) | canvas | same |
| Earthwork | - | the cut/fill engine (embedded), or Structure dig on the canvas | cut/fill totals + Add to takeoff |
| Trenching | Sheets; Pipe trench | canvas | same |
| Summary | - | takeoff by trade, unit price, extended, source; bid readiness | - |
| 3D | - | the cut/fill engine's 3D view (or the flooring rooms in 3D) | - |

## The dock (dock.js + dock.css, 2026-09-26)

Koby: "sheets should be closable, this lines tab should be closable, line
styles I guess will need to take up most of the screen ... drag and drop
[panels] to be on top of another or on the right or left side or customize
your default loading screen".

* Every rail card is a **panel**: Sheets, the tab's tools (Lines / Counts
  / ...), Sheet legend, Areas, Line styles | Sheet, Takeoff, Bid view,
  Questions. Each head carries **... (options)** and **x (close)**; the
  ribbon's **Panels** menu lists them all (side, shown / closed) and resets.
* **Drag a head** to either rail, above or below any panel (6px threshold,
  a ghost chip and a drop line; Esc cancels; the click that ends a drag is
  swallowed so a title does not also fold). The ... menu does the same
  from the keyboard.
* **Sizing**: Line styles fills the left rail, Takeoff the right. The rest
  take their natural height, capped at 42% and scrolling inside, with a JS
  floor: all of a short content, 104px of a long one (Sheets 140). An open
  tool panel, which travels with the module card, gets up to 68%, and the
  fill yields to it. A rail whose minimums outgrow it scrolls, so nothing
  ends up unreachable. Drag a panel's bottom edge for a fixed height, or
  the right rail's inner edge for its width. An empty left rail steps
  aside.
* **Saved per browser**: `core-dock-v1` (sides, order, closed, sizes,
  fills) and `core-rail-r`. Settings -> Appearance -> **Workspace**:
  "Open projects on" (`core-start-module`, the default loading screen:
  the last tab or a fixed one) and "Reset panels".
* Contract: nodes are MOVED (never cloned); `hidden` stays app.js's;
  closing is a class. Dragging is off under `(hover:none)` or below
  1024px, where the rails are drawers (responsive.*).

## Team, roles and the audit log (team.js + server, 2026-09-26)

* Roles are **admin / estimator / viewer**. Staff always meant admin, and
  old records are read by their staff flag. The **viewer gate** sits in the
  auth middleware: any non-read under /api or /cutfill/api is a 403 except
  sign-in, sign-out, change-password and feedback.
* **Team page** (header icon, account menu, `#team`, Settings -> Team):
  - admins: members with role select, projects, last active; reset password
    (one-time, shown once); remove (two presses, never yourself); the
    activity feed.
  - everyone else: their own row, the role's meaning, and the roles card.
  - no accounts: how to create the first admin on the server.
* **Audit log** (`takeoff_engine/audit.py`): who uploaded, detected,
  approved, reset, deleted or changed an account. It never holds a
  password, cookie, file content or path. Per project it shows in the
  Plans Activity card, and names the actors to admins only.
* A **viewer** sees a Read-only chip in the header. "+ New project" and
  demo opens stand down; the server refuses them regardless.
* Verified live on a throwaway :8614 instance (scratch store, CLI-made
  test accounts, one-time passwords blurred in every capture).

## Honesty rules the new pages follow (nothing invented)

From the data-source map (server.py, 2026-09-26):

* **Sheet numbers** come from `/api/job/{id}/triage` (`sheet_no`, text layer,
  all pages). **Titles do not exist** except an ungraded AI read, so the sheet
  list shows the number and the page ("C4.0 . page 1"), never a made-up
  title. Grouping by discipline is by the sheet-number prefix letter - an
  inference, labelled as one; unnumbered pages get their own group.
* **Project cards** show what `/api/projects` has: thumbnail, pages,
  detected, approved, last activity. Trade chips and headline quantities
  come only from approved pages (and cut/fill totals where computed); where
  nothing is approved the card says so instead of showing a number.
* **Team and activity are real, not mocked**: they come from the accounts
  (users.json), the roles, and the audit log added 2026-09-26. Before any
  account exists, the pages say so instead of showing a team. There are
  still no per-project member lists: access is the owner plus admins, and
  that is exactly what the Access card says.
* **Questions** = the engine's own "Check these" queue (computed in the
  browser, per sheet) and the cut/fill engine's questions. No server count.
* **Bid readiness** only lists inputs the app really knows (scale source and
  confirmation, work area, cut/fill surfaces/datum, trench depths, schedules
  read, floor types assigned); anything it cannot see is "not checked", never
  "supplied".
* **Landing page**: no customer logos, no invented metrics. Figures only from
  demo-plans.json `shows` (written from live runs).
* UI copy rules (memory `ui-copy-rules`): no client names or job numbers on
  screen, warnings short and plain, load-bearing detail folded, never
  deleted. The accuracy flag (the BETA notice until 2026-10-01) stays on every surface.

## What a merge of the fork needs here (2026-10-04: empty takeoff, bids by e-mail, wall panels)

`app.js`, `style.css` and any script the fork adds (`wallpanels.js` / `.css`) are the fork's files, whole. The frame
adds only what its own layout needs, and `tests/test_redesign_three_features.js` pins each of these:

* **A new tool** goes in `shell.js` MODULES (`acts`, `owns`), `PANEL_MOD` and `BTN_PANEL`. Wall panels is in Areas and
  opens before Detect (its button shows whenever a plan set is open). `yields`: the tab never opens its own panel
  (Flooring) over an open Wall panels panel, and a Flooring the tab opened by itself steps down for it.
* **A new child of the Projects page** needs a row in the named-area grid (`shell.css`, and the phone's in
  `responsive.css`): the From e-mail list is `inbox`. Buttons that read as links there need the frame's button box
  taken off.
* **New activity actions** need words in `team.js` PHRASE (and in `shell.js` ACT_PHRASE when they name a project).
* **Anything app.js puts in `.approve-bar` or `.links-bar`** travels with those bars into the right rail's foot. The
  links bar shows while it holds a visible link OR button.
* **Bid readiness** reads `tkReadiness()` (the takeoff row) and GET /approved `earlier` / `kept` (an earlier version's
  approval: information, never "missing").
* **A helper's name in `server.py`** must not be one the fork uses as a local: `tests/test_merge_shadow.py` holds that
  (the merge joined a helper `_n` with a loop variable `_n`, and deleting a project answered 500).
* **Fork tests that meet the frame** are adjusted here, never on the fork: the route count (`test_orphan_job.py`), the
  buster pin (`test_scale_verdict_ui.js`), the viewer gate's sentence (`test_wp_acts.py`), the last-seen file
  (`test_inbound_email.py`).

The second merge of 2026-10-04 (the fork's fix round: the account file fails closed) added three:

* **An account function of the redesign** - anything in `takeoff_engine/users.py` that changes `users.json` and carries
  a role (`set_password`, `add_user`, `set_staff`, `set_role`, the command line) - is written the fork's way: under
  `@_writing` (or inside `with _one_writer():`), the roster read with `_fresh()` and never `load()`, a name looked up
  with `find(name, store)`. `load()`'s answer is this process's memory and is never changed in place. A file that is
  there and can not be read is never "no accounts": such a change is refused (`Unreadable`), nothing is written, and
  the page gets a 503. `tests/test_redesign_account_file.py` holds each function, the routes and the storm.
* **The gate judges a request on one read of the roster.** `_SessionAuth._gate` takes the role from the roster the
  session was just checked against (`role_of(user, _accounts)`). Read a second time, an account removed in between
  had no role, and no role is not "viewer": a viewer's last request went through as a write.
* **`app.js` is named one past the fork's at every merge that changes it** (313 after the fork's 312), so a browser
  that holds the redesign's earlier number is handed the new file; `tests/test_redesign_three_features.js` pins it.

## The embedded cut/fill engine

`webui/cutfill/*` are UPSTREAM files the cut/fill session re-copies from
`cutfill-engine/`. They are not edited here. The host restyles the
same-origin iframe from outside: tokens.css is already linked by the embed,
and `shell.js` injects `webui/cutfill-skin.css` into the iframe on load. A
future `git fetch fork` + merge of their copies therefore drops in cleanly.

## Verification

`tools/redesign_capture.py <set>` drives the system Edge (Playwright,
headless) against the live :8600 server and writes
`out/verify/<date>/redesign/<set>/` (screens + capture-log.json with console
errors). `before/` = the fork's UI at 5f6fdc2 on the same demo plans.
Demo plans open fresh, so every detected screen is detected live.
