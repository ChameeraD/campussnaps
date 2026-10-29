"""CampusSnaps web app: runs on Amazon EC2.

Photos are stored in Amazon S3. Their details (event, caption, likes) live in
Amazon DynamoDB, where the gallery Lambda function writes them. The EC2
instance's IAM role supplies credentials, so there are no access keys anywhere
in this code. Standard library + boto3 only, so it runs on a fresh Amazon Linux
2023 server with no build step.
"""
import html
import os
import re
import time
import urllib.parse
import urllib.request
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

BUCKET = os.environ["BUCKET"]
TABLE = os.environ["TABLE"]
REGION = os.environ.get("AWS_REGION", "us-east-1")
PORT = int(os.environ.get("PORT", "80"))
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per photo

# The events students can upload to. Each one is a partition key in DynamoDB.
EVENTS = {
    "sports-day-2026": "Sports Day 2026",
    "freshers-night-2026": "Freshers' Night 2026",
    "campus-life": "Campus life",
}
DEFAULT_EVENT = "sports-day-2026"

s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url="https://s3." + REGION + ".amazonaws.com",
    config=Config(signature_version="s3v4"),
)
table = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)


def instance_info():
    """Read the instance ID and Availability Zone from EC2 metadata (IMDSv2)."""
    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
        )
        token = urllib.request.urlopen(token_req, timeout=1).read().decode()

        def meta(path):
            req = urllib.request.Request(
                "http://169.254.169.254/latest/meta-data/" + path,
                headers={"X-aws-ec2-metadata-token": token},
            )
            return urllib.request.urlopen(req, timeout=1).read().decode()

        return meta("instance-id"), meta("placement/availability-zone")
    except Exception:
        return "local-test", "no-az"


