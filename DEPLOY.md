# Deploying the review UI

One container, one instance. `JOBS` lives in process memory and every artifact
lives on the mounted volume, so **a second replica would hold half the state**
and the UI would 404 at random. Scale the box up, never out, until the job
store moves somewhere shared.

## Sizing, and why

| | | why |
|---|---|---|
| RAM | **32 GB**, 16 GB works | 4 workers — a heavy sheet peaks at multiple GB. Measured 2026-08-25: the whole 20-page demo seed completed at **4 workers / 12 GB** with three heavy sheets detecting at once and no OOM. Four heavy sheets at once was not tested, so 16 GB is the number to trust. `TAKEOFF_WORKERS` is the dial. |
| vCPU | 8 | one core per worker plus the web process |
| disk | 160 GB | the job cache was already 1.3 GB after a handful of documents, and it is what makes a demo instant instead of 90 s a sheet |

**Any Ubuntu 24.04 box with Docker works** — nothing below is
vendor-specific. What does not work is a small PaaS instance with an ephemeral
filesystem: losing `/data/jobs` means every demo is cold again.

Host notes, because they differ in exactly one place that matters:

| | |
|---|---|
| Hetzner | takes your SSH key at create time, has a cloud firewall UI. Strict on non-EU cards. |
| Contabo, Hostinger | **email you a root password instead**, and have no cloud firewall by default. Do step 1b below, and use `ufw`. Ordinary consumer payment methods. |
| DigitalOcean, Vultr, Lightsail | SSH key at create time, cloud firewall available. |

## Provisioning a box, start to finish

Order matters in exactly one place: **the DNS record must resolve before the
first `docker compose up`**, because Caddy requests the certificate on boot and
a failed request backs off before it retries.

**1. The server.** Ubuntu 24.04, 8 vCPU, 16 GB or more, 160 GB or more of
real disk. Note the IPv4 address. If the provider offers an SSH-key field in
the create dialog, use it and skip 1b.

**1b. Only if the provider emailed you a root password** (Contabo, Hostinger,
most budget VPS hosts). The box is on the public internet with password login
enabled from the moment it boots, and these addresses get scanned within
minutes, so this is the first thing to do — before Docker, before
anything. From your machine:

```bash
ssh-copy-id root@THE_IP
```

Then, on the box, turn password login off. **Do it with a drop-in file, not
by editing `sshd_config`.** Ubuntu cloud images ship
`/etc/ssh/sshd_config.d/50-cloud-init.conf` which re-enables passwords, the
`Include` sits at the top of the main config, and for this keyword the first
value wins — so editing the main file changes nothing. Measured on
`ubuntu:24.04`: after `sed`-ing the main file, `sshd -T` still reported
`passwordauthentication yes`; a `00-` drop-in reported `no`.

```bash
echo 'PasswordAuthentication no' > /etc/ssh/sshd_config.d/00-hardening.conf
```

```bash
sshd -T | grep -i passwordauth && systemctl reload ssh
```

That `grep` must print **`passwordauthentication no`** before you trust it.

Keep the current session open while you test a second `ssh root@THE_IP` in
another terminal. If the new one fails, you still have the old one to undo it.

**2. DNS.** At the registrar, an `A` record for the name you want, pointing at
that IPv4. Verify it from your own machine before going further, because
everything after this depends on it:

```bash
dig +short core.workoptional.studio
```

**3. Base setup**, as root on the box:

```bash
apt update && apt upgrade -y && curl -fsSL https://get.docker.com | sh
```

```bash
adduser --disabled-password --gecos "" deploy && usermod -aG docker deploy
```

