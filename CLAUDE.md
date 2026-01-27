# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shotcut is a free, open-source, cross-platform video editor built with C++ and Qt 6. It uses the MLT multimedia framework for video processing and playback.

## Build Commands

```bash
# Configure (run from a separate build directory)
cmake -GNinja -DCMAKE_INSTALL_PREFIX=/usr/local/ /path/to/shotcut

# Build
cmake --build .

# Install (required - Shotcut needs QML files at runtime)
cmake --install .

# Code formatting (requires clang-format-14)
ninja clang-format        # Auto-format code
ninja clang-format-check  # Check formatting without changes

# QML formatting
ninja qmlformat

# Spell check
ninja codespell
```

## Dependencies

- Qt 6.4+ (Charts, Multimedia, Network, OpenGL, QuickControls2, QuickWidgets, Sql, WebSockets, Widgets, Xml)
- MLT 7.36.0+ (mlt++-7)
- FFTW
- FFmpeg
- Frei0r
- SDL

## Architecture

### Core Components

- **MainWindow** (`src/mainwindow.cpp`) - Central application controller, manages all UI and editing state
- **MltController** (`src/mltcontroller.cpp`) - Wrapper around MLT framework, handles media playback and composition
- **MultitrackModel** (`src/models/multitrackmodel.cpp`) - Timeline track management and editing operations

### Key Directories

- `src/commands/` - Undo/redo command implementations (TimelineCommands, FilterCommands, PlaylistCommands)
- `src/docks/` - Panel implementations (TimelineDock, PlaylistDock, EncodeDock, FiltersDock, etc.)
- `src/models/` - Qt data models for timeline, playlist, keyframes, markers, subtitles
- `src/jobs/` - Background job system for encoding, transcoding, screen capture
- `src/widgets/` - Custom Qt widgets including audio/video scopes
- `src/qml/` - QML UI components
- `src/qml/filters/` - Filter UI definitions (~150 filters, each in its own folder)

### Design Patterns

- **Command Pattern**: All editing operations use undo/redo commands in `src/commands/`
- **Model-View**: Qt models in `src/models/` with QML views in `src/qml/views/`
- **Job Queue**: Background processing via AbstractJob subclasses in `src/jobs/`

### Filter System

Filters are exposed via QML UI definitions in `src/qml/filters/`. Each filter has:
- `meta.qml` - Filter metadata and parameters
- `ui.qml` - User interface components

To add a new filter UI for an existing MLT filter, create a new folder in `src/qml/filters/`.

## Code Style

- C++11 standard
- clang-format-14 with Qt Creator style (see `.clang-format`)
- 4-space indentation, no tabs
- 100 character line limit
- Braces on new line for classes, structs, and functions; same line for control statements
- Pointer alignment: `Type *name` (space before asterisk)
- Copyright should be assigned to Meltytech, LLC

## UI Conventions

See https://www.shotcut.com/notes/ui-conventions/ for case, alignment, and spacing guidelines.

## CLI and Automation (Experimental)

This section documents potential approaches for programmatic/AI-driven video editing using Shotcut's underlying MLT framework.

### MLT XML as the Control Interface

Shotcut projects are stored as MLT XML (`.mlt` files). This format can be programmatically generated or manipulated:

- **Producers**: Media sources (video, audio, images)
- **Playlists**: Sequences of clips with timing
- **Tractors**: Multi-track compositions
- **Filters**: Effects applied to clips
- **Transitions**: Between-clip effects

Documentation:
- https://www.mltframework.org/docs/mltxml/
- https://www.shotcut.org/notes/mltxml-annotations/
- DTD: https://github.com/mltframework/mlt/blob/master/src/modules/xml/mlt-xml.dtd

### melt CLI

The `melt` command-line tool (part of MLT, not Shotcut) can render MLT XML files without the GUI:
```bash
melt project.mlt -consumer avformat:output.mp4
```

### Interchange Formats

**Supported exports**: MLT XML, EDL (CMX 3600), Chapters, SRT subtitles

**No FCPXML support**: Shotcut does not import/export Final Cut Pro XML. Existing converters:
- [mlt2fcp](https://github.com/ggambetta/mlt2fcp) - MLT → FCPXML (Python)
- No FCPXML → MLT converter currently exists

### Format Mapping (MLT ↔ FCPXML)

| Concept | MLT XML | FCPXML |
|---------|---------|--------|
| Media source | `<producer>` | `<asset>` |
| Sequence | `<playlist>` | `<sequence>` → `<spine>` |
| Empty space | `<blank>` | `<gap>` |
| Clips | `<entry>` | `<video>` / `<audio>` |
| Timing | `HH:MM:SS.mmm` | Rational `N/Ds` |

A bidirectional FCPXML ↔ MLT converter could enable FCP as UI with MLT as extensible backend.
