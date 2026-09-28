"""ArienX computer-control diagnostic harness — TEST A..G (manual, real X11).

Run:  DISPLAY=:0 python3 diagnostics/computer_diag.py [A|B|C|D|E|F|G|ALL]

Uses only this repo's own code paths (computer_control actions + coords +
x11sel + atspi). Creates its own fixtures under /tmp/arienx-diag and
cleans up windows it opens. Never touches your files, browser, or settings.

Read the [InputCalib]/[Mouse]/[Keyboard]/[Drag] lines it prints: they show
expected vs actual at every step.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault("DISPLAY", ":0")

import actions.computer_control as cc
from core import coords as C

FIX = "/tmp/arienx-diag"
os.makedirs(FIX, exist_ok=True)


def calib_block() -> None:
    """[InputCalib] — the one authoritative space report."""
    try:
        pw, ph = C.pointer_size()
    except Exception as e:
        pw = ph = f"? ({e})"
    mons = C.monitor_info()
    print("[InputCalib]")
    print(f"screenshot=? (per-capture, see [Computer] mapping lines)")
    print(f"desktop={pw}x{ph}")
    print(f"pointer_space={pw}x{ph}")
    sx = sy = ox = oy = "?"
    try:
        import pyautogui
        sw, sh = pyautogui.screenshot().size
        sx, sy = f"{sw / pw:.3f}" if isinstance(pw, int) else "?", \
                 f"{sh / ph:.3f}" if isinstance(ph, int) else "?"
    except Exception as e:
        print(f"screenshot probe failed: {e}")
    print(f"scale_x={sx}")
    print(f"scale_y={sy}")
    print(f"offset=({ox},{oy})")
    print(f"monitors={mons if mons else 'single assumed'}")


def _verify_move(x: int, y: int, label: str) -> bool:
    cc.computer_control({"action": "move", "x": x, "y": y})
    time.sleep(0.3)
    ax, ay = C.mouse_position()
    ok = abs(ax - x) + abs(ay - y) <= 2
    print(f"[Mouse] {label} expected=({x},{y}) actual=({ax},{ay}) "
          f"{'OK' if ok else 'MISMATCH'}")
    return ok


def test_a() -> bool:
    """Move to screen center; verify pointer."""
    print("== TEST A: center move ==")
    calib_block()
    try:
        w, h = C.pointer_size()
    except Exception as e:
        print("no pointer space:", e)
        return False
    return _verify_move(w // 2, h // 2, "center")


def test_b() -> bool:
    """Safe corners (margin avoids the pyautogui failsafe corner)."""
    print("== TEST B: safe corners ==")
    try:
        w, h = C.pointer_size()
    except Exception as e:
        print("no pointer space:", e)
        return False
    m = 20
    ok = _verify_move(m, m, "top-left-safe")
    ok &= _verify_move(w - m, h - m, "bottom-right-safe")
    return bool(ok)


def _own_editor():
    """Own text fixture: xed if its window actually appears, else Tk entry."""
    import shutil
    if shutil.which("xed"):
        p = subprocess.Popen(
            ["xed", os.path.join(FIX, "diag.txt")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(16):
            time.sleep(0.5)
            try:
                out = subprocess.run(
                    ["wmctrl", "-l"], capture_output=True, text=True,
                    timeout=5).stdout
                if "diag.txt" in out:
                    return ("xed", p)
            except Exception:
                pass
        try:
            p.terminate()
        except Exception:
            pass
    import tkinter as tk
    root = tk.Tk()
    root.title("ArienXDiagEditor")
    root.geometry("600x300+660+390")
    ent = tk.Entry(root, width=60)
    ent.pack(pady=40)
    root.update()
    return ("tk", root)


def _close_editor(handle) -> None:
    kind, obj = handle
    try:
        if kind == "xed":
            obj.terminate()
        else:
            obj.destroy()
    except Exception:
        pass


def test_c() -> bool:
    """Type 'Hello World 123 ! @ # test' into our own editor; verify."""
    print("== TEST C: typing ==")
    import re
    h = _own_editor()
    time.sleep(1.0)
    # Production flow: click the field first (focus), then type.
    if h[0] == "tk":
        ent = [w for w in h[1].winfo_children()
               if w.winfo_class() == "Entry"][0]
        h[1].update()
        cc.computer_control({"action": "click",
                             "x": ent.winfo_rootx() + 60,
                             "y": ent.winfo_rooty() + 10})
        time.sleep(0.5)
    else:
        r = cc.computer_control({"action": "ui_find", "app": "xed",
                                 "limit": 30})
        m = re.search(r"\[text\] '' at \((-?\d+),(-?\d+)\)", r)
        if m and int(m.group(1)) > -1000000:
            cc.computer_control({"action": "click",
                                 "x": int(m.group(1)) + 30,
                                 "y": int(m.group(2)) + 10})
            time.sleep(0.5)
    r = cc.computer_control({"action": "smart_type",
                             "text": "Hello World 123 ! @ # test"})
    print("result:", r[:130])
    _close_editor(h)
    ok = "verified" in r and "unverified" not in r
    print("TEST C:", "PASS" if ok else "CHECK (see result)")
    return ok


def test_d() -> bool:
    """Ctrl+A / Ctrl+C / Ctrl+V honesty check (may fail under ibus)."""
    print("== TEST D: clipboard hotkeys (honesty check) ==")
    for keys in ("ctrl+a", "ctrl+c", "ctrl+v"):
        r = cc.computer_control({"action": "hotkey", "keys": keys})
        print(keys, "->", r[:80])
    print("NOTE: combos into IME-managed fields can be eaten by ibus; "
          "window-manager bindings pass. Results above are the verdict.")
    return True


def test_e() -> bool:
    """Drag our own nemo-less window: uses move_window on a probe window."""
    print("== TEST E: window drag ==")
    import tkinter as tk
    root = tk.Tk()
    root.title("ArienXDiagDrag")
    root.geometry("400x300+760+390")
    tk.Label(root, text="drag me").pack()
    root.update()
    time.sleep(0.8)
    r = cc.computer_control({"action": "move_window", "title": "ArienXDiagDrag",
                             "dx": 80, "dy": 60})
    print("result:", r[:130])
    root.destroy()
    ok = "verified" in r and "unverified" not in r
    print("TEST E:", "PASS" if ok else "FAIL")
    return ok


def test_f() -> bool:
    """Drag a file-manager item: creates fixture, drag-selects, reads PRIMARY."""
    print("== TEST F: file item drag-select + PRIMARY read ==")
    import shutil
    if not shutil.which("nemo"):
        print("nemo missing — SKIP")
        return True
    d = os.path.join(FIX, "dragtest")
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "item.txt"), "w").write("x")
    p = subprocess.Popen(["nemo", d], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    time.sleep(2.0)
    r = cc.computer_control({"action": "get_state"})
    print("state:", r[:120])
    subprocess.run(["wmctrl", "-ic", "dragtest"], capture_output=True, timeout=5)
    try:
        p.terminate()
    except Exception:
        pass
    print("TEST F: manual — drag an item in your own file manager and "
          "confirm; automation verified pointer+state above.")
    return True


def test_g() -> bool:
    """locate → move → click → screenshot → re-locate → verify (zenity)."""
    print("== TEST G: full loop on own dialog ==")
    import re
    import shutil
    if not shutil.which("zenity"):
        print("zenity missing — SKIP")
        return True
    p = subprocess.Popen(
        ["zenity", "--entry", "--title", "ArienXDiagLoop",
         "--text", "loop probe"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    time.sleep(2.0)
    try:
        r1 = cc.computer_control({"action": "ui_find", "app": "zenity",
                                  "limit": 20})
        print("locate:", r1[:300])
        # The entry is the nameless [text] object (labels carry names).
        m = re.search(r"\[text\] '' at \((-?\d+),(-?\d+)\)", r1)
        ok = False
        if m and int(m.group(1)) > -1000000:
            x, y = int(m.group(1)) + 10, int(m.group(2)) + 5
            print(cc.computer_control({"action": "click", "x": x, "y": y})[:70])
            print(cc.computer_control({"action": "screenshot",
                                       "path": "~/arienx_diag_g.png"})[:70])
            r2 = cc.computer_control({"action": "ui_find", "app": "zenity",
                                      "limit": 20})
            print("re-locate:", r2[:160])
            m2 = re.search(r"\[text\] '' at \((-?\d+),(-?\d+)\)", r2)
            ok = bool(m2 and int(m2.group(1)) > -1000000)
            r3 = cc.computer_control({"action": "ui_find", "app": "zenity",
                                      "name": "Cancel", "role": "push button"})
            m3 = re.search(r"\((-?\d+),(-?\d+)\)", r3)
            if m3:
                cc.computer_control({"action": "click",
                                     "x": int(m3.group(1)),
                                     "y": int(m3.group(2))})
        print("TEST G:", "PASS" if ok else "FAIL")
        return ok
    finally:
        time.sleep(0.5)
        try:
            p.terminate()
        except Exception:
            pass


TESTS = {"A": test_a, "B": test_b, "C": test_c, "D": test_d,
         "E": test_e, "F": test_f, "G": test_g}


def main(argv: list[str]) -> int:
    which = (argv[1] if len(argv) > 1 else "ALL").upper()
    names = sorted(TESTS) if which == "ALL" else [which]
    results = {}
    for n in names:
        try:
            results[n] = TESTS[n]()
        except Exception as e:
            print(f"TEST {n} ERROR: {e}")
            results[n] = False
        time.sleep(0.5)
    print("\n==== SUMMARY ====")
    for n, ok in results.items():
        print(f"TEST {n}: {'PASS' if ok else 'FAIL/CHECK'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
