"""Archive Qt's version-checked attribution pages as offline plain-text notices.

Only the Python standard library is used. The default cache is outside the
repository. Given the same cached input, output bytes and ordering are stable.
This is a documentation superset, not a binary composition/compliance audit.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
from html.parser import HTMLParser
import io
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import time
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import zipfile


ROOT = Path(__file__).resolve().parents[2]
BASE = "https://doc.qt.io/qt-6/"
INDEXES = (BASE + "licenses-used-in-qt.html", BASE + "qtwebengine-licensing.html")
SUPPLEMENTS = {
    "GNU-LGPL-2.1.txt": "https://www.gnu.org/licenses/old-licenses/lgpl-2.1.txt",
    "GNU-LGPL-2.0.txt": "https://www.gnu.org/licenses/old-licenses/lgpl-2.0.txt",
    "GNU-GPL-2.0.txt": "https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AttributionParser(HTMLParser):
    """Preserve complete preformatted license blocks and surrounding notices."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.pre = 0
        self.title = False
        self.titles: list[str] = []
        self.body: list[str] = []
        self.links: set[str] = set()
        self.pre_blocks = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        if tag == "title":
            self.title = True
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "descr" in attributes.get("class", "").split():
                self.depth = 1
        if not self.depth:
            return
        if tag == "a" and "href" in attributes:
            self.links.add(attributes["href"])
        if tag == "pre":
            self.pre += 1
            self.pre_blocks += 1
            self.body.append("\n")
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"}:
            self.body.append("\n")
        elif tag in {"td", "th"}:
            self.body.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.title = False
        if not self.depth:
            return
        if tag == "pre":
            self.pre -= 1
            self.body.append("\n")
        elif tag in {"p", "li", "h1", "h2", "h3", "h4", "tr", "blockquote"}:
            self.body.append("\n")
        if tag == "div":
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title:
            self.titles.append(data)
        if self.depth:
            self.body.append(data if self.pre else re.sub(r"\s+", " ", data))

    def text(self) -> str:
        # QDoc's Chromium generator double-escapes entities in some <pre> blocks.
        value = "".join(self.body).replace("\r\n", "\n").replace("\r", "\n")
        value = html.unescape(value)
        return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", value).strip() + "\n"


class Cache:
    def __init__(self, directory: Path, offline: bool, refresh: bool) -> None:
        self.directory, self.offline, self.refresh = directory, offline, refresh
        directory.mkdir(parents=True, exist_ok=True)

    def get(self, url: str) -> bytes:
        # Chromium component names can exceed Windows' conservative path limit.
        name = sha256(url.encode("utf-8"))[:16] + "-" + Path(urlparse(url).path).name[:80]
        path = self.directory / name
        if path.is_file() and not self.refresh:
            return path.read_bytes()
        if self.offline:
            raise RuntimeError(f"Offline cache is missing: {url}")
        for attempt in range(3):
            try:
                request = Request(url, headers={"User-Agent": "ChordCue-license-archiver/1.0"})
                with urlopen(request, timeout=45) as response:
                    data = response.read()
                    if response.status != 200 or not data:
                        raise RuntimeError(f"Invalid license response: {url}")
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
                return data
            except Exception as error:
                if attempt == 2:
                    raise RuntimeError(f"Unable to archive {url}: {error}") from error
                time.sleep(attempt + 1)
        raise AssertionError("unreachable")


def parse_page(url: str, raw: bytes, version: str) -> tuple[dict, AttributionParser]:
    source = raw.decode("utf-8")
    page = AttributionParser()
    page.feed(source)
    title = html.unescape("".join(page.titles)).strip()
    # The umbrella index title omits the patch version; its body states it.
    evidence = "patch version in page title"
    if f"components in Qt {version}," in source:
        evidence = "patch version explicitly stated by umbrella index"
    elif "Qt " + version in title:
        pass
    elif title.endswith("Qt " + version.rsplit(".", 1)[0]) and url not in INDEXES:
        evidence = "minor version in page title; patch inherited from verified umbrella index"
    else:
        raise RuntimeError(f"Qt version mismatch; expected {version}: {url} ({title})")
    text = page.text()
    if len(text.strip()) < 30:
        raise RuntimeError(f"Missing attribution body: {url}")
    return {
        "url": url,
        "title": title,
        "versionEvidence": evidence,
        "sourceSha256": sha256(raw),
        "textSha256": sha256(text.encode("utf-8")),
        "textBytes": len(text.encode("utf-8")),
        "preformattedLicenseBlocks": page.pre_blocks,
    }, page


