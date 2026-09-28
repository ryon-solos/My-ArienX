
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import platform
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
)
_OS = platform.system()   # "Windows" | "Darwin" | "Linux"

def _normalize_url(url: str) -> str:
    """
    Bare words like "instagram" → "https://instagram.com"
    Domains like "instagram.com" → "https://instagram.com"
    Full URLs pass through unchanged.
    """
    url = url.strip()
    if not url:
        return "about:blank"
    if "://" in url:
        return url
    # No dot at all → assume .com  (e.g. "instagram" → "instagram.com")
    if "." not in url:
        url = url + ".com"
    return "https://" + url


def _host_of(url: str) -> str:
    """Lowercased hostname, '' when unparseable. Used to confirm a navigation
    actually arrived where it was sent (redirects aside)."""
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


async def _snap(page) -> tuple[str, str]:
    """Lightweight page state: (url, title). Pure DOM read, milliseconds."""
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        return (page.url or "", title or "")
    except Exception:
        return ("", "")


# ── Stale-session detection (browser lifecycle fix) ──────────────────────────
# Playwright reports a dead automation session with messages like:
#   "BrowserContext.new_page: Target page, context or browser has been closed"
# These mean the stored BrowserContext/Page can never work again and must be
# discarded, not reused. Anything else (timeout, selector miss, navigation
# failure) is an ordinary action failure and must NOT trigger a relaunch.
_STALE_MARKERS = (
    "target page, context or browser has been closed",
    "browsercontext has been closed",
    "browser has been closed",
    "context has been closed",
    "page has been closed",
    "connection closed",
    "browser disconnected",
    "disconnected",
    "target crashed",
)


def _is_stale_error(e: BaseException | str) -> bool:
    """True if this error means the Playwright session itself is dead."""
    try:
        msg = str(e).lower()
    except Exception:
        return False
    return any(m in msg for m in _STALE_MARKERS)


def _rethrow_if_stale(e: BaseException) -> None:
    """Re-raise session-death errors; swallow nothing stale.

    Session methods convert ordinary failures (selector miss, timeout, nav
    failure) into honest result strings — but a dead BrowserContext must
    propagate as an exception so the dispatch layer (_run_auto) can
    invalidate, relaunch once, and retry. Call this first in every broad
    `except` inside _BrowserSession.
    """
    if _is_stale_error(e):
        raise e


