"""Build a native stay-open AppleScript applet, using macOS tools only."""

import argparse
import math
import os
import plistlib
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
APP_ID = "local.next.taskassistant.launcher"


def apple_string(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def clean_finder_metadata(app):
    # Clean only legacy display metadata on our generated app, not security attributes.
    for item in [app] + list(app.rglob("*")):
        attributes = subprocess.run(["/usr/bin/xattr", str(item)], check=True, capture_output=True, text=True).stdout.splitlines()
        for attribute in ("com.apple.FinderInfo", "com.apple.ResourceFork"):
            if attribute in attributes:
                subprocess.run(["/usr/bin/xattr", "-d", attribute, str(item)], check=True)


def make_icon(path):
    """Render a fine rounded arrow on a quiet forest-green tile."""
    size = 1024

    def distance(x, y, ax, ay, bx, by):
        vx, vy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
        return math.hypot(x - ax - t * vx, y - ay - t * vy)

    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            qx, qy = abs(x + 0.5 - 512) - 260, abs(y + 0.5 - 512) - 260
            edge = math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - 188
            alpha = max(0, min(1, 0.5 - edge))
            white = max(0, min(1, 22.5 - min(
                distance(x + 0.5, y + 0.5, 344, 680, 680, 344),
                distance(x + 0.5, y + 0.5, 430, 344, 680, 344),
                distance(x + 0.5, y + 0.5, 680, 344, 680, 594),
            )))
            # A restrained tonal shift keeps the original green identity while
            # giving the flat mark some depth without shadows or decoration.
            tone = max(0, min(1, (y - 64) / 896))
            green = (59 - 12 * tone, 99 - 18 * tone, 79 - 14 * tone)
            ink = (248, 251, 246)
            rows.extend(tuple(round(base + (target - base) * white) for base, target in zip(green, ink)) + (round(255 * alpha),))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def build(output):
    output = Path(output).absolute()
    if output.exists():
        try:
            previous = plistlib.loads((output / "Contents/Info.plist").read_bytes())
        except (OSError, ValueError):
            raise SystemExit("目标已存在且无法确认是本启动器；未覆盖：" + str(output))
        if previous.get("CFBundleIdentifier") != APP_ID:
            raise SystemExit("目标是其他应用，未覆盖：" + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="next-build-", dir=output.parent) as temp:
        temp = Path(temp)
        script = (PROJECT / "macos/Next.applescript").read_text(encoding="utf-8")
        script = script.replace("@@PYTHON@@", apple_string(PROJECT / ".venv/bin/python"))
        script = script.replace("@@LAUNCHER@@", apple_string(PROJECT / "macos/launcher.py"))
        source = temp / "Next.applescript"
        source.write_text(script, encoding="utf-8")
        app = temp / "Next.app"
        subprocess.run(["/usr/bin/osacompile", "-s", "-o", str(app), str(source)], check=True)
        info_path = app / "Contents/Info.plist"
        info = plistlib.loads(info_path.read_bytes())
        # The macOS applet template points CFBundleIconName at its Assets.car
        # 'applet' icon. Remove it so our explicit .icns actually takes effect.
        info.pop("CFBundleIconName", None)
        info.update({
            "CFBundleIdentifier": APP_ID, "CFBundleName": "Next", "CFBundleDisplayName": "Next",
            "CFBundleShortVersionString": "1.0", "CFBundleVersion": "3",
            "CFBundleIconFile": "NextArrow-v3.icns", "LSMinimumSystemVersion": "11.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
        })
        info_path.write_bytes(plistlib.dumps(info))
        image = temp / "Next.png"
        make_icon(image)
        iconset = temp / "Next.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = "icon_{}x{}{}.png".format(points, points, "@2x" if scale == 2 else "")
                subprocess.run(
                    ["/usr/bin/sips", "-z", str(points * scale), str(points * scale), str(image), "--out", str(iconset / name)],
                    check=True, stdout=subprocess.DEVNULL,
                )
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(app / "Contents/Resources/NextArrow-v3.icns")], check=True)
        # osacompile may add legacy Finder metadata, which codesign rejects.
        # Clean only these build-artifact attributes; keep security attributes.
        clean_finder_metadata(app)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(app)], check=True)
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(app, output, symlinks=True)
        clean_finder_metadata(output)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(output)], check=True)
    print("已生成：" + str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Next.app using the existing project and venv")
    parser.add_argument("--output", type=Path, default=PROJECT / "macos/build/Next.app")
    build(parser.parse_args().output)