INSTANCE_ID, AZ = instance_info()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>CampusSnaps</title>
<style>
  :root {{ --ink:#16213e; --muted:#5b6478; --accent:#ff9900; --bg:#f6f7fb; --card:#fff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:var(--ink); }}
  header {{ background:var(--ink); color:#fff; padding:24px 16px; }}
  header h1 {{ margin:0; font-size:28px; }}
  header h1 span {{ color:var(--accent); }}
  header p {{ margin:6px 0 0; opacity:.8; }}
  main {{ max-width:960px; margin:0 auto; padding:20px 16px 40px; }}
  .card {{ background:var(--card); border-radius:12px; padding:18px; box-shadow:0 1px 3px rgba(0,0,0,.08); margin-bottom:20px; }}
  .note {{ background:#fff4e0; border-left:4px solid var(--accent); padding:12px 14px; border-radius:8px; margin-bottom:20px; }}
  form.upload {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
  input[type=text], select {{ padding:9px 10px; border:1px solid #cdd2dd; border-radius:8px; font:inherit; }}
  button {{ background:var(--accent); border:0; color:#111; font-weight:600; padding:10px 18px; border-radius:8px; cursor:pointer; font:inherit; }}
  nav {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; }}
  nav a {{ padding:8px 14px; border-radius:999px; background:#e4e7ef; color:var(--ink); text-decoration:none; font-size:14px; }}
  nav a.on {{ background:var(--ink); color:#fff; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }}
  .grid figure {{ margin:0; background:var(--card); border-radius:10px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .grid img {{ width:100%; height:170px; object-fit:cover; display:block; }}
  .grid figcaption {{ display:flex; justify-content:space-between; align-items:center; gap:8px; padding:8px 10px; font-size:14px; }}
  .grid figcaption span {{ color:var(--muted); word-break:break-word; }}
  button.like {{ background:#f1f2f6; padding:6px 10px; font-size:13px; white-space:nowrap; }}
  footer {{ font-size:12px; color:var(--muted); text-align:center; padding:16px; }}
</style></head>
<body>
<header><h1>Campus<span>Snaps</span></h1><p>Share photos from campus events</p></header>
<main>
{banner}
<div class="card">
  <form class="upload" action="/upload" method="post" enctype="multipart/form-data">
    <select name="event">{options}</select>
    <input type="text" name="caption" maxlength="100" placeholder="Caption (optional)">
    <input type="file" name="photo" accept="image/*" required>
    <button type="submit">Upload photo</button>
  </form>
</div>
<nav>{tabs}</nav>
<h2>{event_name} ({count})</h2>
{gallery}
</main>
<footer>Served by EC2 instance {instance} in {az} &middot; photos in Amazon S3 &middot;
details in Amazon DynamoDB &middot; added by AWS Lambda</footer>
</body></html>"""


def esc(value):
    return html.escape(str(value), quote=True)


def photos_for(event_id):
    """One DynamoDB query: every photo for one event, newest first."""
    resp = table.query(
        KeyConditionExpression=Key("eventId").eq(event_id),
        ScanIndexForward=False,  # photoIds start with a timestamp, so this is newest first
        Limit=60,
    )
    return resp.get("Items", [])


def render(event_id, banner=""):
    photos = photos_for(event_id)
    if photos:
        items = []
        for p in photos:
            # A presigned link: the bucket stays private, and this link works for 1 hour.
            url = s3.generate_presigned_url(
                "get_object", Params={"Bucket": BUCKET, "Key": p["s3Key"]}, ExpiresIn=3600
            )
            caption = p.get("caption") or p["photoId"].split("-", 1)[-1]
            items.append(
                '<figure id="' + esc(p["photoId"]) + '"><img src="' + esc(url) + '" alt="'
                + esc(caption) + '" loading="lazy"><figcaption><span>' + esc(caption)
                + '</span><form method="post" action="/like">'
                + '<input type="hidden" name="eventId" value="' + esc(event_id) + '">'
                + '<input type="hidden" name="photoId" value="' + esc(p["photoId"]) + '">'
                + '<button class="like" type="submit">&#9829; ' + str(int(p.get("likes", 0)))
                + "</button></form></figcaption></figure>"
            )
        gallery = '<div class="grid">' + "".join(items) + "</div>"
    else:
        gallery = '<p class="card">No photos for this event yet. Upload the first one!</p>'
    tabs = "".join(
        '<a href="/?event=' + eid + '"' + (' class="on"' if eid == event_id else "") + ">"
        + esc(name) + "</a>"
        for eid, name in EVENTS.items()
    )
    options = "".join(
        '<option value="' + eid + '"' + (" selected" if eid == event_id else "") + ">"
        + esc(name) + "</option>"
        for eid, name in EVENTS.items()
    )
    return PAGE.format(
        refresh='<meta http-equiv="refresh" content="4;url=/?event=' + event_id + '">' if banner else "",
        banner=banner,
        options=options,
        tabs=tabs,
        event_name=esc(EVENTS[event_id]),
        count=len(photos),
        gallery=gallery,
        instance=esc(INSTANCE_ID),
        az=esc(AZ),
    )


def parse_form(content_type, body):
    """Split a multipart/form-data body into {field name: (filename, type, bytes)}."""
    msg = BytesParser(policy=default_policy).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    fields = {}
    if msg.is_multipart():
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name:
                fields[name] = (part.get_filename(), part.get_content_type(), part.get_payload(decode=True) or b"")
    return fields


def pick_event(value):
    return value if value in EVENTS else DEFAULT_EVENT


class Handler(BaseHTTPRequestHandler):
    def send_html(self, status, text):
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_GET(self):
        try:
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path == "/health":
                return self.send_html(200, "ok")
            if url.path not in ("/", "/index.html"):
                return self.send_html(404, "Not found")
            event_id = pick_event(query.get("event", [DEFAULT_EVENT])[0])
            banner = ""
            if "uploaded" in query:
                banner = '<div class="note">Photo received! Lambda is adding it to the gallery&hellip;</div>'
            self.send_html(200, render(event_id, banner))
        except Exception as err:  # keep the demo page alive and show what went wrong
            self.send_html(500, "Something went wrong: " + esc(err))

    def do_POST(self):
        try:
            if self.path == "/upload":
                return self.upload()
            if self.path == "/like":
                return self.like()
            self.send_html(404, "Not found")
        except Exception as err:
            self.send_html(500, "Request failed: " + esc(err))

    def upload(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BYTES:
            return self.send_html(413, "Please upload a photo under 5 MB. <a href='/'>Back</a>")
        fields = parse_form(self.headers.get("Content-Type", ""), self.rfile.read(length))
        filename, content_type, data = fields.get("photo", (None, "", b""))
        if not data or not content_type.startswith("image/"):
            return self.send_html(400, "That doesn't look like an image. <a href='/'>Back</a>")
        event_id = pick_event(fields.get("event", (None, "", b""))[2].decode(errors="ignore"))
        caption = fields.get("caption", (None, "", b""))[2].decode(errors="ignore").strip()[:100]
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", filename or "photo")[-60:]
        photo_id = str(int(time.time() * 1000)) + "-" + safe
        s3.put_object(
            Bucket=BUCKET,
            Key="uploads/" + photo_id,  # uploads/ is the prefix that triggers Lambda
            Body=data,
            ContentType=content_type,
            # S3 metadata must be plain ASCII, so the caption is URL-encoded.
            Metadata={"event": event_id, "caption": urllib.parse.quote(caption)},
        )
        self.redirect("/?event=" + event_id + "&uploaded=1")

    def like(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(min(length, 4096)).decode(errors="ignore"))
        event_id = pick_event(form.get("eventId", [""])[0])
        photo_id = form.get("photoId", [""])[0][:200]
        try:
            # ADD is atomic: two students liking at the same moment both count.
            table.update_item(
                Key={"eventId": event_id, "photoId": photo_id},
                UpdateExpression="ADD likes :one",
                ConditionExpression="attribute_exists(photoId)",
                ExpressionAttributeValues={":one": 1},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        self.redirect("/?event=" + event_id + "#" + urllib.parse.quote(photo_id))


if __name__ == "__main__":
    print("CampusSnaps listening on port", PORT, "bucket", BUCKET, "table", TABLE)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
