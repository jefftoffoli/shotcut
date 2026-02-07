#!/usr/bin/env python3
"""
FCPXML Hoodie Replacement Pipeline

Parses an FCPXML project to extract hoodie clip timings (asset r8),
generates placeholder texture videos, and outputs a valid MLT XML
project with chroma-keyed hoodie composited over replacement textures.

Usage:
    # Dry run - parse and print clips
    python3 hoodie_replacement.py "path/to/Info.fcpxml" --dry-run

    # Generate with placeholders
    python3 hoodie_replacement.py "path/to/Info.fcpxml" \
        -o hoodie.mlt --output-dir ./textures --generator placeholder

    # Tune key color
    python3 hoodie_replacement.py "path/to/Info.fcpxml" \
        --key-color "#2244aa" --delta-h 0.35 --delta-c 0.35 --delta-i 0.40

    # Override source video path
    python3 hoodie_replacement.py "path/to/Info.fcpxml" \
        --source-video /local/path/to/hoodie.m4v -o hoodie.mlt

    # Render via melt
    python3 hoodie_replacement.py "path/to/Info.fcpxml" \
        --render --render-output output.mp4
"""

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional
from urllib.parse import unquote, urlparse


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FormatInfo:
    width: int = 1920
    height: int = 1080
    frame_rate_num: int = 24000
    frame_rate_den: int = 1001

    @property
    def fps(self) -> float:
        return self.frame_rate_num / self.frame_rate_den


@dataclass
class HoodieClip:
    """A single hoodie clip instance extracted from the FCPXML."""
    index: int
    timeline_offset: Fraction  # position on timeline in seconds
    media_start: Fraction      # in-point within the source media in seconds
    duration: Fraction         # duration in seconds
    parent_clip_name: str = ""

    @property
    def timeline_offset_frames(self) -> int:
        return int(self.timeline_offset * 24000 / 1001)

    @property
    def media_start_frames(self) -> int:
        return int(self.media_start * 24000 / 1001)

    @property
    def duration_frames(self) -> int:
        return int(self.duration * 24000 / 1001)

    @property
    def duration_seconds(self) -> float:
        return float(self.duration)


@dataclass
class ChromaKeyConfig:
    """Blue chroma key filter parameters for MLT's frei0r.select0r."""
    key_color: str = "#505191"
    invert: bool = False      # param 1: 0=remove key color, 1=keep key color
    delta_h: float = 0.5      # param 2: hue delta (in HCI mode)
    delta_c: float = 0.5      # param 3: chroma delta
    delta_i: float = 0.6      # param 4: intensity delta (wider for shadows)
    slope: float = 0.1        # param 5: edge slope
    colorspace: float = 1.0   # param 6: 0=RGB, 1=HCI
    shape: float = 0.5        # param 7: 0=Box, 0.5=Ellipsoid, 1=Diamond
    edge: float = 0.7         # param 8: 0=Hard, 0.35=Fat, 0.6=Normal, 0.7=Thin, 0.9=Slope
    operation: float = 0.5    # param 9: 0=Overwrite, 0.3=Max, 0.5=Min, 0.7=Add, 1=Sub

    # Alpha adjust (frei0r.alpha0ps) parameters
    alpha_mode: float = 0.4   # param 2: 0.4 = Shrink Soft
    alpha_amount: float = 0.3 # param 4: shrink amount


# ---------------------------------------------------------------------------
# FCPXML Parser
# ---------------------------------------------------------------------------