def _user_agent() -> str:
    if _OS == "Windows":
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    if _OS == "Darwin":
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    return (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def _real_profile_dir(browser: str) -> str:
    home  = Path.home()
    local = os.environ.get("LOCALAPPDATA", "")
    roam  = os.environ.get("APPDATA", "")

    candidates: list[Path] = []

    if _OS == "Windows":
        m = {
            "chrome":   [Path(local) / "Google"          / "Chrome"          / "User Data"],
            "edge":     [Path(local) / "Microsoft"        / "Edge"            / "User Data"],
            "brave":    [Path(local) / "BraveSoftware"    / "Brave-Browser"   / "User Data"],
            "vivaldi":  [Path(local) / "Vivaldi"          / "User Data"],
            "opera":    [Path(roam)  / "Opera Software"   / "Opera Stable",
                         Path(local) / "Opera Software"   / "Opera Stable"],
            "operagx":  [Path(roam)  / "Opera Software"   / "Opera GX Stable",
                         Path(local) / "Opera Software"   / "Opera GX Stable"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Darwin":
        lib = home / "Library" / "Application Support"
        m = {
            "chrome":   [lib / "Google"             / "Chrome"],
            "edge":     [lib / "Microsoft Edge"],
            "brave":    [lib / "BraveSoftware"       / "Brave-Browser"],
            "vivaldi":  [lib / "Vivaldi"],
            "opera":    [lib / "com.operasoftware.Opera"],
            "operagx":  [lib / "com.operasoftware.OperaGX"],
        }
        candidates = m.get(browser, [])

    elif _OS == "Linux":
        cfg = home / ".config"
        m = {
            "chrome":   [cfg / "google-chrome", cfg / "chromium"],
            "edge":     [cfg / "microsoft-edge"],
            "brave":    [cfg / "BraveSoftware" / "Brave-Browser"],
            "vivaldi":  [cfg / "vivaldi"],
            "opera":    [cfg / "opera"],
            "operagx":  [cfg / "opera-gx"],
        }
        candidates = m.get(browser, [])

    for p in candidates:
        if p.exists():
            print(f"[Browser] ✅ Real profile found for {browser}: {p}")
            return str(p)

    fallback = home / ".jarvis_profiles" / browser
    fallback.mkdir(parents=True, exist_ok=True)
    print(f"[Browser] ⚠️  Real profile not found for {browser}, using: {fallback}")
    return str(fallback)

def _firefox_profile_dir() -> Optional[str]:
    home = Path.home()

    if _OS == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    elif _OS == "Darwin":
        base = home / "Library" / "Application Support" / "Firefox"
    else:
        base = home / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None

    current: dict[str, str] = {}
    default_path: Optional[str] = None

    for line in ini.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("["):
            p = current.get("Path", "")
            if p and current.get("Default") == "1":
                is_rel = current.get("IsRelative", "1") == "1"
                default_path = str(base / p) if is_rel else p
            current = {}
        elif "=" in line:
            k, _, v = line.partition("=")
            current[k.strip()] = v.strip()

    p = current.get("Path", "")
    if p and current.get("Default") == "1":
        is_rel = current.get("IsRelative", "1") == "1"
        default_path = str(base / p) if is_rel else p

    if default_path and Path(default_path).exists():
        print(f"[Browser] Firefox real profile: {default_path}")
        return default_path
    return None

def _find_opera_windows() -> Optional[str]:
    local  = os.environ.get("LOCALAPPDATA", "")
    prog   = os.environ.get("PROGRAMFILES", "")
    prog86 = os.environ.get("PROGRAMFILES(X86)", "")

    candidates = [
        Path(local)  / "Programs" / "Opera"    / "opera.exe",
        Path(local)  / "Programs" / "Opera GX" / "opera.exe",
        Path(prog)   / "Opera"    / "opera.exe",
        Path(prog86) / "Opera"    / "opera.exe",
    ]
    for p in candidates:
        if p.exists():
            print(f"[Browser] Opera found at: {p}")
            return str(p)

    try:
        import winreg
        keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\opera.exe",
            r"SOFTWARE\Clients\StartMenuInternet\OperaStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\OperaGXStable\shell\open\command",
            r"SOFTWARE\Clients\StartMenuInternet\opera\shell\open\command",
        ]
        for key_path in keys:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        print(f"[Browser] Opera found via registry: {exe}")
                        return exe
                except Exception:
                    continue
    except Exception:
        pass

    return shutil.which("opera") or None

def _find_exe_windows(prog_name: str) -> Optional[str]:
    try:
        import winreg
        paths_to_try = [
            rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{prog_name}.exe",
            rf"SOFTWARE\Clients\StartMenuInternet\{prog_name}\shell\open\command",
        ]
        for key_path in paths_to_try:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    k   = winreg.OpenKey(hive, key_path)
                    val = winreg.QueryValue(k, None)
                    winreg.CloseKey(k)
                    exe = val.strip().strip('"').split('"')[0].split(" --")[0].strip()
                    if exe and Path(exe).exists():
                        return exe
                except Exception:
                    continue
    except Exception:
        pass
    return None

_BROWSER_SPECS: dict[str, dict] = {
    "Windows": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": []},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox.exe"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera.exe"],  "special": "opera_windows"},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": [],             "special": "opera_windows"},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave.exe"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi.exe"]},
        "safari":   None,
    },
    "Darwin": {
        "chrome":   {"engine": "chromium", "channel": "chrome",  "bins": []},
        "edge":     {"engine": "chromium", "channel": "msedge",  "bins": ["microsoft-edge"]},
        "firefox":  {"engine": "firefox",  "channel": None,      "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "operagx":  {"engine": "chromium", "channel": None,      "bins": ["opera"]},
        "brave":    {"engine": "chromium", "channel": None,      "bins": ["brave browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None,      "bins": ["vivaldi"]},
        "safari":   {"engine": "webkit",   "channel": None,      "bins": []},
    },
    "Linux": {
        "chrome":   {"engine": "chromium", "channel": None,
                     "bins": ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]},
        "edge":     {"engine": "chromium", "channel": None,
                     "bins": ["microsoft-edge", "microsoft-edge-stable"]},
        "firefox":  {"engine": "firefox",  "channel": None, "bins": ["firefox"]},
        "opera":    {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "operagx":  {"engine": "chromium", "channel": None, "bins": ["opera", "opera-stable"]},
        "brave":    {"engine": "chromium", "channel": None, "bins": ["brave-browser", "brave"]},
        "vivaldi":  {"engine": "chromium", "channel": None, "bins": ["vivaldi-stable", "vivaldi"]},
        "safari":   None,
    },
}

_ALIASES: dict[str, str] = {
    "google chrome":   "chrome",
    "google-chrome":   "chrome",
    "microsoft edge":  "edge",
    "ms edge":         "edge",
    "msedge":          "edge",
    "mozilla firefox": "firefox",
    "opera gx":        "operagx",
    "opera_gx":        "operagx",
}


def _resolve_browser(name: str) -> dict | None:
    name   = _ALIASES.get(name.lower().strip(), name.lower().strip())
    os_map = _BROWSER_SPECS.get(_OS, {})
    spec   = os_map.get(name)
    if spec is None:
        return None

    engine  = spec["engine"]
    channel = spec.get("channel")
    bins    = spec.get("bins", [])
    exe     = None

    if spec.get("special") == "opera_windows":
        exe = _find_opera_windows()
        if not exe:
            print(f"[Browser] ⚠️  Opera executable not found on Windows.")
        return {"engine": engine, "exe": exe, "channel": channel}

    for b in bins:
        found = shutil.which(b)
        if found:
            exe = found
            break

    if not exe and _OS == "Darwin":
        app_names = {
            "chrome":  ["Google Chrome.app"],
            "edge":    ["Microsoft Edge.app"],
            "firefox": ["Firefox.app"],
            "opera":   ["Opera.app", "Opera GX.app"],
            "brave":   ["Brave Browser.app"],
            "vivaldi": ["Vivaldi.app"],
        }
        for app in app_names.get(name, []):
            app_dir = Path("/Applications") / app / "Contents" / "MacOS"
            if app_dir.exists():
                found_bins = list(app_dir.iterdir())
                if found_bins:
                    exe = str(found_bins[0])
                    break

    if not exe and _OS == "Windows" and not channel:
        exe = _find_exe_windows(name)

    return {"engine": engine, "exe": exe, "channel": channel}


def _detect_default_browser() -> str:
    try:
        if _OS == "Windows":
            import winreg
            k = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice",
            )
            prog_id = winreg.QueryValueEx(k, "ProgId")[0].lower()
            winreg.CloseKey(k)
            for kw in ("edge", "firefox", "opera", "brave", "vivaldi", "chrome"):
                if kw in prog_id:
                    return kw
        elif _OS == "Darwin":
            out = subprocess.run(
                ["defaults", "read",
                 "com.apple.LaunchServices/com.apple.launchservices.secure",
                 "LSHandlers"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "safari", "chrome", "edge"):
                if kw in out:
                    return kw
        elif _OS == "Linux":
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
            ).stdout.lower()
            for kw in ("firefox", "opera", "brave", "vivaldi", "chrome", "edge"):
                if kw in out:
                    return kw
    except Exception:
        pass
    return "chrome"


_SEARCH_ENGINES: dict[str, str] = {
    "google":     "https://www.google.com/search?q=",
    "bing":       "https://www.bing.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "yandex":     "https://yandex.com/search/?text=",
}

_MAC_APP_NAMES: dict[str, str] = {
    "chrome":  "Google Chrome",
    "edge":    "Microsoft Edge",
    "firefox": "Firefox",
    "opera":   "Opera",
    "operagx": "Opera GX",
    "brave":   "Brave Browser",
    "vivaldi": "Vivaldi",
    "safari":  "Safari",
}

# Windows registry lookup names for browsers whose spec has no explicit binary
_WIN_EXE_HINTS: dict[str, str] = {"chrome": "chrome", "edge": "msedge"}


def _open_native(url: str, browser_name: Optional[str]) -> str:
    """
    Opens the user's REAL browser normally — with their own profile,
    logged-in accounts and extensions. No automation attaches, so an
    about:blank tab or a blank profile NEVER shows up.
    If url is empty the browser starts with no URL (its own start page /
    session restore) — exactly as if the user had opened it themselves.
    Works on all three of Windows / macOS / Linux.
    """
    url = _normalize_url(url) if url and url.strip() else ""
    if url == "about:blank":
        url = ""

    name = None
    if browser_name:
        name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
    elif not url:
        # No URL → only a window will open; needs the default browser's exe
        name = _detect_default_browser()

    # Specific browser → launch its own executable, exactly like the user would.
    if name:
        if _OS == "Darwin":
            app = _MAC_APP_NAMES.get(name)
            if app:
                cmd = ["open", "-a", app] + ([url] if url else [])
                try:
                    subprocess.run(cmd, check=True, timeout=10)
                    return f"Opened in {name}: {url}" if url else f"Opened {name}."
                except Exception as e:
                    print(f"[Browser] 'open -a {app}' failed ({e}), trying binary…")

        spec = _resolve_browser(name)
        exe  = spec.get("exe") if spec else None
        if not exe and _OS == "Windows":
            if name in ("opera", "operagx"):
                exe = _find_opera_windows()
            else:
                exe = _find_exe_windows(_WIN_EXE_HINTS.get(name, name))
        if exe:
            try:
                subprocess.Popen(
                    [exe, url] if url else [exe],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return f"Opened in {name}: {url}" if url else f"Opened {name}."
            except Exception as e:
                print(f"[Browser] Native launch failed for {name}: {e}")
        print(f"[Browser] '{name}' not found — falling back to default browser.")

    if not url:
        return "Could not find a browser to open."

    # Default browser via the OS — exactly like the user clicking a link.
    try:
        if _OS == "Windows":
            os.startfile(url)                       # ShellExecute → default browser
        elif _OS == "Darwin":
            subprocess.run(["open", url], check=True, timeout=10)
        else:
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        return f"Opened in your default browser: {url}"
    except Exception:
        try:
            if webbrowser.open(url):
                return f"Opened in your default browser: {url}"
        except Exception:
            pass
        return f"Could not open a browser for: {url}"


class _BrowserSession:
    """
    A full session for one browser instance.
    All browsers open on the real profile via launch_persistent_context.
    """

    def __init__(self, browser_name: str):
        self.browser_name = browser_name
        self._spec        = _resolve_browser(browser_name)

        self._loop:    asyncio.AbstractEventLoop | None = None
        self._thread:  threading.Thread | None          = None
        self._ready    = threading.Event()

        self._pw:      Playwright     | None = None
        self._context: BrowserContext | None = None
        self._page:    Page           | None = None
        # Profile dir of the last SUCCESSFUL launch. Recovery retries it
        # first: re-probing a locked real profile costs a 25 s timeout, and
        # in production the user's own Chrome usually holds that lock.
        self._profile_used: str | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"BrowserThread-{self.browser_name}",
        )
        self._thread.start()
        self._ready.wait(timeout=20)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._async_init())
        self._ready.set()
        self._loop.run_forever()

    async def _async_init(self):
        self._pw = await async_playwright().start()

    def run(self, coro, timeout: int = 60) -> str:
        if not self._loop:
            raise RuntimeError(f"Session for '{self.browser_name}' not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def close(self):
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_close(), self._loop).result(10)

    async def _async_close(self):
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self._context = self._page = None

    async def _adopt_page(self) -> Page:
        """
        launch_persistent_context already opens a starting tab.
        Instead of opening a new blank tab (about:blank), it adopts that tab —
        so the user never sees an extra blank tab.
        """
        await asyncio.sleep(0.3)
        pages = self._context.pages
        return pages[0] if pages else await self._context.new_page()

    async def _launch(self, first_profile: str | None = None):
        """
        Launches the browser with the real user profile.
        Does nothing if the context is already open.
        `first_profile`: recovery hint — try this profile dir before the
        real one (avoids a 25 s timeout against a locked real profile).
        """
        if self._context is not None:
            return

        if self._spec is None:
            raise RuntimeError(
                f"'{self.browser_name}' bu platformda ({_OS}) desteklenmiyor."
            )

        engine_name = self._spec["engine"]
        exe         = self._spec["exe"]
        channel     = self._spec["channel"]
        engine_obj  = getattr(self._pw, engine_name)

        if engine_name == "firefox":
            profile = _firefox_profile_dir() or str(
                Path.home() / ".jarvis_profiles" / "firefox"
            )
            kwargs: dict = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            if exe:
                kwargs["executable_path"] = exe
            try:
                self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            except Exception as e:
                print(f"[Browser] Firefox real profile failed ({e}), using JARVIS profile")
                jarvis = str(Path.home() / ".jarvis_profiles" / "firefox_jarvis")
                Path(jarvis).mkdir(parents=True, exist_ok=True)
                self._context = await engine_obj.launch_persistent_context(jarvis, **kwargs)

            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Firefox launched")
            return

        if engine_name == "webkit":
            safari_profile = str(Path.home() / ".jarvis_profiles" / "safari")
            Path(safari_profile).mkdir(parents=True, exist_ok=True)
            kwargs = {
                "headless":    False,
                "slow_mo":     0,
                "viewport":    None,
                "no_viewport": True,
                "timeout":     25_000,
            }
            self._context = await engine_obj.launch_persistent_context(safari_profile, **kwargs)
            self._page = await self._adopt_page()
            print(f"[Browser] ✅ Safari launched")
            return

        profile = _real_profile_dir(self.browser_name)

        kwargs = {
            "headless":    False,
            "slow_mo":     0,
            "viewport":    None,
            "no_viewport": True,
            "timeout":     25_000,
            "args": [
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-default-apps",
                "--no-default-browser-check",
            ],
        }

        if exe:
            kwargs["executable_path"] = exe
        elif channel:
            kwargs["channel"] = channel

        label = (
            f"{self.browser_name}"
            + (f"/{channel}" if channel else "")
            + (f" @ {exe}" if exe else "")
        )

        # Recovery fast-path: the last profile that worked is tried before
        # the real one (which is usually locked by the user's own browser and
        # costs a 25 s timeout to fail). Ordinary launches pass None and keep
        # the real-profile-first order.
        if first_profile and first_profile != profile:
            try:
                self._context = await engine_obj.launch_persistent_context(
                    first_profile, **kwargs)
                self._page = await self._adopt_page()
                self._profile_used = first_profile
                print(f"[Browser] ✅ Launched [{label}] profile={first_profile} (recovery)")
                return
            except Exception as e:
                print(f"[Browser] ⚠️  Recovery profile failed for {label}: {e}")

        try:
            self._context = await engine_obj.launch_persistent_context(profile, **kwargs)
            self._page = await self._adopt_page()
            self._profile_used = profile
            print(f"[Browser] ✅ Launched [{label}] profile={profile}")
            return
        except Exception as e:
            print(f"[Browser] ⚠️  Real profile failed for {label}: {e}")

        # The real profile could not be opened (browser already open / locked
        # profile / newer Chrome versions block the real profile under
        # automation). Fall back to a persistent JARVIS automation profile —
        # accounts logged in here once stay logged in on later sessions too.
        jarvis_profile = str(Path.home() / ".jarvis_profiles" / self.browser_name)
        Path(jarvis_profile).mkdir(parents=True, exist_ok=True)
        print(f"[Browser] Retrying with JARVIS profile: {jarvis_profile}")

        try:
            self._context = await engine_obj.launch_persistent_context(jarvis_profile, **kwargs)
            self._page = await self._adopt_page()
            self._profile_used = jarvis_profile
            print(f"[Browser] ✅ Launched [{label}] with JARVIS profile "
                  f"(sign-ins persist across sessions)")
        except Exception as e2:
            raise RuntimeError(f"Could not launch {self.browser_name}: {e2}") from e2


    async def _full_recover(self) -> str:
        """Discard dead refs, restart Playwright if needed, relaunch, page.

        Exactly-once recovery path: STALE → invalidate → relaunch → page.
        Raises RuntimeError with an honest message when the session cannot
        be restored — the caller must report it, never claim success.
        Runs on the session's own event loop (Playwright objects are
        loop-bound), invoked via run() from any other thread.
        """
        print("[BrowserLifecycle] restarting Playwright session")
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                pass
        self._context = None
        self._page = None

        if self._pw is None:
            try:
                self._pw = await async_playwright().start()
            except Exception as e:
                print(f"[BrowserLifecycle] recovery failed: {e}")
                raise RuntimeError(
                    "browser automation session could not be restored "
                    f"(Playwright restart failed: {e}). "
                    "No action was performed.") from e

        try:
            await self._launch(first_profile=self._profile_used)
        except Exception as e:
            if _is_stale_error(e) or "connection" in str(e).lower():
                # The Playwright driver connection itself died mid-launch —
                # restart it once, then relaunch on the fresh driver.
                try:
                    try:
                        await self._pw.stop()
                    except Exception:
                        pass
                    self._pw = await async_playwright().start()
                    self._context = None
                    self._page = None
                    await self._launch(first_profile=self._profile_used)
                except Exception as e2:
                    print(f"[BrowserLifecycle] recovery failed: {e2}")
                    raise RuntimeError(
                        "browser automation session could not be restored "
                        f"({e2}). No action was performed.") from e2
            else:
                print(f"[BrowserLifecycle] recovery failed: {e}")
                raise RuntimeError(
                    "browser automation session could not be restored "
                    f"({e}). No action was performed.") from e

        try:
            await self._get_page()
        except Exception as e:
            print(f"[BrowserLifecycle] recovery failed: {e}")
            raise RuntimeError(
                "browser automation session could not be restored "
                f"({e}). No action was performed.") from e
        print("[BrowserLifecycle] recovery succeeded")
        return "recovered"

    def restart(self, timeout: int = 90) -> str:
        """Sync wrapper for _full_recover (call from outside the loop thread)."""
        return self.run(self._full_recover(), timeout=timeout)

    async def _get_page(self) -> Page:
        # Probe the stored context BEFORE trusting it: _launch() early-returns
        # on a non-None context, so without this check a context the user
        # closed externally is reused forever and every new_page() raises
        # "Target page, context or browser has been closed".
        if self._context is not None:
            try:
                _ = self._context.pages
            except Exception as e:
                print(f"[BrowserLifecycle] stale context detected: {str(e)[:100]}")
                await self._full_recover()
                return self._page
        await self._launch()
        # If somehow page got closed, open a fresh one
        if self._page is None:
            try:
                self._page = await self._context.new_page()
            except Exception as e:
                if _is_stale_error(e):
                    print(f"[BrowserLifecycle] stale context detected: {str(e)[:100]}")
                    await self._full_recover()
                    return self._page
                raise
            await asyncio.sleep(0.2)
        else:
            try:
                closed = self._page.is_closed()
            except Exception:
                closed = True
            if closed:
                try:
                    self._page = await self._context.new_page()
                except Exception as e:
                    if _is_stale_error(e):
                        print(f"[BrowserLifecycle] stale context detected: {str(e)[:100]}")
                        await self._full_recover()
                        return self._page
                    raise
                await asyncio.sleep(0.2)
        return self._page

    async def go_to(self, url: str) -> str:

        url      = _normalize_url(url)
        page     = await self._get_page()
        prev_url = page.url

        async def _do_goto(p: Page) -> str:
            """Attempt navigation and return the resulting URL (may still be blank)."""
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(0.3)
            except PlaywrightTimeout:
                pass   # page may have partially loaded — check URL below
            except Exception as e:
                _rethrow_if_stale(e)   # dead session → recover+retry, not "non-fatal"
                print(f"[Browser] goto exception (non-fatal): {e}")
            return p.url

        result_url = await _do_goto(page)

        if result_url in ("about:blank", "", None, prev_url) and prev_url in ("about:blank", "", None):
            print(f"[Browser] Still blank after goto — retrying on new tab: {url}")
            try:
                try:
                    new_page = await self._context.new_page()
                except Exception as ne:
                    if _is_stale_error(ne):
                        print(f"[BrowserLifecycle] stale context detected: {str(ne)[:100]}")
                        await self._full_recover()
                        new_page = await self._context.new_page()
                    else:
                        raise
                self._page = new_page
                result_url = await _do_goto(new_page)
            except Exception as e:
                print(f"[Browser] New-tab retry failed: {e}")

        if result_url and result_url not in ("about:blank", "", None):
            if _host_of(result_url) == _host_of(url):
                return f"Opened: {result_url} (verified)"
            return (f"Opened {result_url}, but that is not the requested address "
                    f"({url}) — navigation may be incomplete.")
        return f"Could not open: {url}"

    async def search(self, query: str, engine: str = "google") -> str:
        base = _SEARCH_ENGINES.get(engine.lower(), _SEARCH_ENGINES["google"])
        return await self.go_to(base + query.replace(" ", "+"))

    async def click(self, selector: str = None, text: str = None) -> str:
        page = await self._get_page()
        label = f"text '{text}'" if text else (
            f"selector '{selector}'" if selector else "")
        if not label:
            return "No selector or text provided."

        async def _once() -> tuple[bool, str]:
            """Returns (changed, detail). 'not found' detail means no target."""
            try:
                loc = (page.get_by_text(text, exact=False).first if text
                       else page.locator(selector).first)
                if await loc.count() == 0:
                    return (False, "not found")
                before = await _snap(page)
                await loc.click(timeout=8_000)
                await asyncio.sleep(0.4)
                after = await _snap(page)
                if after != before:
                    return (True, f"page is now at {after[0] or after[1]}")
                try:
                    if await loc.count() == 0:
                        return (True, "target element is gone")
                    if await loc.is_hidden() or await loc.is_disabled():
                        return (True, "target element changed state")
                except Exception:
                    return (True, "target element changed")
                return (False, "no page change")
            except PlaywrightTimeout:
                return (False, "not found")
            except Exception as e:
                _rethrow_if_stale(e)
                return (False, f"error: {e}")

        ok, detail = await _once()
        if ok:
            return f"Clicked {label} — verified ({detail})."
        if detail == "not found":
            return f"Could not find {label} to click."
        # ONE recovery: re-aim and click once more, then stop.
        ok, detail = await _once()
        if ok:
            return f"Clicked {label} on retry — verified ({detail})."
        if detail == "not found":
            return f"Could not find {label} to click."
        return f"Clicked {label}, but no page change could be confirmed."

    async def type_text(self, selector: str = None, text: str = "",
                        clear_first: bool = True) -> str:
        page = await self._get_page()

        async def _once() -> tuple[bool, str]:
            try:
                el = page.locator(selector).first if selector else page.locator(":focus")
                if await el.count() == 0:
                    return (False, "no input found")
                if clear_first:
                    try:
                        await el.clear()
                    except Exception:
                        pass
                await el.type(text, delay=50)
                try:
                    val = await el.input_value()
                except Exception:
                    return (True, "typed (value not readable here)")
                if val == text:
                    return (True, "value confirmed")
                return (False, f"value mismatch ({len(val)} chars present)")
            except PlaywrightTimeout:
                return (False, "no input found")
            except Exception as e:
                _rethrow_if_stale(e)
                return (False, f"error: {e}")

        ok, detail = await _once()
        if ok:
            return f"Text typed — verified ({detail})."
        if detail == "no input found":
            return "Could not find an input to type into."
        # ONE recovery: force-set the value, then stop.
        try:
            el = page.locator(selector).first if selector else page.locator(":focus")
            await el.fill(text)
            val = await el.input_value()
            if val == text:
                return "Text typed on retry — verified (value confirmed)."
        except Exception as e:
            _rethrow_if_stale(e)
        return f"Typed, but the value could not be confirmed ({detail})."

    async def scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()

        async def _pos():
            try:
                return await page.evaluate("() => [window.scrollX, window.scrollY]")
            except Exception as e:
                _rethrow_if_stale(e)
                return None

        async def _once() -> bool:
            before = await _pos()
            try:
                y = amount if direction == "down" else -amount
                await page.mouse.wheel(0, y)
                await asyncio.sleep(0.3)
            except Exception as e:
                _rethrow_if_stale(e)
                return False
            after = await _pos()
            return (before is not None and after is not None and after != before)

        if await _once():
            return f"Scrolled {direction} — verified."
        # ONE recovery, then stop.
        if await _once():
            return f"Scrolled {direction} on retry — verified."
        return (f"Scrolled {direction}, but the scroll position did not change "
                f"(may already be at the end).")

    async def press(self, key: str) -> str:
        page = await self._get_page()
        try:
            before = await _snap(page)
            await page.keyboard.press(key)
            await asyncio.sleep(0.5)
            after = await _snap(page)
            if after != before:
                return f"Pressed: {key} — verified (now at {after[0] or after[1]})."
            # ONE recovery: press once more, then stop.
            await page.keyboard.press(key)
            await asyncio.sleep(0.5)
            after = await _snap(page)
            if after != before:
                return f"Pressed: {key} on retry — verified (now at {after[0] or after[1]})."
            return f"Pressed: {key} (no page change detected)."
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Key error: {e}"

    async def get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4_000]
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Could not get page text: {e}"

    async def get_url(self) -> str:
        page = await self._get_page()
        return page.url

    async def get_links(self, max_links: int = 30) -> str:
        """Result-grounded chaining step: compact verified link list.

        Returns numbered "text → url" lines for the page's links (first
        N with visible text), WITHOUT clicking anything — so a follow-up
        step (open/click result N, report findings) works from real page
        state instead of memory. Read-only; never navigates.
        """
        page = await self._get_page()
        try:
            items = await page.evaluate(
                """() => Array.from(document.querySelectorAll('a[href]'))
                    .slice(0, 80)
                    .map(a => ({text: (a.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
                                href: a.href}))
                    .filter(l => l.text && l.href)"""
            )
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Could not read page links: {e}"
        seen, out = set(), []
        for it in items or []:
            try:
                href = str(it.get("href", ""))
                text = str(it.get("text", ""))
            except Exception:
                continue
            if href in seen or not href.startswith(("http", "file")):
                continue
            seen.add(href)
            out.append(f"{len(out)+1}. {text} → {href}")
            if len(out) >= max(1, min(int(max_links or 30), 50)):
                break
        if not out:
            return "No links with visible text found on this page."
        return f"Links on {page.url} (verified list):\n" + "\n".join(out)

    async def fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        failed  = []
        for selector, value in fields.items():
            ok = False
            try:
                el = page.locator(selector).first
                if await el.count() > 0:
                    await el.clear()
                    await el.type(str(value), delay=40)
                    try:
                        ok = (await el.input_value()) == str(value)
                    except Exception:
                        ok = True   # typed, value not readable here
            except Exception as e:
                _rethrow_if_stale(e)
                results.append(f"✗ {selector}: {e}")
                failed.append(selector)
                continue
            if ok:
                results.append(f"✓ {selector}")
            else:
                failed.append(selector)
        # ONE recovery, only for fields whose value did not read back.
        for selector in list(failed):
            try:
                el = page.locator(selector).first
                await el.fill(str(fields[selector]))
                if (await el.input_value()) == str(fields[selector]):
                    failed.remove(selector)
                    results.append(f"✓ {selector} (retry)")
            except Exception as e:
                _rethrow_if_stale(e)
                results.append(f"✗ {selector}: {e}")
        if not failed:
            return "Form filled and verified: " + ", ".join(results)
        return ("Form partially filled — verified: "
                + ", ".join(results)
                + f". Unverified: {', '.join(failed)}.")

    async def smart_click(self, description: str) -> str:
        page = await self._get_page()

        async def _attempt() -> tuple[bool, str]:
            """One full pass over the locator strategies. No verification."""
            for role in ("button", "link", "searchbox", "textbox", "menuitem", "tab"):
                try:
                    loc = page.get_by_role(role, name=description)
                    if await loc.count() > 0:
                        await loc.first.click(timeout=5_000)
                        return (True, f"role={role}")
                except Exception as e:
                    _rethrow_if_stale(e)
            for name, attempt in (
                ("text", lambda: page.get_by_text(description, exact=False).first.click(timeout=5_000)),
                ("placeholder", lambda: page.get_by_placeholder(description, exact=False).first.click(timeout=5_000)),
                ("label", lambda: page.locator(
                    f'[alt*="{description}" i],[title*="{description}" i],'
                    f'[aria-label*="{description}" i]'
                ).first.click(timeout=5_000)),
            ):
                try:
                    await attempt()
                    return (True, name)
                except Exception as e:
                    _rethrow_if_stale(e)
            return (False, "not found")

        before = await _snap(page)
        found, _how = await _attempt()
        if not found:
            return await self._native_click_fallback(page, description)

        async def _changed(before) -> tuple[bool, str]:
            await asyncio.sleep(0.4)
            after = await _snap(page)
            if after != before:
                return (True, f"page is now at {after[0] or after[1]}")
            return (False, "no page change")

        ok, detail = await _changed(before)
        if ok:
            return f"Clicked: '{description}' — verified ({detail})."
        # ONE recovery: re-aim (strategies re-query the live DOM) and click once more.
        found, _how = await _attempt()
        if not found:
            return f"Clicked: '{description}', but it is no longer found — likely acted on."
        ok, detail = await _changed(before)
        if ok:
            return f"Clicked: '{description}' on retry — verified ({detail})."
        return f"Clicked: '{description}', but no page change could be confirmed."

    async def _native_click_fallback(self, page, description: str) -> str:
        """Screenshot-based click for targets outside the DOM (Chrome profile
        tiles, OS dialogs, browser chrome) where Playwright is blind by design.
        ONE find + click, verified by page state, ONE re-aimed recovery, then
        an honest result. Never the default path — only called after the DOM
        strategies found nothing. No passwords or credentials are ever read,
        logged, or extracted: only visible-element coordinates are used."""
        async def _changed(before) -> tuple[bool, str]:
            # Native UI can react slowly: poll briefly instead of one shot.
            for _ in range(3):
                await asyncio.sleep(0.4)
                after = await _snap(page)
                if after != before:
                    return (True, f"page is now at {after[0] or after[1]}")
            return (False, "no page change")

        try:
            from actions.computer_control import _screen_find as _native_find
            from actions.computer_control import _click as _native_click
            loop = asyncio.get_running_loop()
            coords = await loop.run_in_executor(None, _native_find, description)
        except Exception as e:
            print(f"[Browser] native fallback unavailable: {e}")
            coords = None
        if coords is None:
            return f"Could not find element: '{description}'"
        try:
            try:
                await page.bring_to_front()
            except Exception:
                pass
            before = await _snap(page)
            await loop.run_in_executor(
                None, lambda: _native_click(x=coords[0], y=coords[1]))
            ok, detail = await _changed(before)
            if ok:
                return (f"Clicked native element: '{description}' — verified "
                        f"({detail}).")
            # ONE recovery: fresh re-find (new screenshot inside), click once.
            coords2 = await loop.run_in_executor(None, _native_find, description)
            if coords2 is not None and (
                    abs(coords2[0] - coords[0]) + abs(coords2[1] - coords[1]) > 30):
                await loop.run_in_executor(
                    None, lambda: _native_click(x=coords2[0], y=coords2[1]))
                ok, detail = await _changed(before)
                if ok:
                    return (f"Clicked native element: '{description}' on retry — "
                            f"verified ({detail}).")
            return (f"Clicked native element: '{description}', but no page "
                    f"change could be confirmed.")
        except Exception as e:
            return f"Native click failed for '{description}': {e}"

    async def smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()

        async def _resolve():
            """First matching input, or None. Re-queries the live DOM."""
            candidates = [
                ("placeholder", page.get_by_placeholder(description, exact=False)),
                ("label",       page.get_by_label(description, exact=False)),
                ("role",        page.get_by_role("textbox", name=description)),
                ("searchbox",   page.get_by_role("searchbox")),
                ("combobox",    page.get_by_role("combobox", name=description)),
            ]
            for method, loc in candidates:
                try:
                    el = loc.first
                    if await el.count() == 0:
                        continue
                    return (method, el)
                except Exception as e:
                    _rethrow_if_stale(e)
                    continue
            return (None, None)

        async def _readback(el) -> bool:
            try:
                return (await el.input_value()) == text
            except Exception:
                return False

        method, el = await _resolve()
        if el is None:
            return f"Could not find input: '{description}'"
        try:
            await el.clear()
            await el.type(text, delay=50)
        except Exception as e:
            _rethrow_if_stale(e)
        if await _readback(el):
            return f"Typed into ({method}): '{description}' — verified."
        # ONE recovery: force-set the value on a fresh handle, then stop.
        try:
            _m2, el2 = await _resolve()
            if el2 is not None:
                await el2.fill(text)
                if await _readback(el2):
                    return f"Typed into ({_m2}): '{description}' on retry — verified."
        except Exception as e:
            _rethrow_if_stale(e)
        return f"Typed into '{description}', but the value could not be confirmed."

    async def new_tab(self, url: str = "") -> str:
        page = await self._get_page()
        ctx  = page.context
        try:
            new = await ctx.new_page()
        except Exception as e:
            if not _is_stale_error(e):
                raise
            print(f"[BrowserLifecycle] stale context detected: {str(e)[:100]}")
            await self._full_recover()
            new = await self._context.new_page()
        self._page = new
        if url:
            return await self.go_to(url)
        return "New tab opened."

    async def close_tab(self) -> str:
        page = self._page
        if page and not page.is_closed():
            ctx   = page.context
            await page.close()
            pages = ctx.pages
            self._page = pages[-1] if pages else None
            return "Tab closed."
        return "No active tab to close."

    async def screenshot(self, path: str = None) -> str:
        page = await self._get_page()
        try:
            save_path = path or str(Path.home() / "Desktop" / "jarvis_screenshot.png")
            await page.screenshot(path=save_path, full_page=False)
            return f"Screenshot saved: {save_path}"
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Screenshot error: {e}"

    async def back(self) -> str:
        page = await self._get_page()
        try:
            before = page.url
            await page.go_back(timeout=10_000)
            if page.url != before:
                return f"Navigated back — verified: {page.url}"
            return "Already at the earliest page (URL unchanged)."
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Back error: {e}"

    async def forward(self) -> str:
        page = await self._get_page()
        try:
            before = page.url
            await page.go_forward(timeout=10_000)
            if page.url != before:
                return f"Navigated forward — verified: {page.url}"
            return "Already at the latest page (URL unchanged)."
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Forward error: {e}"

    async def reload(self) -> str:
        page = await self._get_page()
        try:
            await page.reload(timeout=15_000)
            return f"Page reloaded — verified: {page.url}"
        except Exception as e:
            _rethrow_if_stale(e)
            return f"Reload error: {e}"

    async def close_browser(self) -> str:
        await self._async_close()
        return f"{self.browser_name} closed."

