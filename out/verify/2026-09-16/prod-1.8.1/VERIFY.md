# 1.8.1 on the live site — headed (Edge) on core.workoptional.studio

Deployed by the v1.8.1 tag (CI run 35111606076, deploy job 55 s); the box
reports `TAG=v1.8.1` and `takeoff-app-1` healthy on
`ghcr.io/atlassolutions100/takeoff-detection-engine:v1.8.1`. Driver:
scratchpad `live_prod_181.py`, signed in as a throwaway STAFF account created
on the box with `users add --staff` and removed with `users remove` right
after (22 accounts left, none of them throwaway). Readback `readback.json`.
No page errors, no console errors, no 5xx. No demo card was opened.

| check | readback |
|---|---|
| build served | `app.js?v=175`, `style.css?v=86`; `#trimBtn` present with its title; the keyboard help lists <kbd>T</kbd>; `#svNavAccounts` present |
| who am I | staff, accounts on, no must-change |
| Settings → Accounts | nav link, group and section shown; the table lists every account (23 at the time, 3 staff incl. the throwaway) |
| create from the browser | `smoke-181-client@wo.test` created, "account created for …", a 12-character one-time password shown, the row listed (`P3-created-annot.png`) |
| remove from the browser | first press arms, second press: "smoke-181-client@wo.test removed", row gone (`P4-removed-annot.png`) |

The committed captures have every other account's sign-in name replaced by
dots in the page before the screenshot, and the one-time password replaced
by "(12 characters, masked)"; the raw readback carries counts only.
