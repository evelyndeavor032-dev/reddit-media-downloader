import sys
import os
import re
import json
import time
import uuid
import csv
import zipfile
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse

import requests

from PyQt5.QtCore import (
    Qt, QObject, QRunnable, QThreadPool, pyqtSignal, QTimer
)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QTabWidget, QLabel, QPushButton, QLineEdit,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView,
    QSpinBox, QComboBox, QFileDialog, QMessageBox, QGroupBox,
    QAbstractItemView
)

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from selenium import webdriver

    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.chrome.options import Options as ChromeOptions

    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.edge.options import Options as EdgeOptions

except ImportError:
    webdriver = None
    ChromeService = None
    ChromeOptions = None
    EdgeService = None
    EdgeOptions = None


# ============================================================
# APPLICATION CONFIGURATION
# ============================================================

APP_VERSION = "3.8"

BASE_DIR = Path(__file__).resolve().parent

DOWNLOAD_DIR = BASE_DIR / "downloads"

HISTORY_FILE = BASE_DIR / "download_history.json"
INDEX_FILE = BASE_DIR / "subreddit_index.json"
SETTINGS_FILE = BASE_DIR / "settings.json"

CHROME_PROFILE = BASE_DIR / "chrome_profile"
EDGE_PROFILE = BASE_DIR / "edge_profile"

DOWNLOAD_DIR.mkdir(exist_ok=True)
CHROME_PROFILE.mkdir(exist_ok=True)
EDGE_PROFILE.mkdir(exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120 Safari/537.36"
)

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp",
    ".avif", ".bmp", ".tif", ".tiff"
}

GIF_EXTENSIONS = {
    ".gif"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".webm", ".mov", ".mkv",
    ".m4v", ".avi", ".flv", ".m3u8"
}

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".ogg",
    ".opus", ".wav", ".flac", ".wma"
}

MEDIA_EXTENSIONS = (
    IMAGE_EXTENSIONS
    | GIF_EXTENSIONS
    | VIDEO_EXTENSIONS
    | AUDIO_EXTENSIONS
)


# ============================================================
# GENERAL UTILITIES
# ============================================================

def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass

    return default


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    tmp.replace(path)


def valid_url(url):
    try:
        parsed = urlparse((url or "").strip())

        return (
            parsed.scheme in ("http", "https")
            and bool(parsed.netloc)
        )

    except Exception:
        return False


def clean_filename(value):
    value = re.sub(
        r'[\\/:*?"<>|]',
        "_",
        str(value or "")
    )

    value = re.sub(r"\s+", " ", value).strip()

    return value[:120] or "reddit_media"


def clean_community(value):
    value = clean_filename(value or "General")

    value = value.replace(" ", "_")

    return "r_" + value[:100]


def source_for(url):
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "External"

    if "reddit.com" in host or "redd.it" in host:
        return "Reddit"

    if "redgifs.com" in host:
        return "Redgifs"

    return "External"


def media_category(filepath):
    ext = Path(filepath).suffix.lower()

    if ext in GIF_EXTENSIONS:
        return "GIFs"

    if ext in IMAGE_EXTENSIONS:
        return "Images"

    if ext in AUDIO_EXTENSIONS:
        return "Audios"

    if ext in VIDEO_EXTENSIONS:
        return "Videos"

    return "Other"


def community_folder(community):
    return DOWNLOAD_DIR / clean_community(community)


def uploader_folder(community, category, uploader):
    uploader_name = clean_filename(uploader or "Unknown_Uploader").replace(" ", "_")
    folder = community_folder(community) / category / uploader_name

    folder.mkdir(
        parents=True,
        exist_ok=True
    )

    return folder


def organize_file(filepath, community, uploader=""):
    """
    Move a completed file into its community/category folder.
    """

    source = Path(filepath)

    if not source.is_file():
        return str(source)

    category = media_category(source)

    destination_folder = uploader_folder(
        community,
        category,
        uploader
    )

    destination = destination_folder / source.name

    if destination.resolve() == source.resolve():
        return str(source)

    if destination.exists():

        stem = source.stem
        suffix = source.suffix

        destination = destination_folder / (
            f"{stem}_{uuid.uuid4().hex[:6]}{suffix}"
        )

    shutil.move(
        str(source),
        str(destination)
    )

    return str(destination)


def is_direct_media(url):

    if not valid_url(url):
        return False

    parsed = urlparse(url)

    path = parsed.path.lower()
    host = parsed.netloc.lower()

    if Path(path).suffix in MEDIA_EXTENSIONS:

        return True

    return (
        host == "i.redd.it"
        or host.endswith(".i.redd.it")
    )


def make_record(
    url,
    name="",
    uploader="",
    community="",
    category="General",
    post_url="",
    download_method=None,
    media_url=""
):

    method = download_method or (
        "direct" if is_direct_media(url) else "extractor"
    )

    return {
        "id": str(uuid.uuid4()),

        "url": url,

        "post_url": post_url or (
            url if method == "extractor" else ""
        ),

        "media_url": media_url or (
            url if method == "direct" else ""
        ),

        "download_method": method,

        "post_name": name,
        "uploader": uploader,
        "community": community or "General",
        "category": category,

        "source": source_for(url),

        "status": "Pending",
        "progress": 0,

        "saved_file": "",
        "details": "",

        "created": timestamp(),
        "attempts": 0
    }


# ============================================================
# DOWNLOAD WORKERS
# ============================================================

class Signals(QObject):

    progress = pyqtSignal(str, int, str)

    completed = pyqtSignal(
        str, bool, str, str
    )


