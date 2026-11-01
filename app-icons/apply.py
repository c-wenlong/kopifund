"""Write the icon sets in app-icons/ into every place the app icon ships.

    python3 app-icons/apply.py          # the default sets.json names
    python3 app-icons/apply.py iso      # make iso the default, then the same

sets.json lists the sets in the order a picker shows them and names the
default. The default is the icon each app opens with: the web app's favicons,
PWA icons and logo, the iOS app's AppIcon, the desktop bundle and its starting
page, and so the README's picture (the web app's icon-512.png). Every set, the
default too, is also written where a picker switches to it: the web app's
public/app-icons/<set>/, the iOS catalog's AppIcon-<set> and IconPicker-<set>
(AppIcons.swift lists them), and the desktop app's icons/sets/<set>.png, its
Dock icon. On iOS a set with a transparent mark also gets a dark and a tinted
look; on the Mac each icon sits on macOS's grid, a rounded square inset from
the edge. `tauri icon` renders what is drawn here, so apps/desktop needs
`npm ci` first (`make app-icon` does it).
"""
import argparse
import base64
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ICONS = Path(__file__).resolve().parent
REGISTRY = ICONS / "sets.json"
ROOT = ICONS.parent
WEB = ROOT / "apps/web/public"
DESKTOP = ROOT / "apps/desktop"
IOS = ROOT / "apps/ios/Alerts"
CATALOG = IOS / "Assets.xcassets"

NAME = re.compile(r"[a-z0-9-]+")
NEEDS = ("AppIcon-1024.png", "favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-192.png",
         "icon-512.png", "icon-maskable-512.png", "icon-picker-120.png", "icon-picker-180.png", "logo-square.svg")
# the files tauri.conf.json's bundle.icon lists; `tauri icon` writes more
DESKTOP_ICONS = ("32x32.png", "128x128.png", "128x128@2x.png", "icon.icns", "icon.png")
# what the web app's picker swaps in (apps/web/src/components/appIcon.ts)
WEB_SET = ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-picker-120.png")
XCODE_INFO = {"author": "xcode", "version": 1}
# Apple's macOS icon grid: an 824 px rounded square, 100 px in from a 1024 px canvas
MAC_INSET, MAC_SIDE, MAC_RADIUS = 100, 824, 185


def registry() -> dict:
    reg = json.loads(REGISTRY.read_text())
    names = [s["name"] for s in reg["sets"]]
    folders = sorted(p.name for p in ICONS.iterdir() if (p / "AppIcon-1024.png").is_file())
    if sorted(names) != folders:
        sys.exit(f"sets.json lists {sorted(names)} but app-icons/ holds {folders}")
    if reg["default"] not in names:
        sys.exit(f"sets.json's default, {reg['default']}, is not one of its sets")
    for name in names:
        if not NAME.fullmatch(name):
            sys.exit(f"{name}: a set's name is lower-case letters, digits and hyphens")
        missing = [f for f in NEEDS if not (ICONS / name / f).is_file()]
        if missing:
            sys.exit(f"app-icons/{name} is missing {', '.join(missing)}")
    return reg


def write_registry(reg: dict) -> None:
    rows = ",\n".join(f"    {json.dumps(s, ensure_ascii=False)}" for s in reg["sets"])
    REGISTRY.write_text(f'{{\n  "default": {json.dumps(reg["default"])},\n  "sets": [\n{rows}\n  ]\n}}\n')


def tauri_icon(source: Path, out: Path, *extra: str) -> None:
    run = subprocess.run(["npx", "--no-install", "tauri", "icon", str(source), "-o", str(out), *extra],
                         cwd=DESKTOP, capture_output=True, text=True)
    if run.returncode:
        sys.exit(run.stdout + run.stderr + "tauri icon failed: run `npm ci` in apps/desktop first")


def render(svg: str, size: int) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "icon.svg"
        source.write_text(svg)
        tauri_icon(source, Path(tmp) / "out", "--png", str(size))
        return (Path(tmp) / "out" / f"{size}x{size}.png").read_bytes()


def svg_body(path: Path) -> str:
    return re.search(r"<svg[^>]*>(.*)</svg>", path.read_text(), re.S).group(1)


def svg_doc(inner: str, side: int = 1024) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {side} {side}" '
            f'width="{side}" height="{side}">{inner}</svg>\n')


def mac_svg(src: Path) -> str:
    scale = MAC_SIDE / 1024
    return svg_doc(f'<defs><clipPath id="macos"><rect x="{MAC_INSET}" y="{MAC_INSET}" width="{MAC_SIDE}" '
                   f'height="{MAC_SIDE}" rx="{MAC_RADIUS}"/></clipPath></defs><g clip-path="url(#macos)">'
                   f'<g transform="translate({MAC_INSET} {MAC_INSET}) scale({scale})">{svg_body(src / "logo-square.svg")}'
                   f'</g></g>')


def tinted_svg(mark: Path) -> str:
    # iOS tints a grey icon itself, so the mark goes grey and keeps its light and shade
    return svg_doc('<defs><filter id="grey" color-interpolation-filters="sRGB"><feColorMatrix type="saturate" '
                   f'values="0"/></filter></defs><g filter="url(#grey)">{svg_body(mark)}</g>')


def web_logo(src: Path) -> str:
    mark = src / "mark-transparent.svg"
    if mark.is_file():
        return mark.read_text()
    # riso is raster-first and has no transparent mark: its 120 px tile stands in, small enough to cache
    png = base64.b64encode((src / "icon-picker-120.png").read_bytes()).decode()
    return svg_doc(f'<image width="120" height="120" href="data:image/png;base64,{png}"/>', 120)


def write_contents(folder: Path, images: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"images": images, "info": XCODE_INFO}, indent=2, separators=(",", " : "), sort_keys=True)
    (folder / "Contents.json").write_text(body + "\n")