class FcpxmlParser:
    """Parse FCPXML and extract hoodie clips referencing asset r8."""

    def __init__(self, fcpxml_path: str, target_asset_id: str = "r8"):
        self.fcpxml_path = fcpxml_path
        self.target_asset_id = target_asset_id
        self.tree = ET.parse(fcpxml_path)
        self.root = self.tree.getroot()

    def parse_rational_time(self, time_str: str) -> Fraction:
        """Convert FCPXML rational time (e.g., '277987710/24000s') to seconds."""
        if time_str is None:
            return Fraction(0)
        time_str = time_str.rstrip('s')
        if '/' in time_str:
            num, den = time_str.split('/')
            return Fraction(int(num), int(den))
        return Fraction(time_str)

    def get_format_info(self) -> FormatInfo:
        """Extract format info from <format id="r1">."""
        fmt = FormatInfo()
        for format_el in self.root.iter('format'):
            if format_el.get('id') == 'r1':
                fmt.width = int(format_el.get('width', 1920))
                fmt.height = int(format_el.get('height', 1080))
                frame_dur = format_el.get('frameDuration', '1001/24000s')
                dur = self.parse_rational_time(frame_dur)
                # frameDuration is 1001/24000 -> fps = 24000/1001
                fmt.frame_rate_num = dur.denominator
                fmt.frame_rate_den = dur.numerator
                break
        return fmt

    def get_asset_source_path(self) -> Optional[str]:
        """Extract the source file path for the target asset."""
        for asset in self.root.iter('asset'):
            if asset.get('id') == self.target_asset_id:
                media_rep = asset.find('media-rep')
                if media_rep is not None:
                    src = media_rep.get('src', '')
                    parsed = urlparse(src)
                    return unquote(parsed.path)
        return None

    def extract_hoodie_clips(self) -> List[HoodieClip]:
        """Walk the spine and find all clip elements containing video ref=r8."""
        clips = []
        idx = 0

        # Find all <clip> elements anywhere that have name="Unkillible Hoodie A"
        # and contain <video ref="r8">
        for clip_el in self.root.iter('clip'):
            name = clip_el.get('name', '')
            if name != 'Unkillible Hoodie A':
                continue

            # Verify it references r8
            has_r8 = False
            for video_el in clip_el.findall('video'):
                if video_el.get('ref') == self.target_asset_id:
                    has_r8 = True
                    break
            if not has_r8:
                continue

            # Extract timing from the clip element
            # 'offset' in FCPXML = timeline position (absolute or relative)
            # 'start' = media in-point
            # 'duration' = clip duration
            offset_str = clip_el.get('offset', '0s')
            start_str = clip_el.get('start', '0s')
            duration_str = clip_el.get('duration', '0s')

            timeline_offset = self.parse_rational_time(offset_str)
            media_start = self.parse_rational_time(start_str)
            duration = self.parse_rational_time(duration_str)

            # Find parent clip name for context
            parent_name = ""
            # The parent spine clip's name gives context
            # We don't have direct parent access in ET, so use name from offset context

            clip = HoodieClip(
                index=idx,
                timeline_offset=timeline_offset,
                media_start=media_start,
                duration=duration,
                parent_clip_name=name,
            )
            clips.append(clip)
            idx += 1

        # Sort by media_start (since timeline_offset may be absolute spine offsets)
        clips.sort(key=lambda c: c.media_start)

        # Re-index after sorting
        for i, clip in enumerate(clips):
            clip.index = i

        return clips


# ---------------------------------------------------------------------------
# Texture Generators
# ---------------------------------------------------------------------------

class TextureGenerator:
    """Base class for texture video generation."""

    def generate(self, clip: HoodieClip, output_path: str,
                 format_info: FormatInfo) -> str:
        raise NotImplementedError


class PlaceholderGenerator(TextureGenerator):
    """Generate colored gradient videos using ffmpeg."""

    # Rotating color pairs for visual variety
    COLOR_PAIRS = [
        ("red", "purple"),
        ("blue", "cyan"),
        ("green", "yellow"),
        ("orange", "pink"),
        ("magenta", "blue"),
        ("cyan", "green"),
        ("yellow", "red"),
        ("purple", "magenta"),
        ("pink", "orange"),
        ("red", "blue"),
    ]

    def generate(self, clip: HoodieClip, output_path: str,
                 format_info: FormatInfo) -> str:
        c0, c1 = self.COLOR_PAIRS[clip.index % len(self.COLOR_PAIRS)]
        duration = max(clip.duration_seconds, 0.1)
        fps = f"{format_info.frame_rate_num}/{format_info.frame_rate_den}"

        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (f"gradients=s={format_info.width}x{format_info.height}"
                   f":c0={c0}:c1={c1}:speed=1:d={duration:.4f}"),
            "-r", fps,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "ultrafast",
            output_path,
        ]

        print(f"  Generating texture {clip.index:02d}: {c0}->{c1} "
              f"({duration:.2f}s) -> {os.path.basename(output_path)}")

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  WARNING: ffmpeg failed for texture {clip.index}:")
            print(f"    {result.stderr[:500]}")
            # Fall back to color producer in MLT XML (no file needed)
            return ""
        return output_path


# ---------------------------------------------------------------------------
# MLT XML Builder
# ---------------------------------------------------------------------------