**4. A deploy key**, generated on your machine so the private half never
travels:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/takeoff_deploy -N "" -C github-actions
```

```bash
ssh-copy-id -i ~/.ssh/takeoff_deploy.pub deploy@core.workoptional.studio
```

**5. Firewall.** Inbound **22, 80, 443** only. 80 is not optional —
Caddy uses it for the certificate challenge and for the redirect to 443. On a
host with no cloud firewall, use `ufw` — note that `ufw` denies
incoming by default once enabled, so allow SSH *first* or you lock yourself out:

```bash
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
```

**6. Nothing to do for the registry.** The repo is private, so the GHCR
package is private, but the box needs no credential of its own: the deploy
workflow lends it the run's own `GITHUB_TOKEN`, which can read packages owned
by this repo, expires with the job, and is logged out again afterwards. Nothing
persists in `~/.docker/config.json`. Verified on the v1.0.0 deploy
(`Login Succeeded`, pulled, recreated, healthy).

**7. First boot**, as `deploy`:

```bash
git clone https://github.com/AtlasSolutions100/takeoff-detection-engine.git
```

```bash
cd takeoff-detection-engine && cp .env.example .env
```

Fill in `TAKEOFF_UI_PASSWORD` and `SITE_HOST`, then:

```bash
docker compose up -d && docker compose logs -f caddy
```

**8. GitHub repo secrets**, so CI can deploy: `DEPLOY_HOST` (the hostname),
`DEPLOY_USER` (`deploy`), `DEPLOY_SSH_KEY` (the **private** key from step 4,
the whole file including its BEGIN and END lines), `DEPLOY_DIR`
(`/home/deploy/takeoff-detection-engine`).

**9. Seed the demo plans**, from your own machine, which is where the corpus
lives: `./seed-demo.sh https://core.workoptional.studio`.

## Signing in

There is a **login page** at `/login` and a signed session cookie. No HTTP
Basic prompt: a browser caches Basic credentials and replays them forever, so
the moment a password changes the user is locked out with no way to clear it
and no sign-out button. That happened here on 2026-08-25.

Two modes:

| | |
|---|---|
| `users.json` on the volume | each person signs in with their own email and password; `TAKEOFF_UI_PASSWORD` is dead and can be deleted from `.env` |
| no `users.json` | any username plus `TAKEOFF_UI_PASSWORD` |

Manage accounts with:

```bash
docker compose exec app python -m takeoff_engine.users add someone@example.com
```

Add `--staff` for your own team: staff see every document on the box and can
delete any of them, including a client's upload after a meeting.

The container **refuses to start** when there is neither an account nor a
password - it would have nobody to authenticate, on a public box holding client
plan sets. Verified: no password and no accounts exits 1, a password under 8
characters exits 1, and no password WITH accounts boots and logs
`per-user logins active`.

Sessions are signed with `session.key` beside the job store, so they survive a
redeploy. Delete that file to sign everybody out at once.

## Before shipping this build

Two things the box holds today were made by code this build replaces, and a
deploy does not undo data.

**1. Shared rows over a client's upload.** An unseeded box used to scan its
own uploads for the demo plans and register a *shared* row (owner None) over
a client's PDF that happened to wear a demo name. Those rows are still
served - a staff seed does not displace them, the demo picks the lowest job
id - and only deleting them repairs it. On the box, dry run first and read
both sections of the report, then purge:

```bash
docker compose exec -T app python tools/demo_rows_audit.py --store /data/jobs
```

```bash
docker compose exec -T app python tools/demo_rows_audit.py --store /data/jobs --purge && docker compose restart app
```

Nothing is written without `--purge`, and `--store` is required on purpose.
If the purge removed a row that was our own seed of the same bytes a client
uploaded, re-run `seed-demo.sh` afterwards.

**2. Single-password cookies.** Sessions issued before accounts existed carry
no user and are now refused once `users.json` is present; anyone still on one
lands on the login page. No action needed, unless you want everybody signed
out at once: delete `session.key` beside the job store.

`provision_demo.py` never rewrites a row that has an owner - client or
staff, whatever the row is named - and prints each refusal. Its ownership
pass touches only owner-less rows (the pre-login shared marker): demo cards
and the shared prefixes stay shared, and the rest move to staff **only under
`--rewrite-owners`**, after it prints the diff and gets a y/N (`--yes` when
there is no terminal, which `docker compose exec -T` never has). A bare run
is a dry run: accounts and the roster are written, `registry.json` is not.

