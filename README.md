# GymLog server - this is super vibe coded

A small server you run at home (on the Pop!_OS machine) so several people on the same network can each keep their GymLog backups in one place.
**Every person is a separate user with their own token.** Everyone sees only their own data.

- Nothing leaves your network. It stores each person's GymLog backup files (the same zip the app's Backup > Export makes).
- The admin makes and switches off users but never reads their backups.
- Runs in Docker. All its data is one folder (`./data`), easy to back up.
- **A dashboard** you open in a browser on any computer on the network: trends for every exercise, volume by body part, body weight, cardio, how your backups are doing, and downloads to Excel or CSV. See "The dashboard" below.

## Run your own copy (quick start)

Anyone can run their own GymLog server for their own backups; nothing is shared between copies. You need a computer that is on at home (a
Linux PC, a Raspberry Pi, a NAS) with **Docker** and **Docker Compose**.

1. Make an empty folder and save [`compose.selfhost.yml`](compose.selfhost.yml) in it as `docker-compose.yml`.
2. `mkdir -p data && sudo chown 10001:10001 data` (the server runs as an ordinary user, id 10001, and must be allowed to write to `./data`), then `docker compose up -d`
3. `docker compose exec gymlog-server python -m app.cli add-user "Me"` &mdash; prints your **token**. Keep it.
4. Find the computer's address (`hostname -I`) and allow the port if there is a firewall (`sudo ufw allow 8080/tcp`).
5. In the GymLog app (Settings > Server backup) enter `http://<that address>:8080` and your token, press *Test connection*, then *Back up now*. To see your
   data as charts, open `http://<that address>:8080/` in a browser and sign in with the same token.

To update later: `docker compose pull && docker compose up -d`. Your data stays in the `./data` folder; copy it to back it up.
More than one person can share one server: add each with their own `add-user` and token, and they only ever see their own backups.

The image is built for ordinary PCs (amd64) and Raspberry Pi / ARM boxes (arm64). Prefer to build it yourself from this source?
Use `docker-compose.yml` (in this repository) instead: `docker compose up -d --build`.

## Start it (building from the source here)

On the Pop!_OS machine, with Docker and Docker Compose installed:

```bash
cd GymLog-Server            # this folder
mkdir -p data && sudo chown 10001:10001 data
docker compose up -d --build
docker compose logs gymlog-server     # the first start prints the ADMIN TOKEN (also saved in ./data/admin_token)
curl http://localhost:8080/health     # {"status":"ok",...}
```

To let phones on the Wi-Fi reach it, allow the port if the machine has a firewall:

```bash
sudo ufw allow 8080/tcp
hostname -I            # the machine's address, e.g. 192.168.1.20  ->  the server address is http://192.168.1.20:8080
```

Give the machine a fixed address in your router (a "DHCP reservation") so the address does not change.

## Add the people

```bash
docker compose exec gymlog-server python -m app.cli add-user "Sam"
docker compose exec gymlog-server python -m app.cli add-user "Alex"
docker compose exec gymlog-server python -m app.cli list-users
```

Each command for a new user prints that person's **token** once (it looks like `gl_...`). Send it to them privately. They enter the server
address and their token in the app. Other commands:

| Command | What it does |
|---|---|
| `add-user "Name"` | New user and token (names are unique, capitals ignored) |
| `list-users` | Everyone, whether they are on, backups stored, size, last backup |
| `reset-token NAME` | New token (lost it, or it leaked); the old one stops working at once |
| `disable-user NAME` / `enable-user NAME` | Lock someone out / let them back in, keeping their data |
| `delete-user NAME --yes` | Remove the user and ALL their backups |
| `show-admin-token` | Print the admin token |

## How users are kept apart

- A token identifies exactly one user. Every request must carry `Authorization: Bearer <token>`; without one the server says 401.
- A user's backups live in their own folder (`data/users/<user id>/backups`) and in the database under their id. Every lookup is by *both* the backup
  and the user, so asking for someone else's backup id gives 404, and listing shows only your own.
- Tokens are long random strings; the server stores only a hash of each, never the token itself. The admin token is a different thing: it works on
  `/admin/...` only and cannot read anyone's backups.
- Too many wrong tokens from one address in a minute and the server answers 429 for a while.
- Each user has a storage allowance (default 5 GB) and keeps their newest 10 backups (older ones are removed automatically). Both can be changed.

## Settings

Copy `.env.example` to `.env`, edit, then `docker compose up -d --build`:
`GYMLOG_ADMIN_TOKEN`, `GYMLOG_MAX_UPLOAD_MB` (500), `GYMLOG_KEEP_BACKUPS` (10), `GYMLOG_USER_QUOTA_MB` (5000), `GYMLOG_FAILED_LOGINS_PER_MINUTE` (10).

## The dashboard

Open `http://<the server's address>:8080/` in any browser on the same network and sign in with your own token (the same one the app uses). You see
only your own data, read from your newest backup, so the dashboard is only as fresh as your last backup (the app sends one about twice a day, or press
*Back up now*).

