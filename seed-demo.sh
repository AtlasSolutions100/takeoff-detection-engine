#!/usr/bin/env bash
# Put the DEMO PLANS on a hosted box, so the Demo tab has something to show.
#
#   TAKEOFF_EMAIL=you@example.com TAKEOFF_PASSWORD=... ./seed-demo.sh https://core.workoptional.studio
#   ./seed-demo.sh https://core.workoptional.studio        # prompts for both
#
# Runs from a machine that HAS the plan corpus - it uploads from here to there.
# Safe to re-run: uploads are content-addressed, so a plan already on the box
# resolves to the same job instead of a second copy.
#
# ---------------------------------------------------------------------------
# THREE THINGS CHANGED HERE ON 2026-09-01, each for a reason worth keeping.
#
# 1. IT LOGS IN LIKE A PERSON. This used to send HTTP Basic (`-u x:PASSWORD`)
#    against a single site password. Basic auth was REMOVED from the server on
#    2026-08-25 - see the comment above _UI_PASSWORD in server.py, "there is no
#    sign out for a Basic prompt" - and a hosted box with accounts asks for an
#    email and a password on a real login page. Every request this script made
#    would have come back 401 no matter what password was typed into it.
#
# 2. IT SEEDS EXACTLY demo-plans.json, and nothing else. The old list was
#    hand-maintained beside the manifest and had drifted: it carried FIVE
#    client documents from client_meet/beaumont - the Barn, Guide and Thielen
#    residences - and uploading a client's drawings to a public-facing box is
#    not a thing a seeding script should be able to do by accident. The
#    manifest is the single source for what a visitor may see; this reads it.
#
# 3. IT DOES NOT PRE-DETECT. It used to detect every seeded page so a demo
#    opened instantly. That is now wasted work AND the wrong idea: opening a
#    demo sweeps everything derived from the PDF on purpose (POST
#    /api/demo/{name}/open) so the visitor watches the engine run instead of
#    inheriting a cache. Uploading is all the box needs - the upload registers
#    the job, and _demo_source finds the plan by its display name from there.
#    Pass --detect to pre-warm anyway, for the non-demo case where somebody
#    wants a warm cache before a meeting.
# ---------------------------------------------------------------------------
set -u

DETECT=0
ASSUME_YES=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --detect) DETECT=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
HOST="${ARGS[0]:-}"
if [ -z "$HOST" ]; then
  echo "usage: $0 https://host [plan name]... [--detect] [--yes]" >&2
  echo "       set TAKEOFF_EMAIL and TAKEOFF_PASSWORD to run unattended" >&2
  echo "       naming plans seeds only those; the default is every plan" >&2
  echo "       in demo-plans.json" >&2
  exit 2
fi
# Anything after the host narrows the run to those manifest plans. That
# exists because seeding is also a PUBLISHING act - see the notice below -
# and the operator needs a way to put up only some of the cards.
WANT=("${ARGS[@]:1}")
HOST="${HOST%/}"

EMAIL="${TAKEOFF_EMAIL:-}"
if [ -z "$EMAIL" ]; then
  printf 'email for %s: ' "$HOST" >&2
  read -r EMAIL
fi
PW="${TAKEOFF_PASSWORD:-}"
if [ -z "$PW" ]; then
  printf 'password for %s: ' "$EMAIL" >&2
  read -rs PW; echo >&2
fi
[ -z "$EMAIL" ] && { echo "no email given" >&2; exit 2; }
[ -z "$PW" ] && { echo "no password given" >&2; exit 2; }

# Built here rather than inline so the quoting survives: the body is JSON
# and the shell must not touch it.
PYGEN='import json, os, sys
sys.stdout.write(json.dumps({"username": os.environ["EMAIL"],
                             "password": os.environ["PW"]}))'

# Self-signed/local certs: only skip verification for a loopback target.
K=""
case "$HOST" in https://localhost*|https://127.0.0.1*) K="-k";; esac

# The session cookie lives in a file only this user can read, and it is
# removed on the way out however the script exits - a signed session is a
# bearer credential and has no business surviving the run.
JAR="$(mktemp)"
chmod 600 "$JAR"
trap 'rm -f "$JAR"' EXIT INT TERM

# X-Core-Client: the marker every page of the app sends with a write (webui/client.js); the server refuses a write
# without it (server.py _client_gate), so this script says it too
CURL=(curl -sS $K -b "$JAR" -c "$JAR" --max-time 1800 -H 'X-Core-Client: 2')

