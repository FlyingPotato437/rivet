#!/usr/bin/env python3
"""Build an honest, captioned walkthrough from real captured product states.

This is an editorial screenshot walkthrough, not a continuous screen recording.
It does not control a browser, invent states, animate typing, or manufacture
elapsed-time claims. Every shot is backed by an existing screenshot, recorded in
the output evidence manifest with its SHA-256 digest.

Usage:
  .venv/bin/python launch-video/build_demo_backup.py --example-manifest
  .venv/bin/python launch-video/build_demo_backup.py .data/demo-rehearsal/manifest.json

Optional shot keys: detail, crop=[left,top,width,height], source_label.
The final video has no soundtrack; its on-screen captions are also saved as SRT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
W, H, FPS = 1920, 1080, 30
BG = (15, 17, 20)
TEXT = (240, 239, 245)
MUTED = (156, 162, 175)
LAVENDER = (191, 169, 223)
LINE = (48, 51, 59)
PANEL = (23, 26, 31)
SCREEN = (64, 154, 1792, 722)


def font(size: int, heading: bool = False) -> ImageFont.FreeTypeFont:
    path = REPO / "apps/web/public/fonts" / (
        "sora-latin.woff2" if heading else "onest-latin.woff2"
    )
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.truetype(
            str(REPO / "apps/web/public/fonts/ibm-plex-sans.ttf"), size
        )


def wrapped(draw: ImageDraw.ImageDraw, text: str, face, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=face) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr[-6000:] or result.stdout[-6000:])


def still(shot: dict, index: int, total: int, out: Path) -> dict:
    image_path = Path(shot["image"])
    raw = image_path.read_bytes()
    screenshot = Image.open(image_path).convert("RGB")
    original_size = list(screenshot.size)
    if "crop" in shot:
        x, y, width, height = [int(v) for v in shot["crop"]]
        if min(x, y) < 0 or min(width, height) <= 0:
            raise ValueError(f"Invalid crop for {image_path}")
        if x + width > screenshot.width or y + height > screenshot.height:
            raise ValueError(f"Crop exceeds screenshot for {image_path}")
        screenshot = screenshot.crop((x, y, x + width, y + height))

    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)
    draw.text((64, 34), "rivet.", font=font(40, True), fill=TEXT)
    tag = "EDITED PRODUCT WALKTHROUGH  /  FICTIONAL SCENARIOS"
    tag_face = font(16)
    draw.text((W - 64 - draw.textlength(tag, font=tag_face), 46), tag, font=tag_face, fill=MUTED)
    draw.line((64, 105, W - 64, 105), fill=LINE, width=1)

    title = str(shot["title"])
    title_face = font(27, True)
    if draw.textlength(title, font=title_face) > W - 160:
        raise ValueError(f"Shot title is too long: {title}")
    draw.text((64, 116), title, font=title_face, fill=TEXT)

    x, y, width, height = SCREEN
    # A close fit makes real application text the central visual.
    scale = min(width / screenshot.width, height / screenshot.height)
    fitted = screenshot.resize(
        (round(screenshot.width * scale), round(screenshot.height * scale)),
        Image.Resampling.LANCZOS,
    )
    px = x + (width - fitted.width) // 2
    py = y + (height - fitted.height) // 2
    draw.rounded_rectangle((x, y, x + width, y + height), radius=9, fill=PANEL, outline=LINE, width=1)
    canvas.paste(fitted, (px, py))
    draw = ImageDraw.Draw(canvas)

    caption_face = font(29)
    caption_lines = wrapped(draw, str(shot["caption"]), caption_face, 1600)
    if len(caption_lines) > 2:
        raise ValueError(f"Shot caption exceeds two lines: {title}")
    for line_index, line in enumerate(caption_lines):
        draw.text((64, 907 + line_index * 39), line, font=caption_face, fill=TEXT)

    detail = str(shot.get("detail", ""))
    if detail:
        detail_face = font(19)
        if draw.textlength(detail, font=detail_face) > 1700:
            raise ValueError(f"Shot detail is too long: {title}")
        draw.text((64, 994), detail, font=detail_face, fill=MUTED)

    source_label = str(shot.get("source_label", "Actual product capture"))
    draw.text((64, 1042), source_label, font=font(15), fill=MUTED)
    position = f"{index + 1:02d} / {total:02d}"
    draw.text((W - 64 - draw.textlength(position, font=font(16)), 1040), position, font=font(16), fill=LAVENDER)
    for dot in range(total):
        dx = W // 2 + (dot - (total - 1) / 2) * 20
        draw.ellipse((dx - 3, 1046, dx + 3, 1052), fill=LAVENDER if dot == index else LINE)

    canvas.save(out)
    return {
        "image": str(image_path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "original_size": original_size,
        "crop": shot.get("crop"),
        "title": title,
        "caption": shot["caption"],
        "detail": detail,
        "source_label": source_label,
        "duration_seconds": shot["duration"],
        "rendered_frame": str(out),
    }


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def contact_sheet(frames: list[Path], target: Path) -> None:
    thumb_w = 576
    thumb_h = round(H * thumb_w / W)
    cols = min(3, len(frames))
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * thumb_h), BG)
    for i, path in enumerate(frames):
        thumb = Image.open(path).resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(thumb, ((i % cols) * thumb_w, (i // cols) * thumb_h))
    sheet.save(target)


def build(
    manifest_path: Path,
    output_dir: Path,
    allow_short: bool,
    name: str = "Rivet — Outlook Workflow Backup",
    max_duration: float = 95,
) -> None:
    if not name or Path(name).name != name or "/" in name or "\\" in name or len(name) > 120:
        raise ValueError("Use a simple output name without a directory or extension")
    if not 60 <= max_duration <= 600:
        raise ValueError("The maximum duration must be between 60 and 600 seconds")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("footage_mode") != "screenshot_walkthrough":
        raise ValueError("footage_mode must explicitly be screenshot_walkthrough")
    shots = manifest["shots"]
    if not shots:
        raise ValueError("No real screenshot shots were supplied")
    if manifest.get("fictional_scenario") is not True:
        raise ValueError("This demo must be explicitly marked fictional_scenario=true")
    for shot in shots:
        for key in ("image", "duration", "title", "caption"):
            if key not in shot:
                raise ValueError(f"Shot lacks required field: {key}")
        image_path = Path(shot["image"]).expanduser()
        if not image_path.is_absolute():
            image_path = (manifest_path.parent / image_path).resolve()
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        shot["image"] = str(image_path)
        if not 3 <= float(shot["duration"]) <= 20:
            raise ValueError("Each shot must be 3 to 20 seconds")
    total_duration = sum(float(shot["duration"]) for shot in shots)
    if not allow_short and not 60 <= total_duration <= max_duration:
        raise ValueError(f"Expected 60–{max_duration:g} seconds; manifest requests {total_duration:.1f}")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")

    output_dir.mkdir(parents=True, exist_ok=True)
    work = output_dir / "walkthrough-frames"
    work.mkdir(exist_ok=True)
    evidence = []
    clips = []
    frames = []
    subtitles = []
    elapsed = 0.0
    for index, shot in enumerate(shots):
        png = work / f"{index + 1:02d}.png"
        evidence.append(still(shot, index, len(shots), png))
        frames.append(png)
        clip = work / f"{index + 1:02d}.mp4"
        duration = float(shot["duration"])
        # Brief fades mark editorial cuts explicitly; no animation implies a UI action.
        fade_out = max(0, duration - 0.22)
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-i", str(png),
            "-t", str(duration), "-vf",
            f"fade=t=in:st=0:d=0.22,fade=t=out:st={fade_out:.3f}:d=0.22,format=yuv420p",
            "-r", str(FPS), "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "15",
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-colorspace", "bt709", "-an", str(clip),
        ])
        clips.append(clip)
        subtitles.append(
            f"{index + 1}\n{timestamp(elapsed)} --> {timestamp(elapsed + duration)}\n"
            f"{shot['caption']}\n"
        )
        evidence[-1]["starts_at_seconds"] = round(elapsed, 3)
        elapsed += duration
        print(f"Rendered {index + 1}/{len(shots)}: {shot['title']}", flush=True)

    concat = work / "concat.txt"
    # Paths live inside our output directory. Escaping supports apostrophes safely.
    concat.write_text("".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in clips))
    target = output_dir / f"{name}.mp4"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", "-movflags", "+faststart", "-metadata",
        "title=Rivet — edited product walkthrough — fictional scenarios", "-metadata",
        "comment=Edited screenshot walkthrough of actual product states; not a continuous screen recording.",
        str(target),
    ])
    (output_dir / f"{name}.srt").write_text("\n".join(subtitles))
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(target)
    ], check=True, capture_output=True, text=True)
    result = json.loads(probe.stdout)
    stream = next(s for s in result["streams"] if s["codec_type"] == "video")
    actual_duration = float(result["format"]["duration"])
    if stream["width"] != W or stream["height"] != H or abs(actual_duration - total_duration) > 0.2:
        raise RuntimeError("Final video dimensions or duration did not match the manifest")
    sheet = output_dir / "Rivet — Backup Contact Sheet.jpg"
    contact_sheet(frames, sheet)
    report = {
        "mode": "edited screenshot walkthrough of actual product states",
        "fictional_scenario": True,
        "continuous_recording": False,
        "scenario_note": manifest.get("scenario_note"),
        "capture_notes": manifest.get("capture_notes", []),
        "output": str(target),
        "output_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "duration_seconds": actual_duration,
        "resolution": [W, H],
        "fps": FPS,
        "measured_generation_wait_seconds": manifest.get("measured_generation_wait_seconds"),
        "measured_wait_note": manifest.get("measured_wait_note"),
        "shots": evidence,
        "ffprobe": result,
    }
    report_path = output_dir / "walkthrough-evidence.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(json.dumps({"video": str(target), "contact_sheet": str(sheet), "evidence": str(report_path),
                      "duration_seconds": actual_duration}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("--output-dir", type=Path, default=REPO / "output/video")
    parser.add_argument("--name", default="Rivet — Outlook Workflow Backup", help="Output filename stem; existing backups are preserved when a separate directory is used")
    parser.add_argument("--max-duration", type=float, default=95, help="Explicit longer walkthrough limit in seconds; defaults to 95")
    parser.add_argument("--allow-short", action="store_true", help="Allow a short local compositor smoke test")
    parser.add_argument("--example-manifest", action="store_true")
    args = parser.parse_args()
    if args.example_manifest:
        print(json.dumps({
            "footage_mode": "screenshot_walkthrough",
            "fictional_scenario": True,
            "measured_generation_wait_seconds": None,
            "measured_wait_note": "Populate only with timing from the completed rehearsal.",
            "shots": [{
                "image": "actual-product-capture.png", "duration": 9,
                "title": "Use a factual heading supported by this capture",
                "caption": "Describe what is visibly proven in this actual product state.",
                "detail": "Optional concise context, such as which source is live or a local fixture.",
                "source_label": "Actual product capture · fictional scenario",
            }],
        }, indent=2))
        return
    if not args.manifest:
        parser.error("Supply a real screenshot manifest or --example-manifest")
    build(args.manifest.resolve(), args.output_dir.resolve(), args.allow_short, args.name, args.max_duration)


if __name__ == "__main__":
    main()