class MltXmlBuilder:
    """Generate valid MLT XML with 3-track composite structure."""

    def __init__(self, format_info: FormatInfo, source_video: str,
                 chroma_config: ChromaKeyConfig):
        self.fmt = format_info
        self.source_video = source_video
        self.chroma = chroma_config
        self.frame_duration = Fraction(self.fmt.frame_rate_den,
                                       self.fmt.frame_rate_num)

    def frames_to_timecode(self, frames: int) -> str:
        """Convert frame count to MLT timecode HH:MM:SS.mmm."""
        total_seconds = float(Fraction(frames * self.fmt.frame_rate_den,
                                       self.fmt.frame_rate_num))
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"

    def fraction_to_frames(self, frac: Fraction) -> int:
        """Convert a time in seconds (Fraction) to frame count."""
        return int(frac * self.fmt.frame_rate_num / self.fmt.frame_rate_den)

    def _hex_to_frei0r_color(self, hex_color: str) -> str:
        """Convert #rrggbb to frei0r normalized color string 'r g b'."""
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16) / 255.0
        g = int(hex_color[2:4], 16) / 255.0
        b = int(hex_color[4:6], 16) / 255.0
        return f"{r:.6f} {g:.6f} {b:.6f}"

    def build(self, clips: List[HoodieClip],
              texture_paths: List[str]) -> str:
        """Build the complete MLT XML document."""
        root = ET.Element("mlt")
        root.set("LC_NUMERIC", "C")
        root.set("version", "7.22.0")
        root.set("title", "Hoodie Replacement")
        root.set("producer", "main_bin")

        # Profile
        profile = ET.SubElement(root, "profile")
        profile.set("description", f"{self.fmt.width}x{self.fmt.height} "
                     f"{self.fmt.fps:.6f}")
        profile.set("width", str(self.fmt.width))
        profile.set("height", str(self.fmt.height))
        profile.set("progressive", "1")
        profile.set("sample_aspect_num", "1")
        profile.set("sample_aspect_den", "1")
        profile.set("display_aspect_num", str(self.fmt.width))
        profile.set("display_aspect_den", str(self.fmt.height))
        profile.set("frame_rate_num", str(self.fmt.frame_rate_num))
        profile.set("frame_rate_den", str(self.fmt.frame_rate_den))
        profile.set("colorspace", "709")

        # Calculate total timeline length
        if not clips:
            total_frames = 0
        else:
            last_clip = max(clips, key=lambda c: self.fraction_to_frames(
                c.media_start) + c.duration_frames)
            total_frames = (self.fraction_to_frames(last_clip.media_start)
                            + last_clip.duration_frames)

        # We'll place clips by their media_start position in the hoodie source
        # This means track positions are based on where in the source each
        # clip comes from, creating a linear arrangement

        # Compute timeline positions: place clips sequentially with no gaps
        # between them (since they're different sections of the hoodie video)
        timeline_positions = []
        current_pos = 0
        for clip in clips:
            timeline_positions.append(current_pos)
            current_pos += clip.duration_frames

        total_frames = current_pos

        # --- Main bin playlist (required by Shotcut) ---
        main_bin = ET.SubElement(root, "playlist")
        main_bin.set("id", "main_bin")
        main_bin.set("title", "Hoodie Replacement")
        main_bin.set("shotcut:projectAudioChannels", "2")
        main_bin.set("shotcut:projectFolder", "1")

        prop = ET.SubElement(main_bin, "property")
        prop.set("name", "xml_retain")
        prop.text = "1"

        # --- Producers ---
        # Source hoodie producers (one per clip with filters)
        hoodie_producer_ids = []
        for i, clip in enumerate(clips):
            pid = f"hoodie_{i:02d}"
            hoodie_producer_ids.append(pid)
            producer = ET.SubElement(root, "producer")
            producer.set("id", pid)
            producer.set("in", str(
                self.fraction_to_frames(clip.media_start)))
            producer.set("out", str(
                self.fraction_to_frames(clip.media_start)
                + clip.duration_frames - 1))

            p = ET.SubElement(producer, "property")
            p.set("name", "resource")
            p.text = self.source_video

            p = ET.SubElement(producer, "property")
            p.set("name", "mlt_service")
            p.text = "avformat"

            p = ET.SubElement(producer, "property")
            p.set("name", "mlt_image_format")
            p.text = "rgba"

            # Filter 1: frei0r.select0r (Chroma Key: Advanced)
            f1 = ET.SubElement(producer, "filter")
            f1.set("id", f"chroma_{i:02d}")
            f1.set("mlt_service", "frei0r.select0r")

            # Param 0: key color
            fp = ET.SubElement(f1, "property")
            fp.set("name", "0")
            fp.text = self._hex_to_frei0r_color(self.chroma.key_color)

            # Param 1: invert (0=remove key color area, 1=keep key color area)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "1")
            fp.text = "1" if self.chroma.invert else "0"

            # Param 2: delta H (hue)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "2")
            fp.text = str(self.chroma.delta_h)

            # Param 3: delta C (chroma)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "3")
            fp.text = str(self.chroma.delta_c)

            # Param 4: delta I (intensity)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "4")
            fp.text = str(self.chroma.delta_i)

            # Param 5: slope
            fp = ET.SubElement(f1, "property")
            fp.set("name", "5")
            fp.text = str(self.chroma.slope)

            # Param 6: colorspace (1 = HCI)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "6")
            fp.text = str(self.chroma.colorspace)

            # Param 7: shape (0.5 = Ellipsoid)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "7")
            fp.text = str(self.chroma.shape)

            # Param 8: edge (0.7 = Thin)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "8")
            fp.text = str(self.chroma.edge)

            # Param 9: operation (0.5 = Minimum)
            fp = ET.SubElement(f1, "property")
            fp.set("name", "9")
            fp.text = str(self.chroma.operation)

            # threads property for performance
            fp = ET.SubElement(f1, "property")
            fp.set("name", "threads")
            fp.text = "0"

            # Filter 2: frei0r.alpha0ps (Alpha Channel: Adjust)
            f2 = ET.SubElement(producer, "filter")
            f2.set("id", f"alpha_{i:02d}")
            f2.set("mlt_service", "frei0r.alpha0ps")

            # Param 2: operation mode (0.4 = Shrink Soft)
            fp = ET.SubElement(f2, "property")
            fp.set("name", "2")
            fp.text = str(self.chroma.alpha_mode)

            # Param 3: threshold (same as amount)
            fp = ET.SubElement(f2, "property")
            fp.set("name", "3")
            fp.text = str(self.chroma.alpha_amount)

            # Param 4: amount
            fp = ET.SubElement(f2, "property")
            fp.set("name", "4")
            fp.text = str(self.chroma.alpha_amount)

        # Texture producers (one per clip)
        texture_producer_ids = []
        for i, clip in enumerate(clips):
            pid = f"texture_{i:02d}"
            texture_producer_ids.append(pid)
            producer = ET.SubElement(root, "producer")
            producer.set("id", pid)
            producer.set("in", "0")
            producer.set("out", str(clip.duration_frames - 1))

            p = ET.SubElement(producer, "property")
            p.set("name", "length")
            p.text = str(clip.duration_frames)

            if i < len(texture_paths) and texture_paths[i]:
                p = ET.SubElement(producer, "property")
                p.set("name", "resource")
                p.text = texture_paths[i]

                p = ET.SubElement(producer, "property")
                p.set("name", "mlt_service")
                p.text = "avformat"
            else:
                # Fallback: colored gradient producer
                colors = ["#ff0000", "#00ff00", "#0000ff", "#ff00ff",
                          "#ffff00", "#00ffff", "#ff8800", "#8800ff",
                          "#ff0088", "#0088ff"]
                color = colors[i % len(colors)]
                p = ET.SubElement(producer, "property")
                p.set("name", "resource")
                p.text = color

                p = ET.SubElement(producer, "property")
                p.set("name", "mlt_service")
                p.text = "color"

                p = ET.SubElement(producer, "property")
                p.set("name", "mlt_image_format")
                p.text = "rgba"

        # --- Background producer ---
        bg_producer = ET.SubElement(root, "producer")
        bg_producer.set("id", "black")

        p = ET.SubElement(bg_producer, "property")
        p.set("name", "resource")
        p.text = "0"

        p = ET.SubElement(bg_producer, "property")
        p.set("name", "mlt_service")
        p.text = "color"

        p = ET.SubElement(bg_producer, "property")
        p.set("name", "mlt_image_format")
        p.text = "rgba"

        p = ET.SubElement(bg_producer, "property")
        p.set("name", "length")
        p.text = str(total_frames)

        p = ET.SubElement(bg_producer, "property")
        p.set("name", "set.test_audio")
        p.text = "0"

        # --- Track 0: Background ---
        bg_playlist = ET.SubElement(root, "playlist")
        bg_playlist.set("id", "background")

        bg_entry = ET.SubElement(bg_playlist, "entry")
        bg_entry.set("producer", "black")
        bg_entry.set("in", "0")
        bg_entry.set("out", str(total_frames - 1))

        # --- Track 1: Texture playlist ---
        texture_playlist = ET.SubElement(root, "playlist")
        texture_playlist.set("id", "playlist0")
        texture_playlist.set("shotcut:video", "1")
        texture_playlist.set("shotcut:name", "V1 - Textures")

        for i, clip in enumerate(clips):
            entry = ET.SubElement(texture_playlist, "entry")
            entry.set("producer", texture_producer_ids[i])
            entry.set("in", "0")
            entry.set("out", str(clip.duration_frames - 1))

        # --- Track 2: Hoodie playlist (keyed) ---
        hoodie_playlist = ET.SubElement(root, "playlist")
        hoodie_playlist.set("id", "playlist1")
        hoodie_playlist.set("shotcut:video", "1")
        hoodie_playlist.set("shotcut:name", "V2 - Hoodie (Keyed)")

        for i, clip in enumerate(clips):
            entry = ET.SubElement(hoodie_playlist, "entry")
            entry.set("producer", hoodie_producer_ids[i])
            entry.set("in", str(
                self.fraction_to_frames(clip.media_start)))
            entry.set("out", str(
                self.fraction_to_frames(clip.media_start)
                + clip.duration_frames - 1))

        # --- Tractor (multi-track composition) ---
        tractor = ET.SubElement(root, "tractor")
        tractor.set("id", "tractor0")
        tractor.set("title", "Shotcut version 25.01.25")
        tractor.set("shotcut", "1")
        tractor.set("in", "0")
        tractor.set("out", str(total_frames - 1))

        # Multitrack
        multitrack = ET.SubElement(tractor, "multitrack")

        t0 = ET.SubElement(multitrack, "track")
        t0.set("producer", "background")

        t1 = ET.SubElement(multitrack, "track")
        t1.set("producer", "playlist0")

        t2 = ET.SubElement(multitrack, "track")
        t2.set("producer", "playlist1")

        # Transitions: mix (audio) for tracks 1 and 2
        for track_idx in [1, 2]:
            mix = ET.SubElement(tractor, "transition")
            mix.set("id", f"mix{track_idx}")
            mix.set("mlt_service", "mix")
            mix.set("always_active", "1")
            mix.set("sum", "1")
            mix.set("a_track", "0")
            mix.set("b_track", str(track_idx))

        # Transitions: qtblend (video composite) - all reference track 0 as base
        for a_track, b_track in [(0, 1), (0, 2)]:
            blend = ET.SubElement(tractor, "transition")
            blend.set("id", f"qtblend{b_track}")
            blend.set("mlt_service", "qtblend")
            blend.set("a_track", str(a_track))
            blend.set("b_track", str(b_track))
            blend.set("threads", "0")
            # First blend (background->texture) can stay disabled if desired
            # Second blend (texture->hoodie) must be active for compositing

        # Pretty-print
        self._indent(root)
        tree = ET.ElementTree(root)
        return ET.tostring(root, encoding="unicode", xml_declaration=True)

    def _indent(self, elem, level=0):
        """Add indentation to XML tree for readability."""
        indent = "\n" + "  " * level
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                self._indent(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent
        if level == 0:
            elem.tail = "\n"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """Orchestrate the full hoodie replacement pipeline."""

    def __init__(self, args):
        self.args = args
        self.parser = FcpxmlParser(args.fcpxml, args.asset_id)
        self.format_info = self.parser.get_format_info()
        self.chroma_config = ChromaKeyConfig(
            key_color=args.key_color,
            delta_h=args.delta_h,
            delta_c=args.delta_c,
            delta_i=args.delta_i,
            slope=args.slope,
            edge=args.edge,
        )

    def run(self):
        print(f"Parsing FCPXML: {self.args.fcpxml}")
        print(f"Format: {self.format_info.width}x{self.format_info.height} "
              f"@ {self.format_info.fps:.3f} fps")

        # Get source video path
        source_path = self.args.source_video
        if not source_path:
            source_path = self.parser.get_asset_source_path()
        if not source_path:
            print("ERROR: Could not determine source video path. "
                  "Use --source-video to specify.")
            sys.exit(1)
        print(f"Source video: {source_path}")

        # Extract hoodie clips
        clips = self.parser.extract_hoodie_clips()
        print(f"\nFound {len(clips)} hoodie clips:\n")

        for clip in clips:
            print(f"  [{clip.index:02d}] media_start={clip.media_start_frames:6d}f "
                  f"({float(clip.media_start):8.3f}s)  "
                  f"duration={clip.duration_frames:5d}f "
                  f"({clip.duration_seconds:7.3f}s)")

        total_duration = sum(c.duration_seconds for c in clips)
        print(f"\n  Total hoodie duration: {total_duration:.3f}s "
              f"({sum(c.duration_frames for c in clips)} frames)")

        if self.args.dry_run:
            print("\n[Dry run complete]")
            return

        # Generate textures
        texture_paths = []
        if self.args.generator == "placeholder":
            output_dir = self.args.output_dir
            os.makedirs(output_dir, exist_ok=True)
            print(f"\nGenerating placeholder textures in: {output_dir}")

            gen = PlaceholderGenerator()
            for clip in clips:
                out_path = os.path.join(output_dir,
                                        f"texture_{clip.index:02d}.mp4")
                result = gen.generate(clip, out_path, self.format_info)
                texture_paths.append(result)
        elif self.args.generator == "none":
            print("\nSkipping texture generation (using color fallbacks)")
            texture_paths = [""] * len(clips)
        else:
            print(f"\nUnknown generator: {self.args.generator}")
            sys.exit(1)

        # Build MLT XML
        print(f"\nBuilding MLT XML...")
        builder = MltXmlBuilder(self.format_info, source_path,
                                self.chroma_config)
        mlt_xml = builder.build(clips, texture_paths)

        output_path = self.args.output
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(mlt_xml)
        print(f"Written: {output_path}")

        # Optional render
        if self.args.render:
            render_output = self.args.render_output
            print(f"\nRendering via melt -> {render_output}")
            cmd = [
                "melt", output_path,
                "-consumer", f"avformat:{render_output}",
                "vcodec=libx264",
                "acodec=aac",
                "preset=medium",
                "crf=23",
            ]
            print(f"  Command: {' '.join(cmd)}")
            result = subprocess.run(cmd)
            if result.returncode != 0:
                print("WARNING: melt render failed")
            else:
                print(f"Render complete: {render_output}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="FCPXML Hoodie Replacement Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("fcpxml",
                        help="Path to FCPXML file (Info.fcpxml)")
    parser.add_argument("-o", "--output", default="hoodie.mlt",
                        help="Output MLT XML path (default: hoodie.mlt)")
    parser.add_argument("--output-dir", default="./textures",
                        help="Directory for generated texture videos "
                             "(default: ./textures)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and print clips without generating output")
    parser.add_argument("--generator", choices=["placeholder", "none"],
                        default="placeholder",
                        help="Texture generator to use "
                             "(default: placeholder)")
    parser.add_argument("--source-video",
                        help="Override source video path for hoodie footage")
    parser.add_argument("--asset-id", default="r8",
                        help="FCPXML asset ID for hoodie footage "
                             "(default: r8)")

    # Chroma key parameters
    chroma = parser.add_argument_group("chroma key parameters")
    chroma.add_argument("--key-color", default="#505191",
                        help="Key color hex (default: #505191)")
    chroma.add_argument("--delta-h", type=float, default=0.5,
                        help="Hue delta (default: 0.5)")
    chroma.add_argument("--delta-c", type=float, default=0.5,
                        help="Chroma delta (default: 0.5)")
    chroma.add_argument("--delta-i", type=float, default=0.6,
                        help="Intensity delta (default: 0.6)")
    chroma.add_argument("--slope", type=float, default=0.1,
                        help="Edge slope (default: 0.1)")
    chroma.add_argument("--edge", type=float, default=0.7,
                        help="Edge mode: 0=Hard, 0.35=Fat, 0.6=Normal, "
                             "0.7=Thin, 0.9=Slope (default: 0.7)")

    # Render options
    render = parser.add_argument_group("render options")
    render.add_argument("--render", action="store_true",
                        help="Render output via melt after generating MLT XML")
    render.add_argument("--render-output", default="output.mp4",
                        help="Render output file (default: output.mp4)")

    args = parser.parse_args()

    pipeline = Pipeline(args)
    pipeline.run()


if __name__ == "__main__":
    main()
