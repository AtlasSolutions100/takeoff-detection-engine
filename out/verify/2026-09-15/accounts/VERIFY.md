# Accounts from inside the app (Settings -> Accounts) — headed (Edge) on :8100

The dev service ran on the throwaway job store in per-user mode: two
throwaway accounts made with the CLI (`staff@dev.test` staff,
`client@dev.test` client), deleted again after the run. Driver: scratchpad
`live_accounts.py`; readback `readback.json`. The only console errors are
the two expected refusals (401 for the removed client's sign-in, 404 for a
client account calling /api/users). app.js v=175, style.css v=86.

| step (real mouse/keyboard) | readback |
|---|---|
| staff signs in, opens Settings, `#accounts` | nav link, group and section shown; "2 account(s) on this server, 1 staff. You are signed in as staff@dev.test."; own row tagged **you**, its Remove disabled (`A1-accounts-list-raw.png`) |
| type `boss@dev.test`, tick Staff, Create account | "account created for boss@dev.test"; the one-time password box appears with a 12-character password and Copy; the new row reads Staff / "temporary — must change at next sign-in" (`A2-boss-created-annot.png`) |
| Reset password on the client | "new temporary password for client@dev.test"; the row flips to temporary |
| click the client's role button twice | Client -> Staff -> Client (server-side toggle, table re-read each time) |
| Remove on the client | first press arms it: "Really remove?" (`A3-remove-armed-annot.png`); second press: "client@dev.test removed", row gone (`A4-client-removed-annot.png`) |
| demote the boss, then look at own role button | disabled, title "The last staff account cannot be demoted. Make someone else staff first." |
| sign out; sign in as the removed client | login page: "That email and password do not match." |
| sign in as boss@dev.test with the one-time password | sent to /change-password ("Choose your password") (`B1-boss-must-change-annot.png`); after choosing one, lands on Projects, whoami "boss@dev.test · staff", Accounts section shown, "2 account(s) … 2 staff" (`B2-boss-signed-in-annot.png`) |
| boss creates `newclient@dev.test`; sign in as it, choose a password, open Settings | no Accounts link, group or section; `fetch('/api/users')` -> 404 (`N1-client-no-accounts-annot.png`) |

Server rules pinned by `tests/test_users_api.py` (through the ASGI
middleware): client -> 404 on every account route; duplicate names refused
case-insensitively; whitespace names, short and shared-default passwords
refused; cannot remove yourself; the last staff account can be neither
removed nor demoted; a removed account's sign-in and old cookie die; reset
issues a fresh temporary password and kills the old one; with no accounts
the gate answers 409 and names the CLI command for the first account.