- **Overview**: totals, a calendar of workout days, weekly volume, new personal bests, and a few plain-language notes (an exercise that has stalled, a body
  part that gets little work, training that has dropped off).
- **Exercises**: pick any exercise to chart its estimated one-rep max, top set, average weight, volume or reps over time, with variants combined or one line each.
- **Muscles**: sets a week and volume by body part (the same exercise-to-muscle rules as the app), plus most-used exercises and variants.
- **Body**: weight with a 7-day average, and a lift's strength compared with your body weight.
- **Cardio**: minutes a week and time by activity.
- **Backups**: when the last one was made, storage used, what each backup holds and how it changed, download any of them, and a list of possible typing mistakes in your data.
- **Export**: everything as one Excel workbook (a sheet per table) or CSV files, or one table at a time. Use the time range at the top to export only part.

Every chart has a **Table** button showing the same numbers, works with the keyboard (arrow keys move along a line), and follows your light or dark setting.
The weight unit switch (lb or kg) is at the top. The page needs no internet: all of it is served by the server itself.

The page is plain HTTP like the rest, so on a home network it is fine but do not expose it to the internet without HTTPS (see below). Your token is kept
in the browser tab only (cleared when you close it) unless you tick *Remember me on this computer*; *Sign out* clears it either way.

## For the app (and anything else)

| Address | Who | What |
|---|---|---|
| `GET /health` | anyone | `{"status":"ok"}` |
| `GET /api/me` | a user | who the token belongs to, how many backups |
| `POST /api/backups` | a user | the body is the backup zip (`Content-Type: application/zip`; optional `X-Device-Name`). 201 stored, 200 identical to the newest (nothing changed), 400 not a GymLog backup, 413 too big, 507 over the allowance |
| `GET /api/backups` | a user | your backups, newest first, with what is in each (workouts, sets, weigh-ins, photos) |
| `GET /api/backups/latest`, `GET /api/backups/{id}` | a user | download |
| `GET /api/dash/...` | a user | what the dashboard shows (overview, weekly, calendar, exercises, exercise, muscles, body, cardio, backups, checks, export); each takes `from`, `to` and `backup` where it makes sense |
| `DELETE /api/backups/{id}` | a user | delete one of your own |
| `POST /admin/users` `{"name":"Sam"}` | admin | make a user, returns the token once |
| `GET /admin/users`, `POST /admin/users/{id}/reset-token`, `.../disable`, `.../enable`, `DELETE /admin/users/{id}` | admin | look after users |

Quick test without the app: `python tools/gymlog_upload.py upload backup.zip --server http://192.168.1.20:8080 --token gl_...`

## Things to know

- **No encryption.** On your own Wi-Fi this is usually fine, but tokens and backups travel as plain HTTP. If the server is ever reachable from outside the
  house, put it behind HTTPS (a reverse proxy such as Caddy) or a VPN such as Tailscale first.
- Android blocks plain `http://` addresses by default; the GymLog app (1.3 and newer) is set up to allow them so a home server works.
- **Back it up:** copy the `./data` folder now and then (stop the container first for a perfectly clean copy). It holds the user list and every backup.
- **Update:** replace the files, then `docker compose up -d --build`. Your data folder is untouched.
- Moving to another machine: copy the whole folder including `./data`.

## Developing and testing

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\...
.venv/bin/python -m pytest          # 47 tests: separation of users, tokens, uploads, limits, the command line, the dashboard's numbers and downloads
```

## For the person publishing this (maintainer notes)

1. Create a GitHub repository (for example `gymlog-server`), then in this folder: `git remote add origin https://github.com/<you>/gymlog-server.git`
   and `git push -u origin main`. The **Tests** workflow runs on every push.
2. Publish the image: `git tag v1.0.0 && git push origin v1.0.0`. The **Publish the Docker image** workflow builds it for amd64 and arm64 and pushes it to
   `ghcr.io/<you>/gymlog-server` (tags `1.0.0`, `1.0` and `latest`). Watch it under the repository's *Actions* tab.
3. Make the image public so others can pull it without logging in: GitHub profile > *Packages* > `gymlog-server` > *Package settings* > *Change visibility*.
4. For new versions: change `VERSION` in `app/config.py`, commit, tag `v1.1.0`, push the tag.

## Licence

MIT, see [LICENSE](LICENSE). Use it, change it and share it freely; there is no warranty.

## Trying the dashboard on real data

`python tools/db_to_backup.py gymlog.db out.zip` turns a copy of the phone's database into a backup file, which you can upload with `tools/gymlog_upload.py`.

## What comes next

The server, the app's backup feature (Settings > Server backup) and the dashboard are all in. Ideas for later: photos in the dashboard (they are in the
backups already, but they are private, so it would be opt-in), comparing two time periods side by side, and a yearly summary.
