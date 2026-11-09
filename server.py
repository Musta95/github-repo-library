"""GitHub Repo Library: single-file Python standard-library backend."""
import base64
import csv
import hmac
import io
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from categories import CATEGORIES, DEFAULT_CATEGORY, LEGACY_CATEGORY_NAMES, classify, classify_multiple

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DB_PATH = DATA_DIR / "repos.sqlite3"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8765"))
UA = "GithubRepoLibrary/4.4 (personal repository catalog)"
API_VERSION = "2026-03-10"
DB_LOCK = threading.RLock()
SEARCH_RATE_LOCK = threading.Lock()
LAST_SEARCH = 0.0
# Daily auto-discovery. Defaults: Europe/Berlin 03:00, 10 results per topic.
AUTO_DISCOVER_ENABLED = os.environ.get("AUTO_DISCOVER_ENABLED", "true").strip().lower() in ("true", "1", "yes", "on")
AUTO_DISCOVER_HOUR = max(0, min(int(os.environ.get("AUTO_DISCOVER_HOUR", "3")), 23))
AUTO_DISCOVER_LIMIT = max(1, min(int(os.environ.get("AUTO_DISCOVER_LIMIT", "10")), 50))
AUTO_DISCOVER_TZ = os.environ.get("AUTO_DISCOVER_TZ", "Europe/Berlin")