def attribution_urls(page: AttributionParser) -> set[str]:
    result = set()
    for link in page.links:
        url = urljoin(BASE, link).split("#", 1)[0]
        path = urlparse(url).path
        if url.startswith(BASE) and path.endswith(".html") and (
            "-attribution-" in path or "-3rdparty-" in path
        ):
            result.add(url)
    return result


def pyside_notices(version: str, cache: Cache) -> tuple[dict, bytes]:
    # Use Qt's primary download host: regional MirrorBrain redirects can fail
    # independently of the official source file. Archive contents pin the version.
    filename = f"pyside-setup-everywhere-src-{version}.zip"
    path = f"official_releases/QtForPython/pyside6/PySide6-{version}-src/{filename}"
    url = "https://master.qt.io/" + path
    raw = cache.get(url)
    root = f"pyside-setup-everywhere-src-{version}/"
    manifest = {"version": version, "url": url,
                "canonicalDownloadUrl": "https://download.qt.io/" + path,
                "sourceArchiveSha256": sha256(raw), "sourceArchiveBytes": len(raw)}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        if not names or any(not name.startswith(root) for name in names):
            raise RuntimeError("PySide source archive root/version mismatch")
        chosen = {
            name for name in names if "/LICENSES/" in name
            or PurePosixPath(name).name.lower().startswith("copying")
            or PurePosixPath(name).name.lower() in {"license.txt", "licensecomment.txt", "qt_attribution.json"}
        }
        for name in sorted(names):
            if not name.endswith("/qt_attribution.json"):
                continue
            # Upstream attribution files permit literal newlines inside strings.
            entries = json.loads(archive.read(name).decode("utf-8"), strict=False)
            for entry in entries if isinstance(entries, list) else [entries]:
                reference = entry.get("LicenseFile")
                if reference:
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), reference))
                    if not target.startswith(root) or target not in names:
                        raise RuntimeError(f"PySide attribution has missing LicenseFile: {target}")
                    chosen.add(target)
        records, chunks = [], [
            f"PYSIDE / SHIBOKEN {version}: SOURCE LICENSE AND ATTRIBUTION NOTICES\n\n"
            "These complete license/attribution files come from Qt's matching official\n"
            "source archive. The selection includes the LICENSES catalog and examples;\n"
            "it is a source-notice superset, not a claim all examples are in ChordCue.\n"
            "Every LicenseFile referenced by qt_attribution.json is included. Some\n"
            "upstream-designated LicenseFiles are source headers with license comments.\n"
            "Source: " + url + "\nArchive SHA-256: " + sha256(raw) + "\n\n"
        ]
        for name in sorted(chosen):
            data = archive.read(name)
            text = data.decode("utf-8-sig").replace("\r\r\n", "\n").replace("\r\n", "\n")
            records.append({"path": name, "sourceSha256": sha256(data),
                            "textSha256": sha256(text.encode("utf-8"))})
            chunks.extend(["=" * 80 + "\n", name + "\n\n", text, "\n\n"])
        result = "".join(chunks).encode("utf-8")
        manifest.update({"noticeFileCount": len(records), "files": records,
                         "aggregate": {"file": f"PySide6-{version}-NOTICES.txt",
                                       "bytes": len(result), "sha256": sha256(result)}})
        return manifest, result