class DownloadWorker(QRunnable):

    def __init__(self, record, browser):

        super().__init__()

        self.record = dict(record)
        self.browser = browser

        self.signals = Signals()

    def _direct_download(
        self,
        url,
        rid,
        post_url,
        name,
        community
    ):

        headers = {
            "User-Agent": USER_AGENT
        }

        if post_url:
            headers["Referer"] = post_url

        with requests.get(
            url,
            headers=headers,
            stream=True,
            timeout=(20, 60)
        ) as response:

            if response.status_code == 429:
                raise RuntimeError(
                    "RATE LIMITED (HTTP 429)"
                )

            if response.status_code in (401, 403):
                raise RuntimeError(
                    f"ACCESS REQUIRED (HTTP {response.status_code})"
                )

            response.raise_for_status()

            ctype = (
                response.headers
                .get("Content-Type", "")
                .split(";")[0]
                .lower()
            )

            if ctype in ("text/html", "application/xhtml+xml"):
                raise RuntimeError(
                    "The URL returned a webpage, not a media file. Use the page URL with the extractor."
                )

            ext = Path(
                urlparse(url).path
            ).suffix.lower()

            if ext not in MEDIA_EXTENSIONS:

                ext = {
                    "video/mp4": ".mp4",
                    "video/webm": ".webm",
                    "video/quicktime": ".mov",
                    "image/jpeg": ".jpg",
                    "image/png": ".png",
                    "image/gif": ".gif",
                    "image/webp": ".webp",
                    "image/avif": ".avif",
                    "audio/mpeg": ".mp3",
                    "audio/mp4": ".m4a",
                    "audio/ogg": ".ogg",
                    "audio/wav": ".wav"
                }.get(ctype, ".bin")

            if ext == ".m3u8":

                raise RuntimeError(
                    "HLS streams must be handled by yt-dlp."
                )

            temp_folder = DOWNLOAD_DIR / "_processing"

            temp_folder.mkdir(
                parents=True,
                exist_ok=True
            )

            final = temp_folder / (
                f"{clean_filename(name)}_{rid[:8]}{ext}"
            )

            temp = final.with_suffix(
                final.suffix + ".part"
            )

            total = int(
                response.headers.get("Content-Length", 0) or 0
            )

            done = 0

            with open(temp, "wb") as f:

                for chunk in response.iter_content(
                    1024 * 256
                ):

                    if not chunk:
                        continue

                    f.write(chunk)

                    done += len(chunk)

                    pct = (
                        int(done * 100 / total)
                        if total else 0
                    )

                    self.signals.progress.emit(
                        rid,
                        min(pct, 99),
                        "Downloading"
                    )

            if not temp.exists() or temp.stat().st_size == 0:

                temp.unlink(missing_ok=True)

                raise RuntimeError(
                    "Downloaded file verification failed."
                )

            temp.replace(final)

            return str(final)

    def run(self):

        r = self.record

        rid = r["id"]

        method = r.get(
            "download_method",
            "extractor"
        )

        url = r.get("media_url") or r.get("url", "")

        name = r.get("post_name") or "reddit_media"

        community = r.get("community") or "General"

        last_error = ""

        for attempt in range(1, 4):

            try:

                if not valid_url(url):
                    raise RuntimeError(
                        "Invalid download URL."
                    )

                if (
                    method == "direct"
                    and is_direct_media(url)
                    and not url.lower().split("?")[0].endswith(".m3u8")
                ):

                    filepath = self._direct_download(
                        url,
                        rid,
                        r.get("post_url", ""),
                        name,
                        community
                    )

                    if (
                        Path(filepath).is_file()
                        and Path(filepath).stat().st_size > 0
                    ):

                        filepath = organize_file(
                            filepath,
                            community,
                            r.get("uploader", "")
                        )

                        self.signals.completed.emit(
                            rid,
                            True,
                            filepath,
                            "Direct download completed and organized."
                        )

                        return

                    raise RuntimeError(
                        "Downloaded file could not be verified."
                    )

                if yt_dlp is None:

                    raise RuntimeError(
                        "yt-dlp is not installed."
                    )

                processing = DOWNLOAD_DIR / "_processing"

                processing.mkdir(
                    parents=True,
                    exist_ok=True
                )

                template = str(
                    processing
                    / f"{clean_filename(name)}_{rid[:8]}.%(ext)s"
                )

                def hook(d):

                    if d.get("status") == "downloading":

                        total = (
                            d.get("total_bytes")
                            or d.get("total_bytes_estimate")
                        )

                        got = d.get(
                            "downloaded_bytes",
                            0
                        )

                        pct = (
                            int(got * 100 / total)
                            if total else 0
                        )

                        self.signals.progress.emit(
                            rid,
                            min(pct, 99),
                            "Downloading"
                        )

                    elif d.get("status") == "finished":

                        self.signals.progress.emit(
                            rid,
                            100,
                            "Organizing media"
                        )

                opts = {

                    "outtmpl": template,

                    "quiet": True,
                    "no_warnings": True,

                    "noplaylist": True,

                    "retries": 2,
                    "fragment_retries": 2,

                    "socket_timeout": 35,

                    "continuedl": True,

                    "progress_hooks": [hook],

                    "http_headers": {
                        "User-Agent": USER_AGENT,
                        **({"Referer": r.get("post_url", "")} if r.get("post_url") else {})
                    }
                }

                if self.browser != "None":

                    opts["cookiesfrombrowser"] = (
                        self.browser.lower(),
                    )

                with yt_dlp.YoutubeDL(opts) as ydl:

                    info = ydl.extract_info(
                        url,
                        download=True
                    )

                    filepath = (
                        ydl.prepare_filename(info)
                        if info else ""
                    )

                    if filepath and not Path(filepath).exists():

                        stem = Path(filepath).stem

                        matches = list(
                            processing.glob(stem + ".*")
                        )

                        filepath = (
                            str(matches[0])
                            if matches else ""
                        )

                    if (
                        filepath
                        and Path(filepath).is_file()
                        and Path(filepath).stat().st_size > 0
                    ):

                        filepath = organize_file(
                            filepath,
                            community,
                            r.get("uploader", "")
                        )

                        self.signals.completed.emit(
                            rid,
                            True,
                            filepath,
                            "Extractor download completed and organized."
                        )

                        return

                    raise RuntimeError(
                        "Extractor returned without a verifiable output file."
                    )

            except Exception as exc:

                last_error = str(exc)

                low = last_error.lower()

                if any(
                    x in low
                    for x in (
                        "captcha",
                        "login required",
                        "authentication",
                        "401",
                        "403",
                        "private",
                        "deleted",
                        "unsupported url"
                    )
                ):
                    break

                if "429" in low or "rate limit" in low:

                    last_error = "RATE LIMITED: " + last_error

                    time.sleep(15)

                else:

                    time.sleep(2 * attempt)

        self.signals.completed.emit(
            rid,
            False,
            "",
            last_error or "Download failed."
        )


# ============================================================
# REDDIT SCANNER
# ============================================================