def ios_looks(src: Path) -> dict[str, bytes]:
    """The app icon's PNGs by file name: the light one, and a dark and a tinted
    one when the set has a transparent mark (iOS 18 draws its own dark ground)."""
    looks = {"AppIcon.png": (src / "AppIcon-1024.png").read_bytes()}
    mark = src / "mark-transparent.svg"
    if mark.is_file():
        looks["AppIcon-dark.png"] = render(mark.read_text(), 1024)
        looks["AppIcon-tinted.png"] = render(tinted_svg(mark), 1024)
    return looks


def app_icon(folder: Path, looks: dict[str, bytes]) -> None:
    images = []
    for filename in looks:
        image = {"filename": filename, "idiom": "universal", "platform": "ios", "size": "1024x1024"}
        if filename != "AppIcon.png":
            look = filename.removeprefix("AppIcon-").removesuffix(".png")
            image["appearances"] = [{"appearance": "luminosity", "value": look}]
        images.append(image)
    write_contents(folder, images)
    for filename, png in looks.items():
        (folder / filename).write_bytes(png)


def swift_list(reg: dict) -> str:
    rows = "\n".join(f"        IconSet(name: {json.dumps(s['name'])}, label: {json.dumps(s['label'], ensure_ascii=False)}, "
                     f"about: {json.dumps(s['about'], ensure_ascii=False)})," for s in reg["sets"])
    return f"""// Written by app-icons/apply.py from app-icons/sets.json: run `make app-icon`, do not edit.

/// The icon sets in Assets.xcassets: `AppIcon` is `primary`, and every set is
/// also `AppIcon-<name>`, an alternate icon, with `IconPicker-<name>` its 60 pt tile.
enum AppIcons {{
    struct IconSet: Sendable {{
        let name: String
        let label: String
        let about: String
    }}

    static let primary = {json.dumps(reg["default"])}
    static let sets: [IconSet] = [
{rows}
    ]
}}
"""


def web(reg: dict) -> None:
    default = ICONS / reg["default"]
    for name in ("favicon.ico", "favicon.svg", "apple-touch-icon.png", "icon-192.png", "icon-512.png", "icon-maskable-512.png"):
        shutil.copyfile(default / name, WEB / name)
    (WEB / "logo.svg").write_text(web_logo(default))
    shutil.rmtree(WEB / "app-icons", ignore_errors=True)
    for s in reg["sets"]:
        src, out = ICONS / s["name"], WEB / "app-icons" / s["name"]
        out.mkdir(parents=True)
        for name in WEB_SET:
            shutil.copyfile(src / name, out / name)
        (out / "logo.svg").write_text(web_logo(src))


def ios(reg: dict) -> None:
    looks = {s["name"]: ios_looks(ICONS / s["name"]) for s in reg["sets"]}
    shutil.rmtree(CATALOG / "AppIcon.appiconset")
    app_icon(CATALOG / "AppIcon.appiconset", looks[reg["default"]])
    for old in [*CATALOG.glob("AppIcon-*.appiconset"), *CATALOG.glob("IconPicker-*.imageset")]:
        shutil.rmtree(old)
    for s in reg["sets"]:
        name, src = s["name"], ICONS / s["name"]
        app_icon(CATALOG / f"AppIcon-{name}.appiconset", looks[name])
        picker = CATALOG / f"IconPicker-{name}.imageset"
        write_contents(picker, [
            {"idiom": "universal", "scale": "1x"},
            {"filename": "icon-picker-120.png", "idiom": "universal", "scale": "2x"},
            {"filename": "icon-picker-180.png", "idiom": "universal", "scale": "3x"},
        ])
        for n in (120, 180):
            shutil.copyfile(src / f"icon-picker-{n}.png", picker / f"icon-picker-{n}.png")
    (IOS / "AppIcons.swift").write_text(swift_list(reg))


def sorted_icns(data: bytes) -> bytes:
    # `tauri icon` writes the entries in hash order, so the same art gives a new
    # file on every run; sorted by type, a rerun leaves the file as it was
    entries, i = [], 8
    while i < len(data):
        size = struct.unpack(">I", data[i + 4:i + 8])[0]
        entries.append(data[i:i + size])
        i += size
    body = b"".join(sorted(entries, key=lambda e: e[:4]))
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def desktop(reg: dict) -> None:
    default = ICONS / reg["default"]
    icons = DESKTOP / "src-tauri/icons"
    shutil.copyfile(default / "icon-512.png", DESKTOP / "ui/icon.png")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "icon.svg"
        source.write_text(mac_svg(default))
        tauri_icon(source, Path(tmp) / "out")
        for name in DESKTOP_ICONS:
            shutil.copyfile(Path(tmp) / "out" / name, icons / name)
    (icons / "icon.icns").write_bytes(sorted_icns((icons / "icon.icns").read_bytes()))
    shutil.rmtree(icons / "sets", ignore_errors=True)
    (icons / "sets").mkdir()
    for s in reg["sets"]:
        # the Dock draws at most 256 pt, so 512 px is its largest
        (icons / "sets" / f"{s['name']}.png").write_bytes(render(mac_svg(ICONS / s["name"]), 512))


def main() -> int:
    reg = registry()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("set", nargs="?", choices=[s["name"] for s in reg["sets"]],
                    help=f"make this set the default (now {reg['default']})")
    chosen = ap.parse_args().set
    if chosen and chosen != reg["default"]:
        reg["default"] = chosen
        write_registry(reg)
    web(reg)
    ios(reg)
    desktop(reg)
    print(f"{reg['default']} is the app icon on the web app, iOS and the desktop app; "
          f"{', '.join(s['name'] for s in reg['sets'])} are in every picker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