# ---- sign in ---------------------------------------------------------------
# /api/login reads a JSON BODY - `body = await request.json()` - not a form.
# Posting form fields returns 500, not 401: the handler dies inside json.loads
# before it ever looks at the credentials. This script did exactly that, and a
# live run against a prod-shaped box is what turned the 500 up.
#
# The password reaches python through the environment and curl through stdin,
# so it is never an argument - argv is readable by every process on the box.
json_body="$(EMAIL="$EMAIL" PW="$PW" python -c "$PYGEN" 2>/dev/null)"
[ -z "$json_body" ] && { echo "could not build the sign-in body" >&2; exit 1; }

code="$(printf '%s' "$json_body" | curl -sS $K -c "$JAR" \
  -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  --data @- "$HOST/api/login" 2>/dev/null)"
unset json_body
case "$code" in
  200|204|302|303) ;;
  401|403) echo "sign-in refused ($code) - check the email and password" >&2; exit 1 ;;
  429)     echo "too many sign-in attempts; wait five minutes" >&2; exit 1 ;;
  500)     echo "the server errored on sign-in (HTTP 500). It did not reach" >&2
           echo "the password check at all - is this box running a build" >&2
           echo "older than the JSON login body?" >&2; exit 1 ;;
  000)     echo "cannot reach $HOST" >&2; exit 1 ;;
  *)       echo "sign-in failed with HTTP $code" >&2; exit 1 ;;
esac

me="$("${CURL[@]}" "$HOST/api/me" 2>/dev/null)"
case "$me" in
  *'"user"'*) printf 'signed in to %s as %s\n' "$HOST" "$EMAIL" ;;
  *) echo "signed in, but /api/me did not answer with a user - stopping" >&2; exit 1 ;;
esac
case "$me" in
  *'"must_change":true'*)
    echo "this account still has its issued password and must choose a new" >&2
    echo "one before anything else works - sign in at $HOST/login first." >&2
    exit 1 ;;
esac
# Seeding depends on the uploader being STAFF: server.py upload() only turns a
# manifest plan into a SHARED demo row for a staff account. Run as anyone else
# this uploads private copies and the Demo tab stays as empty as it started.
case "$me" in
  *'"staff":true'*) printf 'staff account confirmed\n\n' ;;
  *) echo "$EMAIL is not staff on this box. Seeding would upload the plans" >&2
     echo "as private documents and no demo card would light up." >&2
     exit 1 ;;
esac

# ---- what to seed: the manifest, and only the manifest ---------------------
HERE="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$HERE/demo-plans.json"
[ -f "$MANIFEST" ] || { echo "no demo-plans.json beside this script" >&2; exit 1; }
# Git Bash reports paths as /c/Users/... and the python on PATH is a native
# Windows build, which cannot open one - json.load raised FileNotFoundError,
# 2>/dev/null swallowed it, and the script reported "could not read the
# manifest" about a file it had just confirmed exists. Hand python a path
# its own OS understands; bash keeps using the original for find and [ -f ].
if command -v cygpath >/dev/null 2>&1; then
  MANIFEST_PY="$(cygpath -m "$MANIFEST")"
else
  MANIFEST_PY="$MANIFEST"
fi

# The corpus lives beside the repo. client_meet is CLIENT work and is never
# searched - see note 2 in the header.
CORPUS="$HERE/../TakeoffPro AI"
[ -d "$CORPUS" ] || { echo "plan corpus not found at $CORPUS" >&2; exit 1; }

names="$(python -c "
import json,sys
d=json.load(open(r'''$MANIFEST_PY''', encoding='utf-8'))
for n in d['plans']: print(n)
" 2>/dev/null | tr -d '\r')"
[ -z "$names" ] && {
  echo "could not read $MANIFEST_PY" >&2
  echo "python said:" >&2
  python -c "
import json
d=json.load(open(r'''$MANIFEST_PY''', encoding='utf-8'))
print(len(d['plans']))" >&2
  exit 1
}

