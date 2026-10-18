#!/usr/bin/env python3
"""
Xoilacz Live Stream M3U Scraper (Targeted Teams & Auto-Validation Edition)
- Scrapes live matches from target sports site
- Filters ONLY preferred football teams (Inter Miami, Man Utd, Man City, Como, Barca, Real, Liverpool, Arsenal)
- Captures multiple server streams (BRADY, FELIX, etc.) and validates them
- Automatically cleans old logos and outputs absolute GitHub raw URLs
"""

import asyncio
import io
import os
import re
import shutil
from datetime import datetime, timezone
import urllib.parse

import aiohttp
from PIL import Image, ImageDraw, ImageFont
import pycountry
from playwright.async_api import async_playwright

# Target Website from your input
TARGET_URL = "https://columbiaenvironmentallaw.org/"  # Or your specific sports domain
OUTPUT_FILE = "live_playlist.m3u"
LOGOS_DIR = "logos"
WAIT_PLAYER = 5  # Seconds to wait per server tab

# STRICT STREAM RE: Only capture master/variant playlists, ignore video chunks (.ts)
STREAM_RE = re.compile(
    r"(playlist\.m3u8|index\.m3u8|mono\.m3u8|\.m3u8(\?|$))", re.IGNORECASE
)

# Whitelist of teams you want to scrape
TARGET_TEAMS = [
    "inter miami",
    "manchester united",
    "man. united",
    "manchester city",
    "man. city",
    "como",
    "fc barcelona",
    "barcelona",
    "real madrid",
    "liverpool",
    "arsenal",
]


def normalize_team_name(name: str) -> str:
    return name.strip().lower()


def is_target_match(title: str) -> bool:
    """Checks if the match title contains any of the preferred target teams."""
    lower_title = title.lower()
    for team in TARGET_TEAMS:
        if team in lower_title:
            return True
    return False


def get_fitted_font(
    text: str,
    font_path: str = "arial.ttf",
    max_width: int = 240,
    start_size: int = 28,
):
    size = start_size
    while size >= 14:
        try:
            font = ImageFont.truetype(font_path, size)
            bbox = font.getbbox(text)
            width = bbox[2] - bbox[0]
            if width <= max_width:
                return font
        except Exception:
            return ImageFont.load_default()
        size -= 2
    try:
        return ImageFont.truetype(font_path, 14)
    except Exception:
        return ImageFont.load_default()


async def generate_vs_banner(
    session: aiohttp.ClientSession,
    team1: str,
    team2: str,
    match_slug: str,
) -> str:
    os.makedirs(LOGOS_DIR, exist_ok=True)
    output_path = os.path.join(LOGOS_DIR, f"{match_slug}.png")

    t1_name = (team1 or "TEAM 1").upper()
    t2_name = (team2 or "TEAM 2").upper()

    try:
        canvas = Image.new("RGBA", (700, 300), (18, 20, 29, 255))
        draw = ImageDraw.Draw(canvas)

        try:
            font_vs = ImageFont.truetype("arial.ttf", 48)
        except Exception:
            font_vs = ImageFont.load_default()

        font_t1 = get_fitted_font(t1_name)
        font_t2 = get_fitted_font(t2_name)

        draw.text(
            (180, 150),
            t1_name,
            fill=(255, 65, 65, 255),
            anchor="mm",
            font=font_t1,
        )
        draw.text(
            (350, 150), "VS", fill=(255, 215, 0, 255), anchor="mm", font=font_vs
        )
        draw.text(
            (520, 150),
            t2_name,
            fill=(255, 65, 65, 255),
            anchor="mm",
            font=font_t2,
        )

        canvas.save(output_path, "PNG")
        return output_path
    except Exception as e:
        print(f"    [!] Banner creation error for {match_slug}: {e}")
        return ""


async def validate_stream_link(
    session: aiohttp.ClientSession, stream_info: dict
) -> bool:
    """Validates if the captured m3u8 stream is truly active and working."""
    url = stream_info["url"]
    headers = {
        "User-Agent": stream_info["headers"].get("user-agent", "Mozilla/5.0"),
        "Referer": stream_info["headers"].get("referer", stream_info["referer"]),
    }
    origin = stream_info["headers"].get("origin")
    if origin:
        headers["Origin"] = origin

    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=6)
        ) as resp:
            if resp.status == 200:
                text = await resp.text()
                if "#EXTM3U" in text and (
                    "#EXT-X-STREAM-INF" in text or "m3u8" in text or ".ts" in text
                ):
                    return True
    except Exception:
        pass
    return False


