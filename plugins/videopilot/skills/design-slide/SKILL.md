---
name: design-slide
description: Create project-local RGB slide artwork without global Python package changes.
---

# Design a slide image

Use this skill when a VideoPilot slide needs typography or layout beyond the
engine's built-in overlays. Generate the artwork inside the project's
`sources/` directory, then reference it with `background_image`.

## Keep the dependency isolated

Write a temporary PEP 723 script and run it with `uv run --script`. This lets
`uv` resolve the pinned Pillow dependency in an isolated environment. Do not
run a global `pip install Pillow` and do not add Pillow to the marketplace.

The following script creates an opaque RGB PNG:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pillow==11.3.0",
# ]
# ///

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def parse_args() -> argparse.Namespace:
    """Collect the destination and slide copy for a reusable local render."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--subtitle", default="")
    return parser.parse_args()


def main() -> None:
    """Render a simple high-contrast slide as an opaque RGB PNG."""
    args = parse_args()
    output = Path(args.output)
    if output.suffix.lower() != ".png":
        raise SystemExit("--output must end in .png")
    output.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGB", (1920, 1080), "#0b132b")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=72)
    subtitle_font = ImageFont.load_default(size=36)

    draw.rounded_rectangle(
        (120, 150, 1800, 930),
        radius=40,
        fill="#162447",
        outline="#5bc0be",
        width=4,
    )
    draw.text(
        (200, 360),
        args.title,
        font=title_font,
        fill="#ffffff",
        stroke_width=1,
        stroke_fill="#ffffff",
    )
    if args.subtitle:
        draw.text(
            (204, 500),
            args.subtitle,
            font=subtitle_font,
            fill="#b8c5d6",
        )

    image.save(output, format="PNG", optimize=True)
    print(output)


if __name__ == "__main__":
    main()
```

Save it outside the repository or in a disposable workspace, then run:

```console
uv run --script make-slide.py --output projects/demo/sources/title.png --title "VideoPilot" --subtitle "A minimal rendered example"
```

Use the actual project root and slug. Verify the image before adding it:

```console
uv run --with pillow==11.3.0 python -c "from PIL import Image; im=Image.open('projects/demo/sources/title.png'); assert im.mode == 'RGB'; assert im.size == (1920, 1080)"
```

## Add the image to the timeline

Pass a path relative to the VideoPilot project directory:

```text
add_slide(
  slug="demo",
  background_image="sources/title.png",
  duration_sec=4.0
)
```

The title and subtitle are already pixels in the PNG. Omit `title`,
`subtitle`, and `body` from the `add_slide` call so VideoPilot does not draw
duplicate text over the artwork. A `voiceover` may replace `duration_sec` when
the slide should follow narration timing.

Call `preview_slide` and confirm its `out/preview-NNN.mp4` result before the
final composition. Keep the PNG in the disposable project for rendering; do
not copy generated images or project state into the marketplace repository.