class ScanSignals(QObject):

    status = pyqtSignal(str)

    item = pyqtSignal(dict)

    finished = pyqtSignal(bool, str)


class RedditScanner(QRunnable):

    def __init__(
        self,
        subreddit,
        max_posts,
        max_scrolls,
        interval,
        browser,
        driver_path,
        profile_dir
    ):

        super().__init__()

        self.subreddit = subreddit

        self.max_posts = max_posts
        self.max_scrolls = max_scrolls
        self.interval = interval

        self.browser = browser

        self.driver_path = driver_path
        self.profile_dir = profile_dir

        self.stop_requested = False

        self.signals = ScanSignals()

        self.driver = None

    def stop(self):
        self.stop_requested = True

    def create_browser(self):

        if webdriver is None:
            raise RuntimeError(
                "Selenium is missing. Install requirements."
            )

        if self.browser == "Edge":

            options = EdgeOptions()

            options.add_argument("--start-maximized")
            options.add_argument("--disable-notifications")
            options.add_argument("--user-agent=" + USER_AGENT)

            options.add_argument(
                f"--user-data-dir={self.profile_dir}"
            )

            if (
                self.driver_path
                and Path(self.driver_path).is_file()
            ):

                service = EdgeService(
                    self.driver_path
                )

                self.driver = webdriver.Edge(
                    service=service,
                    options=options
                )

                self.signals.status.emit(
                    "Using configured EdgeDriver."
                )

            else:

                self.driver = webdriver.Edge(
                    options=options
                )

                self.signals.status.emit(
                    "EdgeDriver managed automatically by Selenium Manager."
                )

        else:

            options = ChromeOptions()

            options.add_argument("--start-maximized")
            options.add_argument("--disable-notifications")
            options.add_argument("--user-agent=" + USER_AGENT)

            options.add_argument(
                f"--user-data-dir={self.profile_dir}"
            )

            options.add_experimental_option(
                "excludeSwitches",
                ["enable-logging"]
            )

            if (
                self.driver_path
                and Path(self.driver_path).is_file()
            ):

                service = ChromeService(
                    self.driver_path
                )

                self.driver = webdriver.Chrome(
                    service=service,
                    options=options
                )

                self.signals.status.emit(
                    "Using configured ChromeDriver."
                )

            else:

                self.driver = webdriver.Chrome(
                    options=options
                )

                self.signals.status.emit(
                    "ChromeDriver managed automatically by Selenium Manager."
                )

        return self.driver

    def extract_reddit_post_media(self, post_url, fallback_url=""):
        """Return every publicly exposed media item in a Reddit post/gallery."""
        found = []
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            endpoint = post_url.rstrip("/") + ".json"
            response = requests.get(
                endpoint, params={"raw_json": 1}, headers=headers,
                timeout=(10, 25), allow_redirects=True
            )
            if response.ok:
                payload = response.json()
                listing = payload[0].get("data", {}).get("children", []) if isinstance(payload, list) else []
                if listing:
                    data = listing[0].get("data", {})
                    metadata = data.get("media_metadata") or {}
                    gallery = data.get("gallery_data", {}).get("items", []) or []
                    for gallery_item in gallery:
                        media_id = gallery_item.get("media_id")
                        entry = metadata.get(media_id, {})
                        source = entry.get("s", {})
                        candidates = [source.get("mp4"), source.get("gif"), source.get("u")]
                        for candidate in candidates:
                            if candidate and valid_url(candidate):
                                found.append(candidate.replace("&amp;", "&"))
                                break
                    # Single image/video posts and Reddit-hosted video.
                    if not found:
                        candidates = [data.get("url_overridden_by_dest"), data.get("url")]
                        video = (data.get("secure_media") or {}).get("reddit_video") or (data.get("media") or {}).get("reddit_video") or {}
                        candidates.insert(0, video.get("fallback_url"))
                        for candidate in candidates:
                            if candidate and valid_url(candidate):
                                found.append(candidate)
                                if candidate == video.get("fallback_url"):
                                    break
        except Exception:
            pass
        if not found and fallback_url and valid_url(fallback_url):
            found.append(fallback_url)
        # Stable de-duplication while preserving gallery order.
        unique = []
        seen_urls = set()
        for value in found:
            value = value.strip()
            if value and value not in seen_urls:
                seen_urls.add(value)
                unique.append(value)
        return unique

    def run(self):

        try:

            target = self.subreddit.strip()

            if not target.startswith(("http://", "https://")):

                community = target.strip("/")

                if community.startswith("r/"):
                    community = community[2:]

                target = (
                    "https://www.reddit.com/r/"
                    + community
                    + "/"
                )

            if (
                not valid_url(target)
                or "reddit.com" not in urlparse(target).netloc.lower()
            ):

                raise RuntimeError(
                    "Enter a valid public Reddit subreddit or Reddit URL."
                )

            self.profile_dir.mkdir(
                exist_ok=True,
                parents=True
            )

            self.create_browser()

            self.driver.set_page_load_timeout(60)

            self.driver.get(target)

            time.sleep(4)

            found = {}

            for step in range(self.max_scrolls + 1):

                if self.stop_requested:
                    break

                try:

                    page_text = (
                        self.driver
                        .find_element("tag name", "body")
                        .text or ""
                    ).lower()

                except Exception:

                    page_text = ""

                if any(
                    term in page_text
                    for term in (
                        "you've been blocked",
                        "too many requests",
                        "access denied",
                        "verify you are human"
                    )
                ):

                    raise RuntimeError(
                        "Reddit displayed a block, rate-limit or "
                        "verification page. Scanner stopped."
                    )

                items = self.driver.execute_script(r"""

                    const out = [];

                    const seen = new Set();

                    const toURL = value => {

                        try {

                            return new URL(
                                value,
                                window.location.href
                            );

                        } catch (error) {

                            return null;

                        }

                    };

                    const extensions = new Set([

                        "mp4", "webm", "mov", "mkv", "m4v",
                        "gif", "jpg", "jpeg", "png", "webp",
                        "avif", "m3u8", "mp3", "m4a",
                        "ogg", "wav", "flac"

                    ]);

                    const cards = Array.from(

                        document.querySelectorAll(

                            "shreddit-post, "
                            + "[data-testid='post-container'], "
                            + "article"

                        )

                    );

                    for (const el of cards) {

                        let title =
                            el.getAttribute("post-title") || "";

                        if (!title) {

                            const titleNode =
                                el.querySelector(
                                    "h1,h2,h3,[slot='title']"
                                );

                            title = titleNode
                                ? (titleNode.innerText || "").trim()
                                : "";

                        }

                        let author =
                            el.getAttribute("author") || "";

                        if (!author) {

                            const authorNode =
                                el.querySelector(
                                    "a[href*='/user/'],"
                                    + "a[href*='/u/']"
                                );

                            author = authorNode
                                ? (authorNode.innerText || "").trim()
                                : "";

                        }

                        let permalink =
                            el.getAttribute("permalink") || "";

                        if (!permalink) {

                            const postLink =
                                el.querySelector(
                                    "a[href*='/comments/']"
                                );

                            permalink = postLink
                                ? postLink.href
                                : "";

                        }

                        const postURL = toURL(permalink);

                        if (!postURL) continue;

                        if (seen.has(postURL.href)) continue;

                        seen.add(postURL.href);

                        const candidates = [];

                        for (
                            const link of
                            el.querySelectorAll("a[href]")
                        ) {

                            candidates.push(link.href);

                        }

                        for (
                            const node of
                            el.querySelectorAll(
                                "video,source,img,audio"
                            )
                        ) {

                            const candidate =
                                node.currentSrc
                                || node.src
                                || node.getAttribute("src");

                            if (candidate) {
                                candidates.unshift(candidate);
                            }

                        }

                        let media = "";

                        for (const candidate of candidates) {

                            const mediaURL = toURL(candidate);

                            if (!mediaURL) continue;

                            const host =
                                mediaURL.hostname.toLowerCase();

                            const path =
                                mediaURL.pathname.toLowerCase();

                            const lastPart =
                                path.split("/").pop() || "";

                            const dot =
                                lastPart.lastIndexOf(".");

                            const extension = dot >= 0
                                ? lastPart.slice(dot + 1)
                                : "";

                            if (
                                host === "preview.redd.it"
                                || host.endsWith(".preview.redd.it")
                            ) {
                                continue;
                            }

                            const redditImage =
                                host === "i.redd.it";

                            const redditVideo =
                                host === "v.redd.it";

                            const knownExtension =
                                extensions.has(extension);

                            const redgifs =
                                host === "redgifs.com"
                                || host.endsWith(".redgifs.com");

                            if (
                                redditImage
                                || redditVideo
                                || knownExtension
                                || redgifs
                            ) {

                                media = mediaURL.href;

                                break;

                            }

                        }

                        const pathParts =
                            postURL.pathname.split("/");

                        const rIndex =
                            pathParts.indexOf("r");

                        const community =
                            rIndex >= 0
                            && pathParts.length > rIndex + 1
                                ? pathParts[rIndex + 1]
                                : "";

                        out.push({

                            post_name: title,

                            uploader: author.replace(
                                /^u\//,
                                ""
                            ),

                            community: community,

                            post_url: postURL.href,

                            media_url: media

                        });

                    }

                    return out;

                """)

                for item in items or []:

                    post = item.get(
                        "post_url",
                        ""
                    )

                    if post and post not in found:

                        found[post] = item
                        media_items = self.extract_reddit_post_media(
                            post, item.get("media_url", "")
                        )
                        if not media_items:
                            media_items = [""]
                        for media_number, media_url in enumerate(media_items, 1):
                            expanded = dict(item)
                            expanded["media_url"] = media_url
                            expanded["media_index"] = media_number
                            expanded["media_count"] = len(media_items)
                            self.signals.item.emit(expanded)

                self.signals.status.emit(
                    f"{self.browser} | Scroll {step}/{self.max_scrolls} "
                    f"| {len(found)} unique posts found"
                )

                if len(found) >= self.max_posts:
                    break

                if step < self.max_scrolls:

                    self.driver.execute_script(
                        "window.scrollBy("
                        "0, Math.max(650, "
                        "window.innerHeight * 0.85)"
                        ");"
                    )

                    time.sleep(self.interval)

            self.signals.finished.emit(
                True,
                f"Scan finished: {len(found)} unique posts indexed."
            )

        except Exception as exc:

            self.signals.finished.emit(
                False,
                str(exc)
            )

        finally:

            if self.driver:

                try:
                    self.driver.quit()

                except Exception:
                    pass


