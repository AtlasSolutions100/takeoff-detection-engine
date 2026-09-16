# Your own role is off limits — headed (Edge) on :8100, app.js v=176

On the first day Settings -> Accounts was live the owner clicked the role
button on his own row and demoted himself to client; the page then vanished
from under him and only another staff member (or the box) could put it
back. Now the server refuses any role change on the caller's own account
(400 "You cannot change your own role. Ask another staff member to do it.")
and the UI disables the button on your own row with the same sentence.

Throwaway store in per-user mode with two throwaway staff logins (removed
after). Driver: scratchpad `live_selfrole.py`; readback `readback.json`.

| check | readback |
|---|---|
| own row | role button "Staff", disabled, title "You cannot change your own role. Ask another staff member to do it."; Remove disabled too |
| another staff's row | role button enabled, title "Click to make this a CLIENT account …" |
| API self demote | 400 with the same sentence (the one console error is that refusal) |
| afterwards | /api/me still reports staff |

Pinned by `tests/test_users_api.py` (self demote and self re-state both 400;
another staff can still demote the boss, who loses the page at once; the
remaining staff cannot demote itself) and `tests/test_accounts_settings.js`.
