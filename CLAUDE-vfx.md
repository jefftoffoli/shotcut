# VFX Pipeline

## What We're Doing

We are building a library of automated visual effects for video. Final Cut Pro is our editing interface — all source footage lives in FCP projects, and final output goes back into FCP timelines.

The general workflow is:

1. **Source footage lives in FCP** — clips are edited, timed, and organized there
2. **Extract clip data from FCPXML** — parse the `.fcpxmld` export to get clip timings, source media paths, and edit structure
3. **Process video programmatically** — apply VFX (keying, compositing, generation, AI transforms) using command-line tools
4. **Return results to FCP** — rendered clips drop back into the FCP timeline

## Current Project: Hoodie Replacement

The first effect: replace the color/texture of a blue hoodie in 9 clips from "Unkillible Hoodie A."

**Goal:** Make the hoodie area transparent, then composite any texture or pattern underneath. The person, background, and everything else should remain untouched.

**What works so far:**
- FCPXML parsing extracts all 9 clip timings correctly
- The pipeline script generates valid multi-track project files
- Compositing (layering keyed footage over colored backgrounds) works
- The chroma key filter removes the hoodie color, but also bleeds into sky/foliage

**What needs solving:**
- Tight chroma key that only affects the hoodie, not the background
- May need a different approach entirely: AI segmentation, rotoscoping, or a mask-based method rather than pure color keying

## Future Effects

This is the first of many effects we'll build. Examples of what's coming:

- Texture/pattern replacement on clothing or surfaces
- Color grading and relighting specific objects
- AI-generated texture animation
- Object tracking and compositing
- Automated rotoscoping

Each effect should follow the same pattern: parse FCP project → process → return to FCP.

## Approach

- **FCP** is the UI — we don't need another video editor
- Keep scripts simple and single-purpose
- Test on single frames before rendering full clips

## Key Files

- `scripts/hoodie_replacement.py` — current pipeline script (FCPXML → processed output)
- Source footage: `/Volumes/Johnny 5/Untitled.fcpbundle/Domain Engineer/Original Media/`
- FCPXML: `~/Desktop/Unkillible Hoodie A.fcpxmld/Info.fcpxml`

## Lessons Learned

- FCPXML uses rational time format (`N/Ds`) — need `fractions.Fraction` for accurate conversion
- Test on single frames first (`ffmpeg -vframes 1`) before committing to full renders
- Color keying a muted fabric in a natural environment is fundamentally harder than green screen — consider AI/ML segmentation as an alternative
- Don't chase parameter tuning endlessly — if an approach isn't working after a few iterations, step back and reconsider the method