class _SessionRegistry:
    """Manages all active browser sessions."""

    def __init__(self):
        self._sessions:        dict[str, _BrowserSession] = {}
        self._active_browser:  str                        = ""
        self._lock             = threading.Lock()
        self._last_native_url: str                        = ""

    def has(self, browser_name: str | None = None) -> bool:
        """Is there an active automation session for this browser (or any)?"""
        with self._lock:
            if not browser_name:
                return bool(self._sessions)
            name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
            return name in self._sessions

    def note_native_url(self, url: str) -> None:
        self._last_native_url = url

    def pop_native_url(self) -> str:
        """Returns the last natively-opened URL once (consumed to avoid repeats)."""
        url, self._last_native_url = self._last_native_url, ""
        return url

    def _get_or_create(self, browser_name: str) -> _BrowserSession:
        with self._lock:
            if browser_name not in self._sessions:
                sess = _BrowserSession(browser_name)
                sess.start()
                self._sessions[browser_name] = sess
                print(f"[Registry] New session: {browser_name}")
            return self._sessions[browser_name]

    def get(self, browser_name: str | None = None) -> _BrowserSession:
        if not browser_name:
            browser_name = self._active_browser or _detect_default_browser()
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        sess = self._get_or_create(browser_name)
        self._active_browser = browser_name
        return sess

    def switch(self, browser_name: str) -> str:
        browser_name = _ALIASES.get(browser_name.lower().strip(), browser_name.lower().strip())
        self._get_or_create(browser_name)
        self._active_browser = browser_name
        return f"Active browser → {browser_name}"

    def close_one(self, browser_name: str) -> str:
        with self._lock:
            sess = self._sessions.pop(browser_name, None)
        if sess:
            sess.close()
            if self._active_browser == browser_name:
                self._active_browser = ""
            return f"{browser_name} closed."
        return f"No active session for: {browser_name}"

    def close_all(self) -> str:
        with self._lock:
            names    = list(self._sessions.keys())
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._active_browser = ""
        for s in sessions:
            try:
                s.close()
            except Exception:
                pass
        return "All browsers closed: " + (", ".join(names) if names else "none")

    def list_sessions(self) -> str:
        with self._lock:
            if not self._sessions:
                return "No active browser sessions."
            lines = []
            for name in self._sessions:
                marker = " ◀ active" if name == self._active_browser else ""
                lines.append(f"  • {name}{marker}")
            return "Open browsers:\n" + "\n".join(lines)