def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=20000")
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with DB_LOCK, connect() as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS repos (
              id INTEGER PRIMARY KEY,
              full_name TEXT NOT NULL UNIQUE,
              html_url TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              language TEXT NOT NULL DEFAULT '',
              stars INTEGER NOT NULL DEFAULT 0,
              forks INTEGER NOT NULL DEFAULT 0,
              topics TEXT NOT NULL DEFAULT '[]',
              license TEXT NOT NULL DEFAULT '',
              pushed_at TEXT,
              category TEXT NOT NULL DEFAULT 'Other',
              category_manual INTEGER NOT NULL DEFAULT 0,
              notes TEXT NOT NULL DEFAULT '',
              favorite INTEGER NOT NULL DEFAULT 0,
              added_at TEXT NOT NULL,
              synced_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auto_discover_runs (
              id INTEGER PRIMARY KEY CHECK (id=1),
              last_day TEXT NOT NULL DEFAULT '',
              started_at TEXT NOT NULL DEFAULT '',
              finished_at TEXT NOT NULL DEFAULT '',
              result TEXT NOT NULL DEFAULT '',
              processed INTEGER NOT NULL DEFAULT 0,
              added INTEGER NOT NULL DEFAULT 0,
              categories_done INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS custom_categories (
              name TEXT PRIMARY KEY COLLATE NOCASE,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS repo_categories (
              repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
              category TEXT NOT NULL,
              PRIMARY KEY(repo_id, category)
            );
            CREATE INDEX IF NOT EXISTS idx_repo_categories_cat ON repo_categories(category);
            CREATE TABLE IF NOT EXISTS personal_lists (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL COLLATE NOCASE UNIQUE,
              description TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS personal_list_repos (
              list_id INTEGER NOT NULL REFERENCES personal_lists(id) ON DELETE CASCADE,
              repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
              added_at TEXT NOT NULL,
              PRIMARY KEY (list_id, repo_id)
            );
            CREATE INDEX IF NOT EXISTS idx_list_repos_repo ON personal_list_repos(repo_id);
            CREATE INDEX IF NOT EXISTS idx_repos_cat ON repos(category);
            CREATE INDEX IF NOT EXISTS idx_repos_stars ON repos(stars DESC);
            CREATE VIRTUAL TABLE IF NOT EXISTS repo_fts USING fts5(
              repo_id UNINDEXED, full_name, description, topics,
              notes, category, language, tokenize='unicode61'
            );
        """)
        cols = {r[1] for r in db.execute("PRAGMA table_info(repos)").fetchall()}
        if "categories_manual" not in cols:
            db.execute("ALTER TABLE repos ADD COLUMN categories_manual INTEGER NOT NULL DEFAULT 0")
        # Translate stored built-in categories without losing manual selections.
        # This is idempotent. Personal list names and user-created categories
        # are deliberately not translated or renamed.
        translated_primaries = False
        for old_name, new_name in LEGACY_CATEGORY_NAMES.items():
            # In case both tags exist, keep the English one and remove the old one.
            db.execute("""INSERT OR IGNORE INTO repo_categories(repo_id, category)
                          SELECT repo_id, ? FROM repo_categories WHERE category=?""",
                       (new_name, old_name))
            db.execute("DELETE FROM repo_categories WHERE category=?", (old_name,))
            cur = db.execute("UPDATE repos SET category=? WHERE category=?", (new_name, old_name))
            translated_primaries = translated_primaries or cur.rowcount > 0
        # Migrate existing v2 repositories without overwriting the user's data.
        db.execute("""INSERT OR IGNORE INTO repo_categories(repo_id, category)
                      SELECT id, category FROM repos""")
        # Recompute *automatic* category tags for existing repositories from the
        # stored GitHub topics AND description. Preserve any manual category set.
        changed_primaries = False
        for row in db.execute("SELECT id,full_name,category,topics,description FROM repos WHERE categories_manual=0").fetchall():
            try:
                item = {"topics": json.loads(row["topics"]), "description": row["description"],
                        "full_name": row["full_name"]}
                chosen = row["category"]
                if chosen == DEFAULT_CATEGORY:
                    better = classify(item)
                    if better != DEFAULT_CATEGORY:
                        chosen = better
                        db.execute("UPDATE repos SET category=? WHERE id=?", (chosen, row["id"]))
                        changed_primaries = True
                categories = classify_multiple(item, chosen)
                db.execute("DELETE FROM repo_categories WHERE repo_id=?", (row["id"],))
                for cat in categories:
                    db.execute("INSERT OR IGNORE INTO repo_categories(repo_id,category) VALUES (?,?)", (row["id"], cat))
            except (ValueError, TypeError):
                pass
        num_repos = db.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
        num_fts = db.execute("SELECT COUNT(*) FROM repo_fts").fetchone()[0]
        if num_fts != num_repos or changed_primaries or translated_primaries:
            db.execute("DELETE FROM repo_fts")
            for row in db.execute("SELECT * FROM repos").fetchall():
                index_repo(db, row)


def available_categories():
    """Built-ins and user-created categories, all stored without duplicates."""
    with connect() as db:
        custom = [r[0] for r in db.execute("SELECT name FROM custom_categories ORDER BY name COLLATE NOCASE")]
    return ([{"name": name, "query": spec["query"], "custom": False} for name, spec in CATEGORIES.items()]
            + [{"name": name, "query": "", "custom": True} for name in custom]
            + [{"name": DEFAULT_CATEGORY, "query": "", "custom": False}])


def create_category(name):
    if not isinstance(name, str):
        raise ValueError("Category name must be text.")
    name = name.strip()
    if not 2 <= len(name) <= 60 or any(ord(ch) < 32 for ch in name):
        raise ValueError("Category name must be 2 to 60 characters long.")
    if name.casefold() in {c.casefold() for c in (*CATEGORIES, DEFAULT_CATEGORY, "All", *LEGACY_CATEGORY_NAMES)}:
        raise ValueError("This category already exists.")
    if name.casefold() in {c["name"].casefold() for c in available_categories()}:
        raise ValueError("This category already exists.")
    with DB_LOCK, connect() as db:
        try:
            db.execute("INSERT INTO custom_categories(name,created_at) VALUES(?,?)", (name, now()))
        except sqlite3.IntegrityError as exc:
            raise ValueError("This category already exists.") from exc
    return {"name": name, "query": "", "custom": True}


def valid_category_names():
    return {entry["name"] for entry in available_categories()}


def index_repo(db, r):
    db.execute("DELETE FROM repo_fts WHERE repo_id=?", (str(r["id"]),))
    db.execute("INSERT INTO repo_fts(repo_id,full_name,description,topics,notes,category,language) VALUES(?,?,?,?,?,?,?)",
               (str(r["id"]), r["full_name"], r["description"],
                " ".join(json.loads(r["topics"])), r["notes"], r["category"], r["language"]))


def repo_record(item):
    return {
        "id": int(item["id"]),
        "full_name": str(item["full_name"]),
        "html_url": str(item["html_url"]),
        "description": str(item.get("description") or "")[:500],
        "language": str(item.get("language") or ""),
        "stars": int(item.get("stargazers_count") or 0),
        "forks": int(item.get("forks_count") or 0),
        "topics": json.dumps(item.get("topics") or [], ensure_ascii=False),
        "license": str((item.get("license") or {}).get("spdx_id") or ""),
        "pushed_at": item.get("pushed_at"),
    }


def upsert_repo(item, category=None):
    record = repo_record(item)
    chosen = category if category in CATEGORIES else classify(item)
    stamp = now()
    with DB_LOCK, connect() as db:
        db.execute("""
          INSERT INTO repos(id,full_name,html_url,description,language,stars,forks,topics,license,pushed_at,
                            category,category_manual,notes,favorite,added_at,synced_at)
          VALUES(:id,:full_name,:html_url,:description,:language,:stars,:forks,:topics,:license,:pushed_at,
                 :category,0,'',0,:stamp,:stamp)
          ON CONFLICT(id) DO UPDATE SET
            full_name=excluded.full_name, html_url=excluded.html_url,
            description=excluded.description, language=excluded.language, stars=excluded.stars,
            forks=excluded.forks, topics=excluded.topics, license=excluded.license,
            pushed_at=excluded.pushed_at,
            category=CASE WHEN repos.category_manual=1 THEN repos.category ELSE excluded.category END,
            synced_at=excluded.synced_at
        """, {**record, "category": chosen, "stamp": stamp})
        row = db.execute("SELECT * FROM repos WHERE id=?", (record["id"],)).fetchone()
        if not row["categories_manual"]:
            db.execute("DELETE FROM repo_categories WHERE repo_id=?", (record["id"],))
            for cat in classify_multiple(item, row["category"]):
                db.execute("INSERT OR IGNORE INTO repo_categories(repo_id,category) VALUES (?,?)", (record["id"], cat))
        index_repo(db, row)
    return record["id"]


class GithubError(Exception):
    pass


def github_get(path, params=None):
    # GitHub Search API allows fewer calls than its ordinary repository endpoints.
    # Serialize and space search requests; applies to manual and automatic scans.
    global LAST_SEARCH
    if path.startswith("/search/"):
        with SEARCH_RATE_LOCK:
            interval = 2.5 if GITHUB_TOKEN else 8.0
            remaining = LAST_SEARCH + interval - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            LAST_SEARCH = time.monotonic()
    qs = urllib.parse.urlencode(params or {})
    url = "https://api.github.com" + path + ("?" + qs if qs else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA,
               "X-GitHub-Api-Version": API_VERSION}
    if GITHUB_TOKEN:
        headers["Authorization"] = "Bearer " + GITHUB_TOKEN
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            details = json.loads(exc.read().decode("utf-8")).get("message", "")
        except (ValueError, UnicodeError):
            details = ""
        if exc.code in (403, 429):
            raise GithubError("GitHub API rate limit or access restriction reached. Try again later or configure GITHUB_TOKEN. " + details) from exc
        if exc.code == 404:
            raise GithubError("GitHub repository or user not found.") from exc
        raise GithubError(f"GitHub API HTTP {exc.code}: {details[:220]}") from exc
    except urllib.error.URLError as exc:
        raise GithubError("GitHub is unreachable. Check your internet connection.") from exc


def parse_repo_url(value):
    value = str(value).strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in ("github.com", "www.github.com"):
        raise ValueError("Enter an HTTPS URL in the form github.com/OWNER/REPO.")
    pieces = parsed.path.strip("/").split("/")
    if len(pieces) < 2:
        raise ValueError("URL must include an owner and repository name.")
    owner, name = pieces[:2]
    if name.endswith(".git"):
        name = name[:-4]
    allowed = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
    if not allowed.fullmatch(owner) or not allowed.fullmatch(name):
        raise ValueError("Invalid repository URL.")
    return owner, name


def list_repos(search="", category="", favorite=False, sort="stars", page=1, list_id=None, page_size=40):
    clauses, args = [], []
    if category and category not in ("All", "Alle"):
        clauses.append("EXISTS (SELECT 1 FROM repo_categories rc WHERE rc.repo_id=r.id AND rc.category=?)")
        args.append(category)
    if favorite:
        clauses.append("r.favorite=1")
    if list_id is not None and str(list_id) != "":
        list_id = int(list_id)
        clauses.append("EXISTS (SELECT 1 FROM personal_list_repos p WHERE p.repo_id=r.id AND p.list_id=?)")
        args.append(list_id)
    if search.strip():
        terms = re.findall(r"[^\W_]+", search.strip(), flags=re.UNICODE)[:10]
        if terms:
            fts_query = " AND ".join('"' + t.replace('"', '""') + '"*' for t in terms)
            clauses.append("r.id IN (SELECT CAST(repo_id AS INTEGER) FROM repo_fts WHERE repo_fts MATCH ?)")
            args.append(fts_query)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    order = {"stars": "r.stars DESC", "recent": "r.pushed_at DESC", "name": "r.full_name COLLATE NOCASE ASC", "added": "r.added_at DESC", "synced": "r.synced_at DESC"}.get(sort, "r.stars DESC")
    page = max(1, min(int(page), 100000))
    page_size = max(1, min(int(page_size), 100))
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM repos r" + where, args).fetchone()[0]
        # If an import, edit or concurrent filter shrinks the result count,
        # return the last available page instead of an apparently empty screen.
        max_page = max(1, (total + page_size - 1) // page_size)
        page = min(page, max_page)
        rows = db.execute("SELECT r.* FROM repos r" + where + " ORDER BY " + order + ", r.id DESC LIMIT ? OFFSET ?", args + [page_size, (page-1)*page_size]).fetchall()
        return {"items": [serialize(r, db) for r in rows], "total": total, "page": page, "page_size": page_size}


def serialize(row, db=None):
    data = dict(row)
    data["topics"] = json.loads(data["topics"])
    data["favorite"] = bool(data["favorite"])
    data["category_manual"] = bool(data["category_manual"])
    if db is not None:
        data["list_ids"] = [r[0] for r in db.execute("SELECT list_id FROM personal_list_repos WHERE repo_id=? ORDER BY list_id", (data["id"],))]
        data["categories"] = [r[0] for r in db.execute("SELECT category FROM repo_categories WHERE repo_id=? ORDER BY category", (data["id"],))]
    return data


def get_repo(repo_id):
    with connect() as db:
        row = db.execute("SELECT * FROM repos WHERE id=?", (int(repo_id),)).fetchone()
        if not row:
            raise ValueError("Repository not found.")
        return serialize(row, db)


def get_lists():
    with connect() as db:
        return [dict(r) for r in db.execute("""
            SELECT l.id, l.name, l.description, l.created_at, COUNT(p.repo_id) AS repo_count
            FROM personal_lists l LEFT JOIN personal_list_repos p ON p.list_id=l.id
            GROUP BY l.id ORDER BY l.name COLLATE NOCASE
        """)]


def get_list(list_id):
    with connect() as db:
        row = db.execute("SELECT * FROM personal_lists WHERE id=?", (int(list_id),)).fetchone()
        if not row:
            raise ValueError("List not found.")
        result = dict(row)
        result["repo_ids"] = [r[0] for r in db.execute("SELECT repo_id FROM personal_list_repos WHERE list_id=? ORDER BY added_at DESC", (int(list_id),))]
        return result


def create_list(name, description=""):
    name = str(name).strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("List name must be 1 to 80 characters long.")
    description = str(description).strip()[:500]
    with DB_LOCK, connect() as db:
        try:
            cursor = db.execute("INSERT INTO personal_lists(name,description,created_at) VALUES(?,?,?)", (name, description, now()))
        except sqlite3.IntegrityError as exc:
            raise ValueError("A list with this name already exists.") from exc
        new_list_id = cursor.lastrowid
    return get_list(new_list_id)


def update_list(list_id, payload):
    changes = {}
    if "name" in payload:
        name = str(payload["name"]).strip()
        if not 1 <= len(name) <= 80:
            raise ValueError("List name must be 1 to 80 characters long.")
        changes["name"] = name
    if "description" in payload:
        changes["description"] = str(payload["description"]).strip()[:500]
    if not changes:
        raise ValueError("No valid changes provided.")
    with DB_LOCK, connect() as db:
        cols = ", ".join(f"{k}=?" for k in changes)
        try:
            cursor = db.execute(f"UPDATE personal_lists SET {cols} WHERE id=?", (*changes.values(), int(list_id)))
        except sqlite3.IntegrityError as exc:
            raise ValueError("A list with this name already exists.") from exc
        if cursor.rowcount == 0:
            raise ValueError("List not found.")
    return get_list(list_id)


def delete_list(list_id):
    with DB_LOCK, connect() as db:
        return db.execute("DELETE FROM personal_lists WHERE id=?", (int(list_id),)).rowcount > 0


def set_list_membership(list_id, repo_id, belongs=True):
    list_id, repo_id = int(list_id), int(repo_id)
    with DB_LOCK, connect() as db:
        if not db.execute("SELECT 1 FROM personal_lists WHERE id=?", (list_id,)).fetchone():
            raise ValueError("List not found.")
        if not db.execute("SELECT 1 FROM repos WHERE id=?", (repo_id,)).fetchone():
            raise ValueError("Repository not found.")
        if belongs:
            db.execute("INSERT OR IGNORE INTO personal_list_repos(list_id,repo_id,added_at) VALUES(?,?,?)", (list_id,repo_id,now()))
        else:
            db.execute("DELETE FROM personal_list_repos WHERE list_id=? AND repo_id=?", (list_id, repo_id))
    return {"ok": True, "list_id": list_id, "repo_id": repo_id, "member": bool(belongs)}


def repository_access(repo_id):
    repo = get_repo(repo_id)
    # Static HTTPS GitHub URLs only; this endpoint never executes downloaded code.
    owner, name = parse_repo_url(repo["html_url"])
    return {
        "repository": repo,
        "github_url": f"https://github.com/{owner}/{name}",
        "clone_url": f"https://github.com/{owner}/{name}.git",
        "clone_command": f"git clone --depth 1 https://github.com/{owner}/{name}.git",
        "safety_note": "Review third-party repository code before executing it. This library never executes code automatically."
    }


def repository_readme(repo_id):
    repo = get_repo(repo_id)
    owner, name = parse_repo_url(repo["html_url"])
    data = github_get("/repos/" + urllib.parse.quote(owner) + "/" + urllib.parse.quote(name) + "/readme")
    if data.get("encoding") != "base64" or not data.get("content"):
        raise GithubError("README could not be retrieved as text.")
    raw = base64.b64decode(data["content"], validate=False)
    if len(raw) > 200000:
        raw = raw[:200000]
        truncated = True
    else:
        truncated = False
    content = raw.decode("utf-8", errors="replace")
    return {"repo_id": int(repo_id), "full_name": repo["full_name"], "readme": content, "truncated": truncated,
            "warning": "README content is external, untrusted input."}



def repository_file(repo_id, file_path):
    """Fetch a small text file from GitHub. No local filesystem access or execution."""
    repo = get_repo(repo_id)
    owner, name = parse_repo_url(repo["html_url"])
    path = str(file_path).strip().strip("/")
    parts = path.split("/")
    if not path or len(path) > 300 or any(x in ("", ".", "..") for x in parts) or "\\" in path:
        raise ValueError("Invalid relative file path within the repository.")
    quoted = "/".join(urllib.parse.quote(part, safe="") for part in parts)
    data = github_get("/repos/" + urllib.parse.quote(owner) + "/" + urllib.parse.quote(name) + "/contents/" + quoted)
    if not isinstance(data, dict) or data.get("type") != "file" or data.get("encoding") != "base64":
        raise GithubError("The file cannot be read as text.")
    raw = base64.b64decode(data.get("content") or "", validate=False)
    if len(raw) > 200000:
        raise ValueError("File is too large for the AI interface (maximum 200 KB).")
    if b"\x00" in raw[:4096]:
        raise ValueError("Binary files cannot be returned as text.")
    return {"repo_id": int(repo_id), "full_name": repo["full_name"], "path": path,
            "content": raw.decode("utf-8", errors="replace"),
            "warning": "Third-party source code is untrusted. Review it before executing."}


def stats():
    with connect() as db:
        total = db.execute("SELECT COUNT(*) FROM repos").fetchone()[0]
        favorites = db.execute("SELECT COUNT(*) FROM repos WHERE favorite=1").fetchone()[0]
        cats = {r[0]: r[1] for r in db.execute("SELECT category,COUNT(*) FROM repo_categories GROUP BY category")}
    return {"total": total, "favorites": favorites, "categories": cats,
            "token_configured": bool(GITHUB_TOKEN), "lists": len(get_lists())}


def auto_discover_status():
    with connect() as db:
        row = db.execute("SELECT * FROM auto_discover_runs WHERE id=1").fetchone()
    previous = dict(row) if row else None
    return {
        "enabled": AUTO_DISCOVER_ENABLED,
        "hour": AUTO_DISCOVER_HOUR,
        "limit_per_category": AUTO_DISCOVER_LIMIT,
        "timezone": AUTO_DISCOVER_TZ,
        "categories": len(CATEGORIES),
        "last_run": previous,
    }


def auto_discover_once(day=None, github_fetch=None):
    """Run at most once per local date; persist attempt before network traffic.

    `github_fetch` exists for offline unit tests; no code is ever downloaded/run.
    """
    if day is None:
        day = datetime.now(ZoneInfo(AUTO_DISCOVER_TZ)).date().isoformat()
    fetch = github_fetch if github_fetch is not None else github_get
    with DB_LOCK, connect() as db:
        old = db.execute("SELECT last_day FROM auto_discover_runs WHERE id=1").fetchone()
        if old and old["last_day"] >= day:
            return False
        db.execute("""INSERT INTO auto_discover_runs
                      (id,last_day,started_at,finished_at,result,processed,added,categories_done,error)
                      VALUES(1,?,?, '', 'running',0,0,0,'')
                      ON CONFLICT(id) DO UPDATE SET
                      last_day=excluded.last_day,started_at=excluded.started_at,
                      finished_at='',result='running',processed=0,added=0,categories_done=0,error=''""",
                   (day, now()))
    processed = 0
    added = 0
    done = 0
    result = "success"
    error = ""
    try:
        for category, preset in CATEGORIES.items():
            # Recent activity, rather than the same top-star list every day.
            response = fetch("/search/repositories", {
                "q": preset["query"], "sort": "updated", "order": "desc",
                "per_page": AUTO_DISCOVER_LIMIT, "page": 1})
            for item in response.get("items", []):
                with connect() as db:
                    exists = db.execute("SELECT 1 FROM repos WHERE id=?", (int(item["id"]),)).fetchone()
                upsert_repo(item, category)
                processed += 1
                if not exists:
                    added += 1
            done += 1
    except (GithubError, urllib.error.URLError, ValueError, KeyError, TypeError) as exc:
        result = "partial"
        error = str(exc)[:400]
        print(f"Auto-discovery interrupted: {error}", flush=True)
    finally:
        with DB_LOCK, connect() as db:
            db.execute("""UPDATE auto_discover_runs SET finished_at=?,result=?,processed=?,
                          added=?,categories_done=?,error=? WHERE id=1""",
                       (now(), result, processed, added, done, error))
    print(f"Auto-Discovery {day}: {result}, {done}/{len(CATEGORIES)} categories, "
          f"{added} new out of {processed} results", flush=True)
    return True


def daily_discovery_worker(stop_event):
    print(f"Auto-Discovery: {'enabled' if AUTO_DISCOVER_ENABLED else 'disabled'}; "
          f"daily from {AUTO_DISCOVER_HOUR:02d}:00 ({AUTO_DISCOVER_TZ}), "
          f"{AUTO_DISCOVER_LIMIT} per category", flush=True)
    while not stop_event.is_set():
        try:
            if AUTO_DISCOVER_ENABLED:
                local = datetime.now(ZoneInfo(AUTO_DISCOVER_TZ))
                if local.hour >= AUTO_DISCOVER_HOUR:
                    auto_discover_once(local.date().isoformat())
        except Exception as exc:
            print(f"Auto-discovery scheduler error: {exc}", flush=True)
        stop_event.wait(45)


def update_repo(repo_id, payload):
    changes = {}
    if "category" in payload:
        if not isinstance(payload["category"], str) or payload["category"] not in valid_category_names():
            raise ValueError("Unknown category.")
        changes["category"] = payload["category"]
        changes["category_manual"] = 1
    if "notes" in payload:
        changes["notes"] = str(payload["notes"])[:3000]
    if "favorite" in payload:
        if not isinstance(payload["favorite"], bool):
            raise ValueError("favorite must be true or false.")
        changes["favorite"] = int(payload["favorite"])
    category_tags = None
    if "categories" in payload:
        category_tags = payload["categories"]
        allowed = valid_category_names()
        if not isinstance(category_tags, list) or not category_tags or len(category_tags) > len(allowed) or any(
            not isinstance(cat, str) or cat not in allowed for cat in category_tags
        ):
            raise ValueError("Invalid category selection.")
        category_tags = set(category_tags)
        if "category" in changes:
            category_tags.add(changes["category"])
        changes["categories_manual"] = 1
    if not changes:
        raise ValueError("No valid changes provided.")
    with DB_LOCK, connect() as db:
        cols = ", ".join(f"{k}=?" for k in changes)
        cur = db.execute(f"UPDATE repos SET {cols} WHERE id=?", (*changes.values(), repo_id))
        if cur.rowcount == 0:
            raise ValueError("Repository not found.")
        row = db.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone()
        if category_tags is not None:
            db.execute("DELETE FROM repo_categories WHERE repo_id=?", (repo_id,))
            for cat in sorted(category_tags):
                db.execute("INSERT INTO repo_categories(repo_id,category) VALUES (?,?)", (repo_id, cat))
        elif "category" in changes:
            db.execute("INSERT OR IGNORE INTO repo_categories(repo_id,category) VALUES (?,?)", (repo_id, row["category"]))
        index_repo(db, row)
        return serialize(row, db)


def remove_repo(repo_id):
    with DB_LOCK, connect() as db:
        cur = db.execute("DELETE FROM repos WHERE id=?", (repo_id,))
        db.execute("DELETE FROM repo_fts WHERE repo_id=?", (str(repo_id),))
        return cur.rowcount > 0


def all_repos():
    with connect() as db:
        return [serialize(r, db) for r in db.execute("SELECT * FROM repos ORDER BY category, stars DESC")]


class Handler(BaseHTTPRequestHandler):
    server_version = "RepoLibrary/4.4"

    def require_ai_auth(self):
        if not AI_API_KEY:
            self.send_json({"error": "AI_API_KEY is not configured. Set the server environment variable."}, 503)
            return False
        provided = self.headers.get("X-API-Key", "")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            provided = authorization[7:]
        if not hmac.compare_digest(provided, AI_API_KEY):
            self.send_json({"error": "Invalid or missing AI API key."}, 401)
            return False
        return True

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_bytes(self, content, mime="application/json; charset=utf-8", filename=None):
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > 20000:
            raise ValueError("Invalid request body size.")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        path = url.path
        params = urllib.parse.parse_qs(url.query)
        get = lambda key, default="": params.get(key, [default])[0]
        try:
            if path == "/api/categories":
                return self.send_json(available_categories())
            if path == "/api/stats":
                return self.send_json(stats())
            if path == "/api/auto-discovery":
                return self.send_json(auto_discover_status())
            if path == "/api/lists":
                return self.send_json(get_lists())
            match = re.fullmatch(r"/api/lists/(\d+)", path)
            if match:
                return self.send_json(get_list(match.group(1)))
            if path == "/api/repos":
                return self.send_json(list_repos(get("q"), get("category"), get("favorite") == "1", get("sort", "stars"), get("page", "1"), get("list_id") or None))
            match = re.fullmatch(r"/api/repos/(\d+)", path)
            if match:
                return self.send_json(get_repo(match.group(1)))
            if path.startswith("/api/ai/"):
                if not self.require_ai_auth():
                    return
                if path == "/api/ai/capabilities":
                    return self.send_json({"version": "2.0", "endpoints": ["GET /api/ai/search?q=...&list_id=...", "GET /api/ai/categories", "GET /api/ai/lists", "GET /api/ai/repos/{id}", "GET /api/ai/repos/{id}/readme", "GET /api/ai/repos/{id}/file?path=..."]})
                if path == "/api/ai/search":
                    return self.send_json(list_repos(get("q"), get("category"), False, "stars", get("page", "1"), get("list_id") or None, 50))
                if path == "/api/ai/categories":
                    return self.send_json(available_categories())
                if path == "/api/ai/lists":
                    return self.send_json(get_lists())
                match = re.fullmatch(r"/api/ai/repos/(\d+)", path)
                if match:
                    return self.send_json(repository_access(match.group(1)))
                match = re.fullmatch(r"/api/ai/repos/(\d+)/readme", path)
                if match:
                    return self.send_json(repository_readme(match.group(1)))
                match = re.fullmatch(r"/api/ai/repos/(\d+)/file", path)
                if match:
                    return self.send_json(repository_file(match.group(1), get("path")))
            if path == "/api/export.json":
                return self.send_bytes(json.dumps(all_repos(), ensure_ascii=False, indent=2), filename="github-repos.json")
            if path == "/api/export-library.json":
                return self.send_bytes(json.dumps({"format_version": 2, "repositories": all_repos(), "lists": get_lists()}, ensure_ascii=False, indent=2), filename="github-repo-library-backup.json")
            if path == "/api/export.csv":
                output = io.StringIO()
                items = all_repos()
                fields = ["full_name", "html_url", "description", "category", "language", "stars", "license", "topics", "notes", "favorite", "added_at"]
                writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for repo in items:
                    writer.writerow({**repo, "topics": ", ".join(repo["topics"])})
                return self.send_bytes(output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "github-repos.csv")
            asset_map = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if path in asset_map:
                name = asset_map[path]
                mime = {"index.html": "text/html; charset=utf-8", "app.js": "application/javascript; charset=utf-8", "style.css": "text/css; charset=utf-8"}[name]
                return self.send_bytes((ROOT / "web" / name).read_bytes(), mime)
            return self.send_json({"error": "Not found"}, 404)
        except (ValueError, sqlite3.OperationalError, TypeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except GithubError as exc:
            return self.send_json({"error": str(exc)}, 502)
        except Exception:
            return self.send_json({"error": "Internal server error."}, 500)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            body = self.body_json()
            if path == "/api/categories":
                return self.send_json(create_category(body.get("name", "")), 201)
            if path == "/api/lists":
                return self.send_json(create_list(body.get("name", ""), body.get("description", "")), 201)
            match = re.fullmatch(r"/api/lists/(\d+)/repos", path)
            if match:
                return self.send_json(set_list_membership(match.group(1), body.get("repo_id")))
            if path == "/api/import-url":
                owner, name = parse_repo_url(body.get("url", ""))
                list_id = body.get("list_id")
                if list_id is not None:
                    get_list(list_id)  # fail before import when target list is missing
                item = github_get("/repos/" + urllib.parse.quote(owner) + "/" + urllib.parse.quote(name))
                repo_id = upsert_repo(item)
                if list_id is not None:
                    set_list_membership(list_id, repo_id)
                return self.send_json({"count": 1, "id": repo_id, "list_id": list_id})
            if path == "/api/discover":
                category = str(body.get("category") or "")
                if category not in CATEGORIES:
                    raise ValueError("Select a valid category.")
                amount = max(1, min(int(body.get("limit", 30)), 100))
                query = CATEGORIES[category]["query"]
                result = github_get("/search/repositories", {"q": query, "sort": "stars", "order": "desc", "per_page": amount, "page": 1})
                count = 0
                for item in result.get("items", []):
                    upsert_repo(item, category)
                    count += 1
                return self.send_json({"count": count, "category": category})
            if path == "/api/import-stars":
                username = str(body.get("username") or "").strip()
                max_pages = max(1, min(int(body.get("pages", 2)), 5))
                if username and not re.fullmatch(r"[A-Za-z0-9-]{1,39}", username):
                    raise ValueError("Invalid GitHub username.")
                if not username and not GITHUB_TOKEN:
                    raise ValueError("GITHUB_TOKEN is required to import your own stars without a username.")
                base_path = "/users/" + urllib.parse.quote(username) + "/starred" if username else "/user/starred"
                count = 0
                for page in range(1, max_pages + 1):
                    batch = github_get(base_path, {"per_page": 100, "page": page})
                    for item in batch:
                        upsert_repo(item)
                        count += 1
                    if len(batch) < 100:
                        break
                return self.send_json({"count": count})
            return self.send_json({"error": "Not found"}, 404)
        except (ValueError, json.JSONDecodeError, TypeError) as exc:
            return self.send_json({"error": str(exc)}, 400)
        except GithubError as exc:
            return self.send_json({"error": str(exc)}, 502)
        except Exception:
            return self.send_json({"error": "Internal server error during import."}, 500)

    def do_PATCH(self):
        path = urllib.parse.urlsplit(self.path).path
        match = re.fullmatch(r"/api/repos/(\d+)", path)
        list_match = re.fullmatch(r"/api/lists/(\d+)", path)
        if not match and not list_match:
            return self.send_json({"error": "Not found"}, 404)
        try:
            if list_match:
                return self.send_json(update_list(int(list_match.group(1)), self.body_json()))
            return self.send_json(update_repo(int(match.group(1)), self.body_json()))
        except (ValueError, TypeError) as exc:
            return self.send_json({"error": str(exc)}, 400)

    def do_DELETE(self):
        path = urllib.parse.urlsplit(self.path).path
        match = re.fullmatch(r"/api/repos/(\d+)", path)
        list_match = re.fullmatch(r"/api/lists/(\d+)", path)
        member_match = re.fullmatch(r"/api/lists/(\d+)/repos/(\d+)", path)
        try:
            if member_match:
                return self.send_json(set_list_membership(int(member_match.group(1)), int(member_match.group(2)), False))
            if list_match and delete_list(int(list_match.group(1))):
                return self.send_json({"ok": True})
            if match and remove_repo(int(match.group(1))):
                return self.send_json({"ok": True})
            return self.send_json({"error": "Not found"}, 404)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)


if __name__ == "__main__":
    init_db()
    threading.Thread(target=daily_discovery_worker, args=(threading.Event(),), daemon=True, name="daily-discovery").start()
    print(f"GitHub Repo Library is running at http://{HOST}:{PORT}")
    if not GITHUB_TOKEN:
        print("Notice: GITHUB_TOKEN is not set. Unauthenticated GitHub API requests have lower rate limits.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
