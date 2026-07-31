#!/usr/bin/env python3
"""Build the dockable "Hue Clock.app" bundle in ~/Applications.

Run with `uv run --group build scripts/make_app.py`. py2app produces a real
bundle whose executable *is* the Python process — no launcher that execs away
from the bundle identity, which macOS 26 punishes by never rendering the menu
bar icon (FB21015611). The bundle is standalone (interpreter and deps
embedded): rerun this script after changing code or dependencies, and point it
at the config once with `ln -sf "$PWD/.env" ~/.config/hue_clock/.env`.

The icon (a pendant lamp casting a green "clocked in" glow on deep focus
blue) is rendered from code here; no image assets are checked in.
"""

import contextlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from setuptools import setup

APP = Path.home() / "Applications" / "Hue Clock.app"
SS = 2  # supersample factor for crisp edges after downscale

WHITE = (245, 247, 255, 255)
GREEN = (72, 235, 130)


def render_icon() -> Image.Image:
    s = 1024 * SS

    def p(v):
        return round(v * SS)  # 1024-grid coordinate → supersampled px

    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # Background: Big Sur-style rounded square on Apple's 824/1024 grid,
    # vertical deep-blue gradient (the focus lamp is deep blue).
    inset, radius = p(100), p(185)
    top, bottom = (63, 86, 255), (10, 15, 66)
    grad = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(inset, s - inset):
        t = (y - inset) / (s - 2 * inset)
        color = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=False))
        gdraw.line([(inset, y), (s - inset, y)], fill=(*color, 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [inset, inset, s - inset, s - inset], radius=radius, fill=255
    )
    img.paste(grad, (0, 0), mask)

    def layer(draw_fn):
        # Translucent shapes need their own layer: ImageDraw writes pixels,
        # it doesn't blend, so alpha only composites via alpha_composite.
        overlay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        draw_fn(ImageDraw.Draw(overlay))
        return Image.alpha_composite(img, overlay)

    cx = p(512)

    # Cone of light, dome edge down to the floor pool
    img = layer(
        lambda d: d.polygon(
            [
                (cx - p(128), p(408)),
                (cx + p(128), p(408)),
                (cx + p(312), p(816)),
                (cx - p(312), p(816)),
            ],
            fill=(190, 255, 215, 42),
        )
    )
    img = layer(
        lambda d: d.polygon(
            [
                (cx - p(112), p(408)),
                (cx + p(112), p(408)),
                (cx + p(216), p(816)),
                (cx - p(216), p(816)),
            ],
            fill=(210, 255, 228, 36),
        )
    )

    # Light pool on the floor
    img = layer(lambda d: d.ellipse([cx - p(330), p(772), cx + p(330), p(852)], fill=(*GREEN, 95)))
    img = layer(
        lambda d: d.ellipse([cx - p(212), p(786), cx + p(212), p(838)], fill=(150, 255, 190, 120))
    )

    # Bulb glow halos (drawn before the dome so it caps them)
    img = layer(
        lambda d: d.ellipse([cx - p(95), p(430 - 95), cx + p(95), p(430 + 95)], fill=(*GREEN, 50))
    )
    img = layer(
        lambda d: d.ellipse([cx - p(68), p(430 - 68), cx + p(68), p(430 + 68)], fill=(*GREEN, 95))
    )

    draw = ImageDraw.Draw(img)

    # Cord
    draw.rounded_rectangle([cx - p(8), p(150), cx + p(8), p(340)], radius=p(8), fill=WHITE)

    # Dome (half-disc, flat edge down) with a rim lip
    draw.pieslice(
        [cx - p(160), p(380 - 160), cx + p(160), p(380 + 160)], start=180, end=360, fill=WHITE
    )
    draw.rounded_rectangle([cx - p(168), p(366), cx + p(168), p(398)], radius=p(14), fill=WHITE)

    # Bulb: bright green core with white-hot center
    draw.ellipse([cx - p(48), p(430 - 48), cx + p(48), p(430 + 48)], fill=(*GREEN, 255))
    draw.ellipse([cx - p(24), p(424 - 24), cx + p(24), p(424 + 24)], fill=(235, 255, 242, 255))

    return img.resize((1024, 1024), Image.LANCZOS)


def build_icns(master: Image.Image, icns_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                side = base * scale
                suffix = "@2x" if scale == 2 else ""
                master.resize((side, side), Image.LANCZOS).save(
                    iconset / f"icon_{base}x{base}{suffix}.png"
                )
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)], check=True)


PLIST = {
    "CFBundleDisplayName": "Hue Clock",
    "CFBundleIdentifier": "com.dillonoleary.hue-clock",
    "CFBundleName": "Hue Clock",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundleVersion": "0.1.0",
    "LSMinimumSystemVersion": "12.0",
    # Regular app on purpose: the Dock icon shows while running, so
    # start/stop is visible and right-click → Quit works.
    "LSUIElement": False,
    "NSHighResolutionCapable": True,
    "NSLocalNetworkUsageDescription": (
        "Hue Clock watches the Philips Hue bridge on your local network "
        "to record clock in/out transitions."
    ),
}


def build_app() -> None:
    if not sysconfig.get_config_var("PYTHONFRAMEWORK"):
        raise SystemExit(
            "py2app needs a framework build of Python; uv's managed interpreters are not.\n"
            "Use e.g.:  uv run --python /opt/homebrew/opt/python@3.13/bin/python3.13"
            " --group build scripts/make_app.py"
        )
    if APP.exists():
        shutil.rmtree(APP)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        build_icns(render_icon(), work / "AppIcon.icns")
        # Generated entry script: keeps hue_clock.menubar a normal package
        # module inside the bundle instead of doubling as __main__.
        entry = work / "hue_clock_app.py"
        entry.write_text("from hue_clock.menubar import main\n\nmain()\n")
        sys.argv[1:] = ["py2app"]
        # setup() runs from inside the work dir: setuptools must not discover
        # the repo's pyproject.toml (whose uv_build metadata it can't digest),
        # and build/dist artifacts stay out of the repo.
        with contextlib.chdir(work):
            setup(
                name="Hue Clock",
                app=[str(entry)],
                options={
                    "py2app": {
                        "iconfile": str(work / "AppIcon.icns"),
                        # eventsourcing resolves persistence modules from topic
                        # strings at runtime — whole-package copies keep those
                        # dynamic imports out of py2app's static-analysis blind
                        # spot.
                        "packages": ["hue_clock", "eventsourcing"],
                        "plist": PLIST,
                    },
                },
            )
        shutil.move(str(next((work / "dist").glob("*.app"))), APP)
    # Ad-hoc sign the whole bundle: Apple silicon refuses unsigned arm64 code,
    # and py2app's own signing misses nested pieces — the app silently runs
    # under Rosetta (or not at all) without this.
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(APP)], check=True)
    print(f"built {APP}")
    print("drag it to the Dock; click to start, quit from the menu bar or Dock")


if __name__ == "__main__":
    build_app()