_registry = _SessionRegistry()


def _run_auto(sess: _BrowserSession, make, action: str,
              timeout: int = 60, retry_on_stale: bool = True) -> str:
    """Run one automation coroutine with exactly-once stale-session recovery.

    `make` is a zero-arg factory returning a FRESH coroutine, so the retry
    re-executes the original action instead of re-awaiting a dead future.
    Non-stale failures (timeout, selector miss, nav failure) are returned
    as-is and never trigger a relaunch. Verification strings produced by
    the session methods pass through untouched (Phase D preserved).
    Stale death is caught two ways: as a raised exception, and as a result
    string embedding the Playwright message (backstop for any path that
    formats the error instead of raising). Either way: recover once, retry
    the original action exactly once.
    """
    def _recover_once() -> bool:
        try:
            sess.run(sess._full_recover(), timeout=90)
        except Exception as re:
            print(f"[BrowserLifecycle] recovery failed: {re}")
            return False
        print("[BrowserLifecycle] recovery succeeded — retrying once")
        return True

    try:
        result = sess.run(make(), timeout=timeout)
    except concurrent.futures.TimeoutError:
        return f"Browser action '{action}' timed out ({timeout}s)."
    except Exception as e:
        if retry_on_stale and _is_stale_error(e):
            print(f"[BrowserLifecycle] stale context detected during "
                  f"'{action}': {str(e)[:100]}")
            if not _recover_once():
                return (f"Browser error ({action}): automation session was "
                        f"closed and could not be restored. "
                        f"No action was performed.")
            try:
                return sess.run(make(), timeout=timeout)
            except concurrent.futures.TimeoutError:
                return (f"Browser action '{action}' timed out ({timeout}s) "
                        f"after session recovery.")
            except Exception as e2:
                return f"Browser error ({action}): {e2}"
        return f"Browser error ({action}): {e}"
    if retry_on_stale and isinstance(result, str) and _is_stale_error(result):
        # get_text/get_url return PAGE CONTENT, which may legitimately
        # contain words like "disconnected" — never treat those as death.
        if action in ("get_text", "get_url"):
            return result
        print(f"[BrowserLifecycle] stale context detected in '{action}' "
              f"result: {result[:100]}")
        if not _recover_once():
            return (f"Browser error ({action}): automation session was "
                    f"closed and could not be restored. "
                    f"No action was performed.")
        try:
            return sess.run(make(), timeout=timeout)
        except concurrent.futures.TimeoutError:
            return (f"Browser action '{action}' timed out ({timeout}s) "
                    f"after session recovery.")
        except Exception as e2:
            return f"Browser error ({action}): {e2}"
    return result


