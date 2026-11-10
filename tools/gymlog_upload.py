#!/usr/bin/env python3
"""Send a GymLog backup file to the server, or fetch your newest one. Needs only Python 3.

  python gymlog_upload.py upload  backup.zip   --server http://192.168.1.20:8080 --token gl_xxx
  python gymlog_upload.py list                 --server ... --token ...
  python gymlog_upload.py latest  restored.zip --server ... --token ...

The token can also come from the GYMLOG_TOKEN environment variable and the address from GYMLOG_SERVER.
"""
import argparse
import json
import os
import platform
import sys
import urllib.error
import urllib.request


def call(method, url, token, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": "Bearer " + token, **(headers or {})})
    try:
        return urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"The server said {e.code}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach the server: {e.reason}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["upload", "list", "latest"])
    p.add_argument("file", nargs="?")
    p.add_argument("--server", default=os.environ.get("GYMLOG_SERVER", ""))
    p.add_argument("--token", default=os.environ.get("GYMLOG_TOKEN", ""))
    a = p.parse_args()
    if not a.server or not a.token:
        sys.exit("Give --server and --token (or set GYMLOG_SERVER and GYMLOG_TOKEN).")
    base = a.server.rstrip("/")
    if a.action == "upload":
        if not a.file:
            sys.exit("Say which file to upload.")
        with open(a.file, "rb") as f:
            body = f.read()
        r = call("POST", base + "/api/backups", a.token, body, {"Content-Type": "application/zip", "X-Device-Name": platform.node()[:60]})
        out = json.load(r)
        print(("Uploaded" if out["created"] else "Already on the server (nothing changed)") + f": {out['backup']['id']}  {out['backup']['summary']}")
    elif a.action == "list":
        for b in json.load(call("GET", base + "/api/backups", a.token))["backups"]:
            print(b["id"], f"{b['size'] / 1e6:.1f} MB", b["summary"], b["device"])
    else:
        r = call("GET", base + "/api/backups/latest", a.token)
        out = a.file or "gymlog-latest.zip"
        with open(out, "wb") as f:
            f.write(r.read())
        print("Saved", out)


if __name__ == "__main__":
    main()