# ============================================================
# MAIN GUI
# ============================================================

class MainWindow(QMainWindow):

    def __init__(self):

        super().__init__()

        self.setWindowTitle(
            f"Reddit Media Downloader {APP_VERSION}"
        )

        self.resize(1500, 900)

        self.pool = QThreadPool.globalInstance()

        self.records = load_json(
            HISTORY_FILE,
            []
        )

        self.index_records = load_json(
            INDEX_FILE,
            []
        )

        self.settings = load_json(
            SETTINGS_FILE,
            {}
        )

        self.active_ids = set()

        self.workers = []

        self.scanner = None
        self.scan_worker = None

        self.build_interface()

        self.refresh_queue()
        self.refresh_index()

        self.queue_timer = QTimer(self)

        self.queue_timer.timeout.connect(
            self.pump_queue
        )

        self.queue_timer.start(500)

    # ========================================================
    # INTERFACE
    # ========================================================

    def build_interface(self):

        central = QWidget()

        self.setCentralWidget(central)

        root = QVBoxLayout(central)

        head = QHBoxLayout()

        title = QLabel(
            "REDDIT MEDIA DOWNLOADER"
        )

        title.setStyleSheet(
            "font-size:25px;"
            "font-weight:bold;"
            "color:#ff4500;"
        )

        version = QLabel(
            f"{APP_VERSION} • CHROME + EDGE"
        )

        version.setStyleSheet(
            "color:#ff956f;"
            "font-weight:bold;"
        )

        head.addWidget(title)
        head.addStretch()
        head.addWidget(version)

        root.addLayout(head)

        self.tabs = QTabWidget()

        root.addWidget(self.tabs)

        self.queue_tab = QWidget()
        self.index_tab = QWidget()
        self.settings_tab = QWidget()

        self.tabs.addTab(
            self.queue_tab,
            "Download Queue"
        )

        self.tabs.addTab(
            self.index_tab,
            "Indexed Posts"
        )

        self.tabs.addTab(
            self.settings_tab,
            "Settings"
        )

        self.build_queue_tab()
        self.build_index_tab()
        self.build_settings_tab()

        self.activity = QTextEdit()

        self.activity.setReadOnly(True)

        self.activity.setMaximumHeight(95)

        root.addWidget(
            QLabel("Activity")
        )

        root.addWidget(
            self.activity
        )

        self.setStyleSheet("""
        QWidget {
            background:#181818;
            color:#eee;
            font-size:10pt;
        }

        QTabWidget::pane {
            border:1px solid #444;
        }

        QTabBar::tab {
            background:#292929;
            padding:10px 16px;
            color:white;
        }

        QTabBar::tab:selected {
            background:#ff4500;
            font-weight:bold;
        }

        QPushButton {
            background:#333;
            color:white;
            padding:8px 12px;
            border:1px solid #555;
            border-radius:5px;
        }

        QPushButton:hover {
            background:#ff4500;
        }

        QLineEdit,QTextEdit,QSpinBox,QComboBox {
            background:#252525;
            color:white;
            border:1px solid #555;
            padding:6px;
        }

        QTableWidget {
            background:#202020;
            alternate-background-color:#292929;
            gridline-color:#444;
            selection-background-color:#773018;
        }

        QHeaderView::section {
            background:#303030;
            color:#ff7849;
            padding:7px;
            font-weight:bold;
        }

        QGroupBox {
            border:1px solid #555;
            margin-top:10px;
            padding:10px;
        }
        """)

    # ========================================================
    # QUEUE TAB
    # ========================================================

    def build_queue_tab(self):

        layout = QVBoxLayout(
            self.queue_tab
        )

        bar = QHBoxLayout()

        buttons = [

            ("Add URL", self.add_single),

            ("Add Multiple URLs", self.add_batch),

            ("Import CSV / JSON", self.import_file),

            ("Retry Failed", self.retry_failed),

            ("Remove Selected", self.remove_selected),

            ("Export ZIP", self.export_zip),

            ("Export Manifest", self.export_manifest)

        ]

        for label, fn in buttons:

            button = QPushButton(label)

            button.clicked.connect(fn)

            bar.addWidget(button)

        layout.addLayout(bar)

        self.queue_table = QTableWidget(
            0,
            10
        )

        self.queue_table.setHorizontalHeaderLabels([

            "Post Name",
            "Uploaded By",
            "Community",
            "Category",
            "Status",
            "Progress",
            "Download URL",
            "Saved File",
            "Source",
            "Details"

        ])

        self.queue_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )

        self.queue_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers
        )

        self.queue_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )

        self.queue_table.horizontalHeader().setStretchLastSection(
            True
        )

        layout.addWidget(
            self.queue_table
        )

        self.queue_status = QLabel()

        layout.addWidget(
            self.queue_status
        )

    # ========================================================
    # INDEX TAB
    # ========================================================

    def build_index_tab(self):

        layout = QVBoxLayout(
            self.index_tab
        )

        top = QHBoxLayout()

        self.subreddit_input = QLineEdit(
            "r/all"
        )

        self.subreddit_input.setPlaceholderText(
            "r/videos or https://www.reddit.com/r/videos/"
        )

        self.open_browser_btn = QPushButton(
            "Open Browser + Start Scraper"
        )

        self.stop_scan_btn = QPushButton(
            "Stop Scan"
        )

        self.stop_scan_btn.setEnabled(False)

        self.scan_visible_btn = QPushButton(
            "Scan Current Community"
        )

        self.add_selected_btn = QPushButton(
            "Add Selected to Queue"
        )

        self.add_all_btn = QPushButton(
            "Add All to Queue"
        )

        self.clear_index_btn = QPushButton(
            "Clear Index"
        )

        top.addWidget(
            QLabel("Community:")
        )

        top.addWidget(
            self.subreddit_input,
            1
        )

        top.addWidget(
            self.open_browser_btn
        )

        top.addWidget(
            self.stop_scan_btn
        )

        top.addWidget(
            self.scan_visible_btn
        )

        layout.addLayout(top)

        actions = QHBoxLayout()

        for button in (
            self.add_selected_btn,
            self.add_all_btn,
            self.clear_index_btn
        ):

            actions.addWidget(button)

        layout.addLayout(actions)

        self.index_table = QTableWidget(
            0,
            6
        )

        self.index_table.setHorizontalHeaderLabels([

            "Post Name",
            "Uploaded By",
            "Community",
            "Post URL",
            "Media URL (each gallery item)",
            "Method"

        ])

        self.index_table.setSelectionBehavior(
            QAbstractItemView.SelectRows
        )

        self.index_table.setSelectionMode(
            QAbstractItemView.ExtendedSelection
        )

        self.index_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )

        self.index_table.horizontalHeader().setStretchLastSection(
            True
        )

        layout.addWidget(
            self.index_table
        )

        self.open_browser_btn.clicked.connect(
            self.start_scan
        )

        self.stop_scan_btn.clicked.connect(
            self.stop_scan
        )

        self.scan_visible_btn.clicked.connect(
            self.start_scan
        )

        self.add_selected_btn.clicked.connect(
            self.add_selected_index
        )

        self.add_all_btn.clicked.connect(
            self.add_all_index
        )

        self.clear_index_btn.clicked.connect(
            self.clear_index
        )

    # ========================================================
    # SETTINGS TAB
    # ========================================================

    def build_settings_tab(self):

        layout = QVBoxLayout(
            self.settings_tab
        )

        group = QGroupBox(
            "Application, Browser and Download Settings"
        )

        form = QFormLayout(group)

        self.parallel_spin = QSpinBox()

        self.parallel_spin.setRange(1, 64)

        self.parallel_spin.setValue(
            int(self.settings.get("parallel", 4))
        )

        self.batch_spin = QSpinBox()

        self.batch_spin.setRange(1, 10000)

        self.batch_spin.setValue(
            int(self.settings.get("batch", 20))
        )

        self.cookie_browser = QComboBox()

        self.cookie_browser.addItems([
            "None",
            "Chrome",
            "Edge",
            "Firefox"
        ])

        self.cookie_browser.setCurrentText(
            self.settings.get("cookies", "None")
        )

        self.browser_choice = QComboBox()

        self.browser_choice.addItems([
            "Chrome",
            "Edge"
        ])

        self.browser_choice.setCurrentText(
            self.settings.get("browser", "Chrome")
        )

        self.max_posts_spin = QSpinBox()

        self.max_posts_spin.setRange(1, 10000)

        self.max_posts_spin.setValue(
            int(self.settings.get("max_posts", 200))
        )

        self.max_scroll_spin = QSpinBox()

        self.max_scroll_spin.setRange(1, 1000)

        self.max_scroll_spin.setValue(
            int(self.settings.get("max_scrolls", 40))
        )

        self.interval_spin = QSpinBox()

        self.interval_spin.setRange(2, 60)

        self.interval_spin.setValue(
            int(self.settings.get("scroll_interval", 3))
        )

        self.driver_path = QLineEdit(
            self.settings.get("driver_path", "")
        )

        driver_row = QHBoxLayout()

        driver_row.addWidget(
            self.driver_path
        )

        browse = QPushButton(
            "Browse Driver…"
        )

        browse.clicked.connect(
            self.browse_driver
        )

        driver_row.addWidget(browse)

        driver_wrap = QWidget()

        driver_wrap.setLayout(
            driver_row
        )

        form.addRow(
            "Scanner Browser:",
            self.browser_choice
        )

        form.addRow(
            "Parallel Downloads:",
            self.parallel_spin
        )

        form.addRow(
            "Queue Batch Size:",
            self.batch_spin
        )

        form.addRow(
            "yt-dlp Browser Cookies:",
            self.cookie_browser
        )

        form.addRow(
            "Maximum Posts per Scan:",
            self.max_posts_spin
        )

        form.addRow(
            "Maximum Scrolls:",
            self.max_scroll_spin
        )

        form.addRow(
            "Seconds Between Scrolls:",
            self.interval_spin
        )

        form.addRow(
            "ChromeDriver / EdgeDriver:",
            driver_wrap
        )

        layout.addWidget(group)

        folders = QGroupBox(
            "Automatic Media Organization"
        )

        folder_layout = QVBoxLayout(folders)

        folder_info = QLabel(
            "Every completed download is sorted automatically:\n\n"
            "Images  → Images\n"
            "GIF files → GIFs\n"
            "Video files → Videos\n"
            "Audio files → Audios\n\n"
            "Folder layout: downloads/r_SUBREDDIT/Images|GIFs|Videos|Audios/UPLOADER/\n"
            "Every detected gallery item is queued separately. Unknown authors use Unknown_Uploader."
        )

        folder_info.setWordWrap(True)

        folder_layout.addWidget(folder_info)

        layout.addWidget(folders)

        info = QLabel(
            "Chrome and Edge open in separate visible windows. "
            "Leave the driver path empty to use Selenium Manager. "
            "It automatically manages the appropriate browser driver. "
            "If you specify a manual driver, select the executable "
            "matching the chosen scanner browser. Each browser has "
            "its own local profile. Sign in there yourself if needed. "
            "The scanner does not bypass CAPTCHA, private communities, "
            "login restrictions or rate limits."
        )

        info.setWordWrap(True)

        layout.addWidget(info)

        layout.addStretch()

        for widget in (
            self.parallel_spin,
            self.batch_spin,
            self.cookie_browser,
            self.browser_choice,
            self.max_posts_spin,
            self.max_scroll_spin,
            self.interval_spin
        ):

            if hasattr(widget, "valueChanged"):

                widget.valueChanged.connect(
                    self.save_settings
                )

            else:

                widget.currentTextChanged.connect(
                    self.save_settings
                )

        self.driver_path.textChanged.connect(
            self.save_settings
        )

    def browse_driver(self):

        browser = self.browser_choice.currentText()

        if browser == "Edge":

            title = "Select Microsoft Edge WebDriver"

            file_filter = (
                "EdgeDriver (msedgedriver.exe);;"
                "Executable (*.exe)"
            )

        else:

            title = "Select Google ChromeDriver"

            file_filter = (
                "ChromeDriver (chromedriver.exe);;"
                "Executable (*.exe)"
            )

        path, _ = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            file_filter
        )

        if path:
            self.driver_path.setText(path)

    def save_settings(self, *_):

        self.settings = {

            "parallel": self.parallel_spin.value(),

            "batch": self.batch_spin.value(),

            "cookies": self.cookie_browser.currentText(),

            "browser": self.browser_choice.currentText(),

            "max_posts": self.max_posts_spin.value(),

            "max_scrolls": self.max_scroll_spin.value(),

            "scroll_interval": self.interval_spin.value(),

            "driver_path": self.driver_path.text().strip()

        }

        save_json(
            SETTINGS_FILE,
            self.settings
        )

    # ========================================================
    # SCANNER CONTROLS
    # ========================================================

    def start_scan(self):

        if self.scan_worker:

            QMessageBox.information(
                self,
                "Scanner running",
                "A scan is already running."
            )

            return

        self.save_settings()

        browser = self.browser_choice.currentText()

        if browser == "Edge":

            profile = EDGE_PROFILE

        else:

            profile = CHROME_PROFILE

        self.scan_worker = RedditScanner(

            self.subreddit_input.text(),

            self.max_posts_spin.value(),

            self.max_scroll_spin.value(),

            self.interval_spin.value(),

            browser,

            self.driver_path.text().strip(),

            profile

        )

        self.scan_worker.signals.status.connect(
            self.log
        )

        self.scan_worker.signals.item.connect(
            self.add_index_item
        )

        self.scan_worker.signals.finished.connect(
            self.scan_finished
        )

        self.stop_scan_btn.setEnabled(True)

        self.open_browser_btn.setEnabled(False)

        self.scan_visible_btn.setEnabled(False)

        self.log(
            f"Launching separate {browser} window…"
        )

        self.pool.start(
            self.scan_worker
        )

    def stop_scan(self):

        if self.scan_worker:

            self.scan_worker.stop()

            self.log(
                "Stop requested; browser will close after current wait."
            )

    def scan_finished(self, ok, message):

        self.scan_worker = None

        self.stop_scan_btn.setEnabled(False)

        self.open_browser_btn.setEnabled(True)

        self.scan_visible_btn.setEnabled(True)

        self.log(message)

        if not ok:

            QMessageBox.warning(
                self,
                "Scraper",
                message
            )

    # ========================================================
    # INDEX MANAGEMENT
    # ========================================================

    def add_index_item(self, item):

        post = item.get(
            "post_url",
            ""
        )

        if not valid_url(post):
            return

        media = item.get("media_url", "")
        identity = (post.lower(), media.lower())
        known = {
            (x.get("post_url", "").lower(), x.get("media_url", "").lower())
            for x in self.index_records
        }
        if identity in known:
            return

        method = (
            "direct"
            if is_direct_media(media)
            else ("extractor" if media else "post")
        )

        self.index_records.append({

            "post_name": item.get("post_name", ""),

            "uploader": item.get("uploader", ""),

            "community": item.get("community", ""),

            "post_url": post,

            "media_url": media,

            "download_method": method,
            "media_index": item.get("media_index", 1),
            "media_count": item.get("media_count", 1),

            "indexed": timestamp()

        })

        self.save_index()

        self.refresh_index()

    def scan_current_page(self):

        self.start_scan()

    def refresh_index(self):

        self.index_table.setRowCount(0)

        for record in self.index_records:

            row = self.index_table.rowCount()

            self.index_table.insertRow(row)

            method = record.get(
                "download_method",
                (
                    "direct"
                    if is_direct_media(
                        record.get("media_url", "")
                    )
                    else "extractor"
                )
            )

            values = [

                record.get("post_name", ""),

                record.get("uploader", ""),

                record.get("community", ""),

                record.get("post_url", ""),

                record.get("media_url", ""),

                method

            ]

            for col, value in enumerate(values):

                self.index_table.setItem(
                    row,
                    col,
                    QTableWidgetItem(str(value))
                )

    def index_to_record(self, item):

        post = item.get(
            "post_url",
            ""
        )

        media = item.get(
            "media_url",
            ""
        )

        if media and media.rstrip("/") == post.rstrip("/"):
            media = ""

        direct = is_direct_media(media)
        chosen = media if media else post
        method = "direct" if direct else "extractor"

        return make_record(

            chosen,

            item.get("post_name", ""),

            item.get("uploader", ""),

            item.get("community", ""),

            "General",

            post,

            method,

            media

        )

    def add_selected_index(self):

        rows = sorted({

            item.row()
            for item in self.index_table.selectedItems()

        })

        self.add_records([

            self.index_to_record(
                self.index_records[index]
            )

            for index in rows

            if index < len(self.index_records)

        ])

    def add_all_index(self):

        self.add_records([

            self.index_to_record(item)

            for item in self.index_records

        ])

    def clear_index(self):

        if QMessageBox.question(
            self,
            "Clear Index",
            "Delete all indexed posts?"
        ) == QMessageBox.Yes:

            self.index_records = []

            self.save_index()

            self.refresh_index()

    # ========================================================
    # ADD URLS
    # ========================================================

    def get_text(self, title, prompt):

        from PyQt5.QtWidgets import QInputDialog

        return QInputDialog.getText(
            self,
            title,
            prompt
        )

    def add_single(self):

        url, ok = self.get_text(
            "Add URL",
            "Enter Reddit post URL or direct media URL:"
        )

        if ok:

            url = url.strip()

            if not valid_url(url):

                QMessageBox.warning(
                    self,
                    "Invalid URL",
                    "Enter a valid HTTP/HTTPS URL."
                )

                return

            self.add_records([

                make_record(
                    url,
                    post_url=(
                        url
                        if source_for(url) == "Reddit"
                        else ""
                    )
                )

            ])

    def add_batch(self):

        from PyQt5.QtWidgets import QInputDialog

        text, ok = QInputDialog.getMultiLineText(
            self,
            "Add Multiple URLs",
            "Enter one URL per line:"
        )

        if ok:

            records = []

            for line in text.splitlines():

                url = line.strip()

                if valid_url(url):

                    records.append(

                        make_record(
                            url,
                            post_url=(
                                url
                                if source_for(url) == "Reddit"
                                else ""
                            )
                        )

                    )

            self.add_records(records)

    def add_records(self, records):

        known = {
            r.get("url", "").lower()
            for r in self.records
        }

        added = 0

        for record in records:

            url = (
                record.get("url") or ""
            ).strip()

            if not valid_url(url):
                continue

            if url.lower() in known:
                continue

            if not record.get("download_method"):

                record["download_method"] = (
                    "direct"
                    if is_direct_media(url)
                    else "extractor"
                )

            if (
                record["download_method"] == "extractor"
                and not record.get("post_url")
            ):
                record["post_url"] = url

            if record.get("media_url") and record.get("post_url"):
                record["url"] = record["media_url"]

            if (
                record["download_method"] == "direct"
                and not record.get("media_url")
            ):

                record["media_url"] = url

            if not record.get("community"):
                record["community"] = "General"

            self.records.append(record)

            known.add(url.lower())

            added += 1

        self.save_history()

        self.refresh_queue()

        self.log(
            f"Added {added} download(s)."
        )

    # ========================================================
    # DOWNLOAD QUEUE MANAGEMENT
    # ========================================================

    def pump_queue(self):

        slots = (

            min(
                self.parallel_spin.value(),
                self.batch_spin.value()
            )

            - len(self.active_ids)

        )

        if slots <= 0:
            return

        pending = [

            record

            for record in self.records

            if (
                record.get("status") == "Pending"
                and record["id"] not in self.active_ids
            )

        ]

        for record in pending[:slots]:

            self.start_download(record)

    def start_download(self, record):

        record["status"] = "Downloading"

        record["details"] = "Starting"

        record["attempts"] = record.get("attempts", 0) + 1

        self.active_ids.add(
            record["id"]
        )

        worker = DownloadWorker(
            record,
            self.cookie_browser.currentText()
        )

        self.workers.append(worker)

        worker.signals.progress.connect(
            self.download_progress
        )

        worker.signals.completed.connect(
            self.download_completed
        )

        self.save_history()

        self.refresh_queue()

        self.pool.start(worker)

    def download_progress(self, rid, pct, status):

        for record in self.records:

            if record["id"] == rid:

                record["progress"] = pct

                record["details"] = status

                break

        self.refresh_queue()

    def download_completed(
        self,
        rid,
        success,
        filepath,
        details
    ):

        self.active_ids.discard(rid)

        for record in self.records:

            if record["id"] == rid:

                if (
                    success
                    and filepath
                    and Path(filepath).is_file()
                    and Path(filepath).stat().st_size > 0
                ):

                    record["status"] = "Completed"

                    record["progress"] = 100

                    record["saved_file"] = filepath

                    record["category"] = media_category(filepath)

                    record["details"] = details

                else:

                    record["status"] = "Failed"

                    record["progress"] = 0

                    record["details"] = (
                        details
                        if not success
                        else "Output verification failed"
                    )

                break

        self.save_history()

        self.refresh_queue()

        self.workers = [

            worker

            for worker in self.workers

            if worker.record["id"] != rid

        ]

        self.log(
            "Download finished: " + details
        )

    def retry_failed(self):

        for record in self.records:

            if record.get("status") == "Failed":

                record["status"] = "Pending"

                record["progress"] = 0

                record["details"] = "Retry queued"

        self.save_history()

        self.refresh_queue()

    def remove_selected(self):

        rows = sorted(

            {
                item.row()
                for item in self.queue_table.selectedItems()
            },

            reverse=True

        )

        ids = set()

        for row in rows:

            if row < len(self.records):

                rid = self.records[row]["id"]

                if rid not in self.active_ids:
                    ids.add(rid)

        self.records = [

            record

            for record in self.records

            if record["id"] not in ids

        ]

        self.save_history()

        self.refresh_queue()

    def refresh_queue(self):

        self.queue_table.setRowCount(0)

        pending = 0
        completed = 0
        failed = 0

        for record in self.records:

            status = record.get(
                "status",
                "Pending"
            )

            if status in ("Pending", "Downloading"):
                pending += 1

            elif status == "Completed":
                completed += 1

            elif status == "Failed":
                failed += 1

            row = self.queue_table.rowCount()

            self.queue_table.insertRow(row)

            values = [

                record.get("post_name", ""),

                record.get("uploader", ""),

                record.get("community", ""),

                record.get("category", ""),

                status,

                str(record.get("progress", 0)) + "%",

                record.get("url", ""),

                record.get("saved_file", ""),

                record.get("source", ""),

                record.get("details", "")

            ]

            for col, value in enumerate(values):

                self.queue_table.setItem(
                    row,
                    col,
                    QTableWidgetItem(str(value))
                )

        self.queue_status.setText(

            f"Pending: {pending} | "
            f"Completed: {completed} | "
            f"Failed: {failed}"

        )

    # ========================================================
    # IMPORT / EXPORT
    # ========================================================

    def import_file(self):

        path, _ = QFileDialog.getOpenFileName(

            self,
            "Import",
            "",
            "CSV / JSON (*.csv *.json)"

        )

        if not path:
            return

        try:

            if path.lower().endswith(".json"):

                data = load_json(
                    Path(path),
                    []
                )

                if isinstance(data, dict):
                    data = data.get("records", [])

            else:

                with open(
                    path,
                    "r",
                    encoding="utf-8-sig",
                    newline=""
                ) as f:

                    data = list(
                        csv.DictReader(f)
                    )

            imported = []

            for item in data:

                post = item.get(
                    "post_url",
                    ""
                )

                media = item.get(
                    "media_url",
                    ""
                )

                url = item.get("url") or (

                    media
                    if is_direct_media(media)
                    else post

                )

                if valid_url(url):

                    method = (

                        "direct"
                        if is_direct_media(url)
                        else "extractor"

                    )

                    imported.append(

                        make_record(

                            url,

                            item.get("post_name", ""),

                            item.get("uploader", ""),

                            item.get("community", ""),

                            item.get("category", "General"),

                            post,

                            method,

                            media

                        )

                    )

            self.add_records(imported)

        except Exception as exc:

            QMessageBox.critical(
                self,
                "Import error",
                str(exc)
            )

    def export_manifest(self):

        path, _ = QFileDialog.getSaveFileName(

            self,
            "Export Manifest",
            "manifest.json",
            "JSON (*.json)"

        )

        if path:

            save_json(
                Path(path),
                self.records
            )

    def export_zip(self):

        path, _ = QFileDialog.getSaveFileName(

            self,
            "Export ZIP",
            "reddit_downloads.zip",
            "ZIP (*.zip)"

        )

        if not path:
            return

        try:

            with zipfile.ZipFile(
                path,
                "w",
                zipfile.ZIP_DEFLATED
            ) as archive:

                for record in self.records:

                    saved = record.get(
                        "saved_file",
                        ""
                    )

                    if saved and os.path.isfile(saved):

                        saved_path = Path(saved)

                        try:

                            archive_name = str(
                                saved_path.relative_to(DOWNLOAD_DIR)
                            )

                        except ValueError:

                            archive_name = saved_path.name

                        archive.write(
                            saved,
                            archive_name
                        )

                archive.writestr(

                    "download_manifest.json",

                    json.dumps(
                        self.records,
                        indent=2,
                        ensure_ascii=False
                    )

                )

            QMessageBox.information(
                self,
                "Export complete",
                "ZIP created successfully."
            )

        except Exception as exc:

            QMessageBox.critical(
                self,
                "ZIP error",
                str(exc)
            )

    # ========================================================
    # PERSISTENCE / LOGGING
    # ========================================================

    def save_history(self):

        save_json(
            HISTORY_FILE,
            self.records
        )

    def save_index(self):

        save_json(
            INDEX_FILE,
            self.index_records
        )

    def log(self, message):

        self.activity.append(

            f"[{datetime.now().strftime('%H:%M:%S')}] "
            f"{message}"

        )

    def closeEvent(self, event):

        if self.active_ids or self.scan_worker:

            if QMessageBox.question(

                self,
                "Work in progress",

                "Downloads or scanning are still running. "
                "Close anyway?"

            ) != QMessageBox.Yes:

                event.ignore()

                return

        if self.scan_worker:

            self.scan_worker.stop()

        self.save_settings()

        self.save_history()

        self.save_index()

        event.accept()


# ============================================================
# APPLICATION ENTRY POINT
# ============================================================

def main():

    app = QApplication(sys.argv)

    app.setApplicationName(
        "Reddit Media Downloader"
    )

    window = MainWindow()

    window.show()

    sys.exit(
        app.exec_()
    )


if __name__ == "__main__":

    main()