# Resolve every name to a file BEFORE uploading anything, so a missing plan
# stops the run instead of half-seeding the box.
declare -a FILES=() MISSING=()
while IFS= read -r name; do
  # The CR is already gone - it is stripped with tr where the list is
  # captured. Do NOT try to strip it here with ${name%...}: two attempts to
  # write the escape into this file lost it in transit, and what landed was
  # ${name%$''}, which strips an empty suffix and does nothing at all. The
  # symptom is every plan but the LAST reported missing, because command
  # substitution eats the final newline and takes the final CR with it.
  [ -z "$name" ] && continue
  if [ "${#WANT[@]}" -gt 0 ]; then
    _pick=0
    for w in "${WANT[@]}"; do [ "$w" = "$name" ] && _pick=1; done
    [ "$_pick" -eq 1 ] || continue
  fi
  hit="$(find "$CORPUS" -type f -name "$name" -not -path "*/Ryno Construction/*" -print -quit 2>/dev/null)"
  if [ -n "$hit" ]; then FILES+=("$hit"); else MISSING+=("$name"); fi
done <<< "$names"

if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "not found in the corpus - nothing uploaded:" >&2
  for m in "${MISSING[@]}"; do echo "  $m" >&2; done
  exit 1
fi

if [ "${#FILES[@]}" -eq 0 ]; then
  echo "nothing selected - the names given match no manifest plan" >&2
  exit 1
fi

# ---- say what this publishes, before it publishes it -----------------------
# A demo card is visible to EVERY account on the box. On core.workoptional.studio
# that is twenty accounts, eighteen of them contractors who compete with each
# other. Uploading here is therefore not just a file transfer: as of the
# manifest-name rule in server.py upload(), a staff upload of a manifest plan
# lands as a SHARED row on purpose, which is what makes the card work at all.
# So the list gets read back before anything moves.
echo "This publishes ${#FILES[@]} plan(s) to $HOST as DEMO cards."
echo "A demo card is readable by every account on that box, not just yours:"
for f in "${FILES[@]}"; do echo "    $(basename "$f")"; done
echo
if [ "$ASSUME_YES" -ne 1 ]; then
  if [ -t 0 ]; then
    printf 'publish these? [y/N] ' >&2
    read -r _ans
    case "$_ans" in y|Y|yes|YES) ;; *) echo "nothing uploaded." >&2; exit 1 ;; esac
    echo
  else
    echo "not a terminal and --yes not given - nothing uploaded." >&2
    exit 1
  fi
fi

echo "seeding ${#FILES[@]} demo plan(s) to $HOST"
[ "$DETECT" -eq 1 ] && echo "(--detect: pre-warming, though a demo open sweeps it)"
echo

ok=0; bad=0; t_all=$(date +%s)
for f in "${FILES[@]}"; do
  name="$(basename "$f")"
  printf '%s\n' "$name"
  t0=$(date +%s)
  up="$("${CURL[@]}" -F "file=@$f" "$HOST/api/upload")"
  job="$(printf '%s' "$up" | sed -n 's/.*"job_id":"\([^"]*\)".*/\1/p')"
  if [ -z "$job" ]; then
    printf '  UPLOAD FAILED: %s\n\n' "$(printf '%s' "$up" | head -c 160)"
    bad=$((bad+1)); continue
  fi
  printf '  uploaded in %ds (job %s)\n' "$(( $(date +%s) - t0 ))" "$job"
  ok=$((ok+1))

  if [ "$DETECT" -eq 1 ]; then
    page="$(python -c "
import json
d=json.load(open(r'''$MANIFEST_PY''', encoding='utf-8'))
print(d['plans'].get(r'''$name''',{}).get('page',0))
" 2>/dev/null | tr -d '\r' || echo 0)"
    "${CURL[@]}" -o /dev/null -X POST "$HOST/api/job/$job/page/$page/detect"
    t1=$(date +%s); st=""
    while [ $(( $(date +%s) - t1 )) -lt 1500 ]; do
      st="$("${CURL[@]}" "$HOST/api/job/$job/page/$page/status" \
            | sed -n 's/.*"status":"\([^"]*\)".*/\1/p')"
      case "$st" in done|idle|error) break;; esac
      sleep 5
    done
    printf '  page %-3s %s in %ds\n' "$page" "${st:-no-reply}" "$(( $(date +%s) - t1 ))"
  fi
  echo
done

printf 'uploaded %d plan(s), %d failed, %ds total\n' "$ok" "$bad" "$(( $(date +%s) - t_all ))"
if [ "$bad" -eq 0 ]; then
  echo "the Demo tab on $HOST should now show all $ok."
else
  exit 1
fi