def browser_control(
    parameters:    dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    browser = params.get("browser", "").lower().strip() or None
    result  = "Unknown action."

    if action == "switch":
        target = browser or params.get("target", "").lower().strip()
        result = _registry.switch(target) if target else "Please specify a browser."
        _log(player, result)
        return result

    if action == "list_browsers":
        result = _registry.list_sessions()
        _log(player, result)
        return result

    if action == "close_all":
        result = _registry.close_all()
        _log(player, result)
        return result

    if action == "close":
        target = browser or _registry._active_browser
        result = _registry.close_one(target) if target else "No browser specified."
        _log(player, result)
        return result

    # ── Navigation is ALWAYS native ──────────────────────────────────────────
    # go_to / search / new_tab open the site in the user's own browser —
    # their own profile, logged-in accounts and start page; exactly as if the
    # user had opened it themselves. A controlled window with about:blank never
    # opens here. The only exception: if an automation flow is already running,
    # navigation continues in that window (so multi-step tasks aren't split).
    if action in ("go_to", "search", "new_tab"):
        if _registry.has(browser):
            sess = _registry.get(browser)
            if action == "search":
                result = _run_auto(
                    sess, lambda: sess.search(params.get("query", ""),
                                              params.get("engine", "google")),
                    action)
            elif action == "new_tab":
                result = _run_auto(
                    sess, lambda: sess.new_tab(params.get("url", "")),
                    action)
            else:
                result = _run_auto(
                    sess, lambda: sess.go_to(params.get("url", "")),
                    action)
            _log(player, result)
            return result

        if action == "search":
            base    = _SEARCH_ENGINES.get(params.get("engine", "google").lower(),
                                          _SEARCH_ENGINES["google"])
            nav_url = base + params.get("query", "").replace(" ", "+")
        else:
            nav_url = params.get("url", "").strip()

        result = _open_native(nav_url, browser)
        if result.startswith("Opened") and nav_url:
            _registry.note_native_url(_normalize_url(nav_url))
        _log(player, result)
        return result

    # ── Interactive actions (click/type/read…) ───────────────────────────────
    # These require a physically controllable browser; the automation window
    # only opens here, and as soon as it opens it goes to the user's last
    # navigated page — it doesn't sit on a blank page.
    try:
        sess = _registry.get(browser)
    except Exception as e:
        result = f"Could not start browser session: {e}"
        _log(player, result)
        return result

    try:
        last = _registry.pop_native_url()
        if last:
            try:
                sess.run(sess.go_to(last))
            except Exception as e:
                print(f"[Browser] Could not resume last page ({last}): {e}")

        if action == "click":
            result = _run_auto(sess, lambda: sess.click(params.get("selector"), params.get("text")), action)
        elif action == "type":
            result = _run_auto(sess, lambda: sess.type_text(
                params.get("selector"), params.get("text", ""), params.get("clear_first", True)), action)
        elif action == "scroll":
            result = _run_auto(sess, lambda: sess.scroll(params.get("direction", "down"), int(params.get("amount", 500))), action)
        elif action == "fill_form":
            result = _run_auto(sess, lambda: sess.fill_form(params.get("fields", {})), action)
        elif action == "smart_click":
            result = _run_auto(sess, lambda: sess.smart_click(params.get("description", "")), action)
        elif action == "smart_type":
            result = _run_auto(sess, lambda: sess.smart_type(params.get("description", ""), params.get("text", "")), action)
        elif action == "get_text":
            result = _run_auto(sess, lambda: sess.get_text(), action)
        elif action == "get_url":
            result = _run_auto(sess, lambda: sess.get_url(), action)
        elif action == "get_links":
            try:
                _n = int(params.get("max_links", 30))
            except Exception:
                _n = 30
            result = _run_auto(sess, lambda n=_n: sess.get_links(n), action)
        elif action == "press":
            result = _run_auto(sess, lambda: sess.press(params.get("key", "Enter")), action)
        elif action == "close_tab":
            # Retrying close_tab after a recovery would close the FRESH tab,
            # so it recovers without re-closing and reports honestly instead.
            try:
                result = sess.run(sess.close_tab(), timeout=60)
            except concurrent.futures.TimeoutError:
                result = f"Browser action '{action}' timed out (60s)."
            except Exception as e:
                if _is_stale_error(e):
                    print(f"[BrowserLifecycle] stale context detected during "
                          f"'{action}': {str(e)[:100]}")
                    try:
                        sess.run(sess._full_recover(), timeout=90)
                        result = "Previous tab was already closed; browser session restored with a fresh tab."
                        print("[BrowserLifecycle] recovery succeeded")
                    except Exception as re:
                        print(f"[BrowserLifecycle] recovery failed: {re}")
                        result = (f"Browser error ({action}): automation session was "
                                  f"closed and could not be restored ({re}). "
                                  f"No action was performed.")
                else:
                    result = f"Browser error ({action}): {e}"
        elif action == "screenshot":
            result = _run_auto(sess, lambda: sess.screenshot(params.get("path")), action)
        elif action == "back":
            result = _run_auto(sess, lambda: sess.back(), action)
        elif action == "forward":
            result = _run_auto(sess, lambda: sess.forward(), action)
        elif action == "reload":
            result = _run_auto(sess, lambda: sess.reload(), action)
        else:
            result = f"Unknown browser action: '{action}'"

    except Exception as e:
        # Safety net for errors outside the per-action runner (session lookup
        # follow-ups, unexpected plumbing failures). Stale errors here mean
        # the resume-navigation itself hit a dead session; report honestly.
        result = f"Browser error ({action}): {e}"

    _log(player, result)
    return result


def _log(player, text: str):
    short = str(text)[:80]
    print(f"[Browser] {short}")
    if player:
        player.write_log(f"[browser] {short[:60]}")


# ── Tool declaration (auto-discovered by core/action_loader.py) ──────────────
TOOL = {
    "name": "browser_control",
    "description": "Controls any web browser EXCEPT YouTube (use youtube_video for anything on YouTube). Use for: opening websites (go_to), searching the general web (search), clicking elements, filling forms, scrolling, page screenshots, navigation, any web-based task. 'search' opens a search-results PAGE — it never answers a question; to answer from current information use web_search instead. 'get_links' returns the page's compact verified link list WITHOUT clicking — use it whenever a later step must act on a specific result ('open the first result', 'what did you find'). Simple open/search requests launch the user's own browser normally (their real profile and logged-in accounts); interactive actions (click, type, fill_form...) attach an automation browser. When a click target is not in the page DOM (native browser UI such as profile tiles, OS dialogs), smart_click falls back to one screenshot-based native click, verified the same way. Every interactive result says whether it was verified (navigated URL, changed state, read-back value) — never claim success the tool did not confirm. Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', 'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | get_links | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"
            },
            "browser": {
                "type": "STRING",
                "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."
            },
            "url": {
                "type": "STRING",
                "description": "URL for go_to / new_tab action"
            },
            "query": {
                "type": "STRING",
                "description": "Search query for search action"
            },
            "engine": {
                "type": "STRING",
                "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"
            },
            "selector": {
                "type": "STRING",
                "description": "CSS selector for click/type"
            },
            "text": {
                "type": "STRING",
                "description": "Text to click or type"
            },
            "description": {
                "type": "STRING",
                "description": "Element description for smart_click/smart_type"
            },
            "direction": {
                "type": "STRING",
                "description": "up | down for scroll"
            },
            "amount": {
                "type": "INTEGER",
                "description": "Scroll amount in pixels (default: 500)"
            },
            "key": {
                "type": "STRING",
                "description": "Key name for press action (e.g. Enter, Escape, F5)"
            },
            "path": {
                "type": "STRING",
                "description": "Save path for screenshot"
            },
            "incognito": {
                "type": "BOOLEAN",
                "description": "Open in private/incognito mode"
            },
            "clear_first": {
                "type": "BOOLEAN",
                "description": "Clear field before typing (default: true)"
            }
        },
        "required": [
            "action"
        ]
    },
    "handler": browser_control,
}