class TargetedStreamScraper:

    def __init__(self, target_url=TARGET_URL):
        self.target_url = target_url
        self.captured_streams = []

    def _clean_logos_folder(self):
        if os.path.exists(LOGOS_DIR):
            print(f"[*] Cleaning up old logos from '{LOGOS_DIR}' folder...")
            shutil.rmtree(LOGOS_DIR)
        os.makedirs(LOGOS_DIR, exist_ok=True)

    async def run(self):
        self._clean_logos_folder()

        print(f"[*] Initializing Chromium Browser for target: {self.target_url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            print("\n[Tier 1] Scanning Live Matches & Filtering Target Teams...")
            match_links = await self._tier_1_find_target_matches(page)
            print(f"    -> Found {len(match_links)} matching target live match(es).")

            async with aiohttp.ClientSession() as http_session:
                raw_streams = []
                for title, url, slug in match_links:
                    print(f"\n[Tier 2] Processing Match: {title}")

                    parts = title.split(" VS ")
                    t1 = parts[0] if len(parts) > 0 else "Team 1"
                    t2 = parts[1] if len(parts) > 1 else "Team 2"

                    logo_path = await generate_vs_banner(http_session, t1, t2, slug)

                    try:
                        match_streams = await self._process_match_page(
                            browser, url, title, logo_path
                        )
                        raw_streams.extend(match_streams)
                    except Exception as e:
                        print(f"    [!] Error processing match {title}: {str(e)}")

                await browser.close()

                print(
                    f"\n[*] Validating {len(raw_streams)} captured stream link(s)..."
                )
                for s in raw_streams:
                    is_working = await validate_stream_link(http_session, s)
                    if is_working:
                        print(f"    [✔ WORKING] {s['url'][:70]}")
                        self.captured_streams.append(s)
                    else:
                        print(f"    [✘ DEAD/INVALID] {s['url'][:70]}")

            print(
                f"\n[*] Generating M3U Playlist ({len(self.captured_streams)} WORKING streams saved)..."
            )
            self._generate_m3u(self.captured_streams)

    async def _tier_1_find_target_matches(self, page):
        """Scrapes match cards and filters only those matching our target team list."""
        try:
            await page.goto(self.target_url, wait_until="commit", timeout=30000)
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[!] Target page load failed: {e}")
            return []

        matches = await page.evaluate(
            r"""
            (targetTeams) => {
                const results = [];
                const seen = new Set();
                const matchElements = Array.from(document.querySelectorAll('a[href*="match"], a[href*="watch"], div[class*="match"], a'));

                matchElements.forEach(el => {
                    const href = el.href || (el.querySelector('a') ? el.querySelector('a').href : '');
                    if (!href || seen.has(href)) return;

                    const card = el.closest('div, tr, li') || el.parentElement;
                    const cardText = card ? card.innerText.trim().replace(/\n/g, ' ') : '';
                    
                    if (cardText.length < 3) return;

                    // Check if any target team is in this match card text
                    const lowerText = cardText.toLowerCase();
                    const isMatched = targetTeams.some(team => lowerText.includes(team));

                    if (isMatched && (cardText.includes('VS') || cardText.includes('-'))) {
                        let title = cardText.includes('VS') ? cardText.split(/VS/i).join('VS') : cardText;
                        if (title.length > 60) title = "Football Match";

                        let slug = href.split('/').filter(Boolean).pop() || 'match';
                        slug = slug.replace(/[^a-zA-Z0-9-_]/g, '_');

                        seen.add(href);
                        results.push([title, href, slug]);
                    }
                });

                return results;
            }
        """,
            TARGET_TEAMS,
        )
        return matches

    async def _process_match_page(
        self, browser, match_url, match_title, match_logo
    ):
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        found_streams = []
        captured_urls = set()

        async def route_handler(route):
            request = route.request
            url = request.url

            if STREAM_RE.search(url) and url not in captured_urls:
                captured_urls.add(url)
                headers = await request.all_headers()
                found_streams.append({
                    "title": match_title,
                    "url": url,
                    "logo": match_logo,
                    "headers": headers,
                    "referer": match_url,
                })
                print(f"        -> [CAPTURED STREAM] {url[:70]}")
            await route.continue_()

        await page.route("**/*", route_handler)

        try:
            await page.goto(match_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Click server tabs like BRADY, FELIX, FULL HD, etc.
            stream_tabs = await page.evaluate("""
                () => {
                    const IGNORED = ['HOME', 'DOWNLOAD', 'APK', 'BACK', 'CLOSE', 'SEARCH', 'TELEGRAM', 'FACEBOOK'];
                    const buttons = Array.from(document.querySelectorAll('button, div[class*="server"], div[class*="tab"], a[class*="btn"], span[class*="btn"]'));
                    const tabs = [];
                    
                    buttons.forEach((el, idx) => {
                        const txt = el.innerText.trim();
                        const upper = txt.toUpperCase();
                        if (!txt || txt.length > 25 || IGNORED.some(w => upper.includes(w))) return;
                        tabs.push({ index: idx, name: txt });
                    });
                    return tabs;
                }
            """)

            if stream_tabs:
                print(f"    -> Found {len(stream_tabs)} server tab(s).")
                for tab in stream_tabs:
                    print(f"    -> Switching to Server: {tab['name']}")
                    try:
                        await page.evaluate(
                            """
                            (idx) => {
                                const buttons = Array.from(document.querySelectorAll('button, div[class*="server"], div[class*="tab"], a[class*="btn"], span[class*="btn"]'));
                                if (buttons[idx]) buttons[idx].click();
                            }
                            """,
                            tab["index"],
                        )
                    except Exception:
                        pass
                    await asyncio.sleep(WAIT_PLAYER)
            else:
                await asyncio.sleep(WAIT_PLAYER)

        except Exception as e:
            print(f"    [!] Error during match stream extraction: {e}")
        finally:
            await context.close()

        for idx, s in enumerate(found_streams):
            s["logo"] = match_logo
            if len(found_streams) > 1:
                s["title"] = f"{s['title']} (Server {idx + 1})"

        return found_streams

    def _generate_m3u(self, streams):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        github_repo = os.getenv("GITHUB_REPOSITORY", "")
        github_branch = os.getenv("GITHUB_REF_NAME", "main")

        if github_repo:
            base_logo_url = (
                f"https://raw.githubusercontent.com/{github_repo}/{github_branch}/"
            )
        else:
            base_logo_url = ""

        lines = [
            "#EXTM3U",
            f"# generated: {ts}",
            f"# source: {self.target_url}",
            f"# streams: {len(streams)}",
            "",
        ]

        for s in streams:
            title = s["title"].replace('"', "'")
            url = s["url"]
            logo = s.get("logo", "")

            if logo:
                if logo.startswith("http://") or logo.startswith("https://"):
                    full_logo_url = logo
                elif base_logo_url:
                    full_logo_url = f"{base_logo_url}{logo}".replace("\\", "/")
                else:
                    full_logo_url = logo
            else:
                full_logo_url = ""

            ref = s["headers"].get("referer", s["referer"])
            ua = s["headers"].get("user-agent", "Mozilla/5.0")
            origin = s["headers"].get("origin", "")

            lines.append(
                f'#EXTINF:-1 tvg-logo="{full_logo_url}" group-title="Targeted Football Matches",{title}'
            )
            lines.append(f"#EXTVLCOPT:http-referrer={ref}")
            lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
            if origin:
                lines.append(f"#EXTVLCOPT:http-origin={origin}")

            kodi_props = [f"Referer={ref}", f"User-Agent={ua}"]
            if origin:
                kodi_props.append(f"Origin={origin}")
            lines.append(
                "#KODIPROP:inputstream.adaptive.stream_headers=" + "&".join(kodi_props)
            )

            lines.append(url)
            lines.append("")

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\n[+] Success! Playlist saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    scraper = TargetedStreamScraper()
    asyncio.run(scraper.run())
            