Demo cards (2026-10-01): only the two shared plan sets ship (Loves Lane civil
sheets, Belle Farms). An owner-less row is open to every account only when it
is a demo card or one of `shared_names` in `demo-plans.json` (exact names,
case aside); any other
owner-less row (a card removed from an older manifest) is staff-only by the
server's rule, hidden from clients and never deleted. No server step is
needed for that: deploying the new image is enough.

Customer list (2026-10-01): `provision_demo.py` no longer carries the
customers' addresses - the image shipped them. It reads them from
`/data/jobs/provision-accounts.txt` (one e-mail per line, `#` comments), or
`--accounts PATH`. That file lives only on the box's volume; never commit
it. Without it the script creates the two staff accounts only and leaves
every existing account alone. Day-to-day account work does not need it:
Settings -> Accounts, or `docker compose exec app python -m
takeoff_engine.users add <email>`. If you do want to re-run the script for
everybody (e.g. `--reissue`), first put the list on the box, e.g.
`docker compose cp provision-accounts.txt app:/data/jobs/`. The box already
knows every account, so the file can be made there: `docker compose exec -T
app python -m takeoff_engine.users list | grep -o '^[^ ]*@[^ ]*' >
provision-accounts.txt` (staff included, which is harmless).

## Updates

- **push to `main`** -> builds, runs the import check, publishes `:latest`. **Does not touch the box.**
- **push a `v*` tag** -> publishes `:vN.N.N` and **deploys**
- **Run workflow** button -> deploys `:latest` on demand

There is one box, and it is the one clients see. A deploy restarts the
container under whoever is demoing, so deploying is deliberate rather than
automatic: tag it, or press the button, when nobody is in a meeting. The day a
second box exists, point `main` at it and restore the staging environment.

```bash
git tag -a v1.0.1 -m "what changed" && git push origin v1.0.1
```

Repo secrets are already set: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_DIR`
(`/home/deploy/takeoff`), `DEPLOY_SSH_KEY`.

```bash
git tag v1.0.0 && git push origin v1.0.0    # production
```

GitHub repo secrets required: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`,
`DEPLOY_DIR`. The image is published to GHCR with the built-in `GITHUB_TOKEN`.

## What hosted mode changes

`TAKEOFF_HOSTED=1` is set in the image. It refuses client-supplied filesystem
paths — those would be an arbitrary file read on a public box — and empties
`/api/samples`. **Uploading is therefore the only way in**, which is why the
whole window is a drop target.

Uploads are content-addressed: the same plan set uploaded twice resolves to the
same path, so the second upload returns the same job with its detections
already cached, and the reviewer's saved labels and prices come back with it.

## Getting the demo plans onto a new box

`./seed-demo.sh https://core.workoptional.studio`, run from a machine that has
the plan corpus. It uploads from there to the box, detects the pages worth
showing, and prints how long each took. Safe to re-run: uploads are
content-addressed and a page with a fresh result reports `done` without
recomputing. Klayton's fiber set is deliberately excluded, he is under NDA.

**Re-seed after any deploy that changed `takeoff_engine/`.** A stored result
records the engine signature that produced it, and `_result_stale` in
`server.py` refuses to serve one whose signature has moved. That is correct
behaviour, because a number the current engine would not produce is how a wrong
figure reaches a bid, but it means the volume surviving a deploy keeps the
files without keeping them valid. A UI-only or `server.py`-only deploy needs no
re-seed.

That is a pre-warmed cache, and the honest way to use it is to say so and press
**Reset this document** live — it clears the server's cached detections for that
PDF and re-detects from scratch in front of the room. That turns the one thing
that could look staged into the most convincing thing in the demo.

## If it will not start

`docker compose logs app` — the entrypoint prints exactly which guard it hit.
The health check treats **401 as healthy**: the site password being on is the
correct state, not a failure.