def collect(version: str, cache: Cache, output: Path, workers: int) -> dict:
    indexes, pending, records, pages = [], set(), {}, {}
    for url in INDEXES:
        record, page = parse_page(url, cache.get(url), version)
        indexes.append(record)
        pending.update(attribution_urls(page))
    if len(pending) < 100:
        raise RuntimeError("Attribution index unexpectedly short; review the upstream layout")
    while pending:
        urls = sorted(pending)

        def read(url):
            try:
                return url, parse_page(url, cache.get(url), version)
            except Exception as error:
                return url, error

        failures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for url, result in pool.map(read, urls):
                if isinstance(result, Exception):
                    failures.append(f"{url}: {result}")
                    continue
                record, page = result
                records[url], pages[url] = record, page
                if len(records) % 25 == 0:
                    print(f"Validated {len(records)} attribution pages", flush=True)
        if failures:
            raise RuntimeError("Archive incomplete; rerun to retry cached misses:\n" + "\n".join(failures))
        pending = set().union(*(attribution_urls(page) for page in pages.values())) - records.keys()
        print(f"Collected {len(records)} Qt {version} attribution pages", flush=True)

    supplements, supplemental_data = [], {}
    for name, url in sorted(SUPPLEMENTS.items()):
        raw = cache.get(url)
        text = raw.decode("utf-8").replace("\r\n", "\n")
        if len(text) < 15_000 or "GENERAL PUBLIC LICENSE" not in text:
            raise RuntimeError(f"Full GNU license text missing: {url}")
        data = text.encode("utf-8")
        supplemental_data[name] = data
        supplements.append({"file": name, "url": url, "sourceSha256": sha256(raw),
                            "sha256": sha256(data), "bytes": len(data)})

    header = f"""QT {version}: THIRD-PARTY COPYRIGHT AND LICENSE NOTICES

This is an offline documentation superset of the official Qt {version}
third-party attribution index, including all modules/platforms and the Qt
WebEngine Chromium list. Entries can describe tools, optional code, platforms,
or modules that are not part of ChordCue's Windows binary. Inclusion does not
claim that every listed component was compiled or shipped. Individual copyright
and license terms follow their respective components; ChordCue's MIT license
does not replace these terms. Qt's documentation extraction is not a binary
composition audit or a legal compliance certification.

Sources:
{INDEXES[0]}
{INDEXES[1]}

The companion Qt-{version}-notices-manifest.json records every source URL,
the raw HTML SHA-256, and extracted-text SHA-256. Full GNU LGPL 2.0, LGPL 2.1,
GPL 2.0, LGPL 3.0 and GPL 3.0 texts are also distributed as separate files.
Some upstream component pages refer to those texts by name or URL.

Generated by windows/packaging/collect_qt_notices.py; order is URL-sorted.
Only HTML presentation and entity escaping have been removed. Complete text
from each attribution page's main description, including preformatted blocks,
is retained. See windows/docs/THIRD_PARTY.md for source/rebuild instructions.

"""
    chunks = [header]
    for url in sorted(records):
        chunks.extend(["=" * 80 + "\n", records[url]["title"] + "\n", url + "\n\n", pages[url].text(), "\n"])
    aggregate = "".join(chunks).encode("utf-8")
    pyside_manifest, pyside_data = pyside_notices(version, cache)
    name = f"Qt-{version}-THIRD-PARTY-NOTICES.txt"
    manifest = {"schemaVersion": 1, "qtVersion": version,
                "scope": "all-module and all-platform documentation superset; not an SBOM",
                "indexes": indexes, "noticeCount": len(records),
                "notices": [records[url] for url in sorted(records)],
                "supplementalFullTexts": supplements,
                "pysideSourceNotices": pyside_manifest,
                "aggregate": {"file": name, "sha256": sha256(aggregate), "bytes": len(aggregate)}}
    output.mkdir(parents=True, exist_ok=True)
    (output / name).write_bytes(aggregate)
    (output / pyside_manifest["aggregate"]["file"]).write_bytes(pyside_data)
    for filename, data in supplemental_data.items():
        (output / filename).write_bytes(data)
    (output / f"Qt-{version}-notices-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-version", default="6.11.2")
    parser.add_argument("--cache", type=Path, default=ROOT.parent / "qt-license-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "licenses")
    parser.add_argument("--offline", action="store_true", help="Require a complete existing raw cache")
    parser.add_argument("--refresh", action="store_true", help="Replace cached responses from official URLs")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    if args.offline and args.refresh:
        parser.error("--offline and --refresh cannot be combined")
    manifest = collect(args.qt_version, Cache(args.cache, args.offline, args.refresh), args.output, args.workers)
    print(json.dumps({"noticeCount": manifest["noticeCount"], **manifest["aggregate"]}, indent=2))


if __name__ == "__main__":
    main()
