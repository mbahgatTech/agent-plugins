---
name: create-video
description: Plan, resume, render, and recover VideoPilot projects with its public MCP tools.
---

# Create a VideoPilot project

VideoPilot executes an explicit project plan. Draft the script, clip choices,
and timeline with the user; do not invent source selections or destructive
recovery steps.

## Public tool surface

These are the 20 tools exposed by `videopilot==0.1.7`. Optional
`project_root` values override the default `./projects` directory.

| Tool | Current arguments |
|---|---|
| `doctor` | none |
| `voices` | `engine="edge-tts"`, `locale?` |
| `list_projects` | `project_root?` |
| `project_status` | `slug`, `project_root?` |
| `init` | `slug`, `source?: list[str]`, `name?`, `project_root?` |
| `import_source` | `slug`, `path`, `source_id?`, `project_root?` |
| `read_state` | `slug`, `file`, `project_root?` |
| `write_state` | `slug`, `file`, `content`, `project_root?` |
| `tts` | `slug`, `only?: list[str]`, `force=false`, `project_root?` |
| `transcribe` | `slug`, `source_id`, `model="base"`, `language?`, `project_root?` |
| `silence` | `slug`, `source_id`, `threshold_db=-35.0`, `min_silence_sec=1.0`, `output?`, `project_root?` |
| `cut` | `slug`, `only?: list[str]`, `force=false`, `stream_copy=false`, `project_root?` |
| `compose` | `slug`, `project_root?` |
| `export` | `slug`, `edl=false`, `fcpxml=false`, `script=false`, `project_root?` |
| `schema` | none |
| `add_vo_segment` | `slug`, `id`, `text`, `voice?`, `rate?`, `pitch?`, `engine?`, `pause_after_ms?`, `position?`, `project_root?` |
| `add_slide` | `slug`, `voiceover?`, `background_color?`, `background_image?`, `title?`, `subtitle?`, `body?: list[str]`, `duration_sec?`, `pad_after_sec?`, `motion?`, `position?`, `project_root?` |
| `set_compose_output` | `slug`, `filename?`, `resolution?`, `fps?`, `video_bitrate?`, `audio_bitrate?`, `video_codec?`, `audio_codec?`, `project_root?` |
| `preview_slide` | `slug`, `index`, `project_root?` |
| `is_up_to_date` | `slug`, `scope?`, `project_root?` |

Use `file` and `content` with state tools, and `source_id` with source-specific
tools. Do not substitute older argument aliases.

## Start or resume safely

1. Call `doctor`. Stop and surface the complete result when `ok` is false or
   `exit_code` is nonzero.
2. Call `schema` before authoring state when the current structure is not
   already known.
3. For an existing slug, call `project_status`, then `read_state` for each
   present file. Call `is_up_to_date` before repeating `tts`, `cut`,
   `transcribe`, or `compose`.
4. For a new slug, call `init`. Its `source` argument is a list of paths.
   Preserve every returned source id and all user-provided ids.

Do not delete a project or recreate it to recover from a failed stage. Existing
state and intermediate files are diagnostic evidence and may contain completed
work.

## State contracts

`project.json` is engine-managed. Its `sources` entries contain `id`, relative
`path`, and detected media details. Read it through
`read_state(file="project")`; never replace it with `write_state`.

`script.json` contains:

- optional `voice_defaults` with `engine`, `voice`, `rate`, and `pitch`
- `segments`, each requiring `id` and `text`, with optional per-segment voice
  settings and `pause_after_ms`

Prefer `add_vo_segment` for incremental work. Duplicate ids return an error
without overwriting the existing segment.

`cut-plan.json` contains `clips`. Each clip requires `id`, a `source` id from
`project.json`, numeric `start` and `end` seconds, and may include `label`.

`compose-plan.json` contains an optional `output` block and a `timeline`:

- a slide needs `voiceover` or `duration_sec`; it may use color, image, text,
  body lines, padding, and supported motion
- a clip references a cut-plan `clip` id and may reference a voiceover
- `background_image` paths are relative to the project directory

The engine-managed `voice/manifest.json` and `clips/manifest.json` record
output paths and verified durations. Read them from `project_status` before
final timeline decisions.

## Voiceover workflow

1. Draft the complete narration and voice settings.
2. Add or update segments without changing established ids.
3. Show the final script and settings to the user.
4. **Immediately before calling `tts`, obtain explicit user approval.**
5. Call `is_up_to_date(scope="tts")`; skip synthesis when it reports current
   outputs.
6. Call `tts` only for the approved segment set. Do not use `force=true`
   unless the user approved regeneration.

Edge TTS uses the network. Azure Speech is optional and reads
`AZURE_SPEECH_KEY` and `AZURE_SPEECH_REGION` from the user's environment.

## Source selection and cutting

Use `transcribe` only when text is needed to choose spans; its first model
download can be large. Use `silence` when the requested rule is strictly
silence removal. Otherwise translate explicit timestamps or reviewed choices
into `cut-plan.json`.

1. Show the complete cut plan, including source ids and time spans.
2. **Immediately before calling `cut`, obtain explicit user approval.**
3. Call `is_up_to_date(scope="cut")`; skip current outputs.
4. Call `cut`. Keep `stream_copy=false` unless the user accepts keyframe-bound
   cuts.

Never remove a failed clip entry to make the stage look successful.

## Timeline, preview, and final render

Use `add_slide` for incremental slides and `set_compose_output` for output
changes. Use `write_state(file="compose-plan", content=...)` when clips and
slides need one reviewed full-timeline update.

Call `preview_slide(slug, index)` to render one timeline item. A successful
result has `exit_code: 0` and a nonempty path under
`out/preview-NNN.mp4`. Invalid indices and missing plans return an error; a
preview is a video, not a PNG.

Before the final render:

1. Compare voice and clip durations with the timeline.
2. Show the output settings and complete timeline to the user.
3. **Immediately before calling `compose`, obtain explicit user approval.**
4. Call `is_up_to_date(scope="compose")`; skip a current final output.
5. Call `compose` and report the returned `final_path`.

Use `export` only when the user asks for EDL, FCPXML, or a replay script.

## Failure handling

Treat either an `error` field or a nonzero `exit_code` as a failed call. Stop
the current stage, report the error and log, and leave the associated segment,
clip, state, and intermediate files intact. Do not prune failed entries, claim
success from a path alone, or start an unbounded retry loop.

After a correction, reread state and call `is_up_to_date` before retrying only
the affected ids. A successful render requires `exit_code: 0`, an existing
`final_path`, and independent media inspection when the result is being used
as a publication check.
