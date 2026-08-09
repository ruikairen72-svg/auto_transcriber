"""
Gradio web application for auto-transcription and speaker diarization.

Five-step workflow:
1. Upload MP4 & optional EAF template
2. Process (extract audio → transcribe → diarize)
3. Map speaker IDs to human-readable tier names via interactive cards
4. Review and edit segments in the interactive timeline editor
5. Download EAF + PFSX annotation files
"""

from __future__ import annotations

import os
import sys
import json
import time
import traceback
from datetime import datetime
import gradio as gr

# ---------------------------------------------------------------------------
# Monkey-patch: fix gradio_client 1.3.0 bug where JSON Schema
# `additionalProperties: true` (a boolean) crashes `get_type()` /
# `_json_schema_to_python_type()`.
# ---------------------------------------------------------------------------
import gradio_client.utils as _gc_utils

_orig_get_type = _gc_utils.get_type
_orig_json_schema_to_python_type = _gc_utils._json_schema_to_python_type


def _patched_get_type(schema):
    if isinstance(schema, bool):
        return "boolean"
    return _orig_get_type(schema)


def _patched_json_schema_to_python_type(schema, defs):
    if isinstance(schema, bool):
        return "bool"
    return _orig_json_schema_to_python_type(schema, defs)


_gc_utils.get_type = _patched_get_type
_gc_utils._json_schema_to_python_type = _patched_json_schema_to_python_type
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Monkey-patch: speechbrain LazyModule — when k2/WFST is not installed (it
# isn't needed for pyannote), the lazy import of ``speechbrain.utils.importutils``
# raises ImportError deep inside unrelated import chains (e.g. torch →
# transformers → linecache → speechbrain).  Swallow it.
# ---------------------------------------------------------------------------
import speechbrain.utils.importutils as _sbiu

_orig_ensure = _sbiu.LazyModule.ensure_module


def _patched_ensure(self, *args, **kwargs):
    try:
        return _orig_ensure(self, *args, **kwargs)
    except ImportError:
        pass


_sbiu.LazyModule.ensure_module = _patched_ensure
# ---------------------------------------------------------------------------

from config import MAX_SPEAKERS, TEMP_DIR, validate_token
from processors import run_pipeline, cleanup_temp
from processors.exporter import generate_eaf, generate_pfsx
from timeline_editor import build_timeline_html


# ---------------------------------------------------------------------------
# CSS — clean, professional look for linguistics annotation work
# ---------------------------------------------------------------------------
CUSTOM_CSS = """
.gradio-container { max-width: 1400px !important; margin: 0 auto !important; }
.speaker-card {
    border: 1px solid #e0e0e0;
    border-radius: 12px;
    padding: 20px;
    margin: 10px 0;
    background: #fafafa;
    transition: box-shadow 0.2s;
}
.speaker-card:hover { box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
.speaker-header {
    font-size: 1.1em; font-weight: 600; color: #333;
    margin-bottom: 8px; padding-bottom: 8px;
    border-bottom: 2px solid #2196F3;
}
.instruction-box {
    background: #e3f2fd; border-left: 4px solid #2196F3;
    padding: 12px 16px; border-radius: 4px; margin: 12px 0;
}
footer { visibility: hidden; }
"""


# ---------------------------------------------------------------------------
# Error logging
# ---------------------------------------------------------------------------

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def _log_error(exc: Exception) -> None:
    """Append the exception with full traceback to ``logs/error.log``."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "error.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 60}\n")
        f.write(f"[{timestamp}] {type(exc).__name__}: {exc}\n")
        traceback.print_exc(file=f)
        f.write(f"{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Main processing function (generator with gr.Progress)
# ---------------------------------------------------------------------------

def process_video(video_file, num_speakers, template_eaf_file, state, template_tiers,
                  progress=gr.Progress()):
    """
    Generator that runs the full pipeline and yields incremental UI updates.

    Output order (must match ``dynamic_outputs`` in ``build_ui``):
        [pipeline_state, step1, step3, step4, step5, status_md,
         (card_col, audio, samples, name)×10,
         video_player, timeline_html, hidden_json, eaf_dl, pfsx_dl, template_tiers_state]
    """

    # ---------- helper: "empty" state (step 1 visible, rest hidden) ----------
    def _empty():
        updates = [
            state,                                    # 0  pipeline_state
            gr.update(visible=True),                  # 1  step1
            gr.update(visible=False),                 # 2  step3 (speaker cards)
            gr.update(visible=False),                 # 3  step4 (timeline)
            gr.update(visible=False),                 # 4  step5 (download)
            gr.update(value="", visible=True),        # 5  status_md
        ]
        for _ in range(MAX_SPEAKERS):
            updates += [
                gr.update(visible=False),              # card col
                gr.update(visible=False, value=None),  # audio
                gr.update(visible=False, value=""),    # samples
                gr.update(visible=False, value="", choices=[]),  # name (Dropdown)
            ]
        updates += [
            gr.update(visible=False, value=None),     # video_player
            gr.update(visible=False, value=""),       # timeline_html
            gr.update(visible=False, value=""),       # hidden_json
            gr.update(visible=False, value=None),     # eaf_dl
            gr.update(visible=False, value=None),     # pfsx_dl
            [],                                       # template_tiers_state
        ]
        return updates

    # ==================================================================
    # 1. Input validation
    # ==================================================================
    if video_file is None:
        raise gr.Error("请先上传一个 MP4 文件")

    filepath = str(video_file)
    if not filepath.lower().endswith(".mp4"):
        raise gr.Error(
            f"不支持的文件格式，仅支持 .mp4 文件。"
            f"当前文件: {os.path.basename(filepath)}"
        )

    # ==================================================================
    # 1b. Parse optional EAF template
    # ==================================================================
    # Robust across pympi versions: tiers can be dict, list, or tuple.
    # Tier values can be dict (older pympi) or tuple (newer pympi:
    #   (begin_ts, end_ts, attributes_dict, media_index, ...))
    # ==================================================================
    template_eaf_path: str | None = None
    tier_names: list[str] = []
    tier_hierarchy: dict[str, str | None] = {}

    # ---- Persistent template storage ----
    TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    PERSIST_TEMPLATE = os.path.join(TEMPLATES_DIR, "_template.eaf")

    if template_eaf_file is not None:
        # Copy uploaded template to persistent location so it survives across runs
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        import shutil
        shutil.copy2(str(template_eaf_file), PERSIST_TEMPLATE)
        template_eaf_file = PERSIST_TEMPLATE
    elif os.path.exists(PERSIST_TEMPLATE):
        # Reuse previously uploaded template
        template_eaf_file = PERSIST_TEMPLATE

    if template_eaf_file is not None:
        try:
            from pympi import Elan

            tpl_path = str(template_eaf_file)
            tpl = Elan.Eaf(tpl_path)

            # ---- resolve tier items (name, attributes) across formats ----
            def _resolve_tier_items(tiers_obj):
                """Yield (tier_name, attributes_dict_or_None) regardless of
                whether *tiers_obj* is a dict, list, or tuple, and regardless
                of whether individual values are dicts or tuples."""
                if isinstance(tiers_obj, dict):
                    for name, val in tiers_obj.items():
                        yield name, val
                elif isinstance(tiers_obj, (list, tuple)):
                    for item in tiers_obj:
                        if isinstance(item, str):
                            yield item, None
                        elif isinstance(item, tuple) and len(item) >= 2:
                            yield item[0], item[1]
                        elif isinstance(item, dict):
                            yield item.get("TIER_ID", str(item)), item
                else:
                    # fallback: try iterating
                    for name in tiers_obj:
                        yield name, None

            for tn, tv in _resolve_tier_items(tpl.tiers):
                tier_names.append(tn)

                # Extract PARENT_REF from whatever format the tier value uses
                attrs: dict | None = None
                if isinstance(tv, dict):
                    attrs = tv
                elif isinstance(tv, (tuple, list)) and len(tv) >= 3:
                    # pympi ≥ 2.x tuple: (begin, end, attr_dict, media_index, ...)
                    mid = tv[2]
                    if isinstance(mid, dict):
                        attrs = mid

                pr = attrs.get("PARENT_REF") if attrs else None
                tier_hierarchy[tn] = pr if pr else None

            # ---- Fallback: infer hierarchy from naming convention ----
            # If PARENT_REF is not set, try "child@parent" pattern.
            # E.g. "interaction@INB" → parent is "INB"
            for tn in tier_names:
                if tier_hierarchy.get(tn) is None and "@" in tn:
                    parts = tn.rsplit("@", 1)
                    if len(parts) == 2:
                        candidate_parent = parts[1]
                        if candidate_parent in tier_names and candidate_parent != tn:
                            tier_hierarchy[tn] = candidate_parent

            template_eaf_path = os.path.join(TEMP_DIR, "_template.eaf")
            tpl.to_file(template_eaf_path)

            print(
                f"✅ 成功解析模板，共 {len(tier_names)} 个层，"
                f"层级关系: {tier_hierarchy}"
            )
        except Exception as exc:
            raise gr.Error(f"解析 EAF 模板失败: {exc}")

    # ==================================================================
    # 2. Main processing
    # ==================================================================
    try:
        cleanup_temp(TEMP_DIR)
        t_start = time.time()

        progress(0.05, desc="🔊 Extracting audio (16 kHz mono WAV)...")
        updates = _empty()
        updates[5] = gr.update(value="🔊 **Stage 1/4** — Extracting audio...")
        yield updates

        ns = int(num_speakers) if num_speakers and int(num_speakers) > 0 else None
        pipeline_result = run_pipeline(
            filepath,
            TEMP_DIR,
            num_speakers=ns,
            progress_callback=lambda amount, desc: progress(amount, desc=desc),
        )

        # Stash the template path, tier names, and hierarchy for later use
        pipeline_result["_template_eaf_path"] = template_eaf_path
        pipeline_result["_tier_names"] = tier_names
        pipeline_result["_tier_hierarchy"] = tier_hierarchy
        pipeline_result["_video_path"] = filepath

        # ==============================================================
        # Success — show speaker cards (Step 3)
        # ==============================================================
        progress(1.0, desc="✅ Complete")

        speakers = pipeline_result["speakers"]
        clips = pipeline_result["speaker_clips"]
        samples = pipeline_result["transcript_samples"]
        aligned = pipeline_result["aligned_segments"]

        n = min(len(speakers), MAX_SPEAKERS)
        elapsed = time.time() - t_start

        final = [
            pipeline_result,                                   # 0  pipeline_state
            gr.update(visible=False),                          # 1  step1
            gr.update(visible=True),                           # 2  step3 (speaker cards)
            gr.update(visible=False),                          # 3  step4 (timeline)
            gr.update(visible=False),                          # 4  step5 (download)
            gr.update(                                         # 5  status_md
                value=(
                    f"✅ **Done!** Detected **{n}** speaker(s), "
                    f"**{len(aligned)}** segments in **{elapsed:.1f}s**.\n\n"
                    f"Listen to each voice clip below and enter a tier name "
                    f"for each speaker."
                ),
                visible=True,
            ),
        ]

        # Populate speaker cards
        for i in range(MAX_SPEAKERS):
            if i < n:
                spk = speakers[i]
                clip_path = clips.get(spk, "")
                sample_texts = samples.get(spk, [])
                sample_display = "\n".join(
                    f"• {t}" for t in sample_texts if t
                ) or "(no transcription available)"

                final += [
                    gr.update(visible=True),                              # card col
                    gr.update(value=clip_path, visible=True),             # audio
                    gr.update(value=sample_display, visible=True),        # samples
                    gr.update(value="", choices=tier_names, visible=True),  # name dropdown
                ]
            else:
                final += [
                    gr.update(visible=False),
                    gr.update(visible=False, value=None),
                    gr.update(visible=False, value=""),
                    gr.update(visible=False, value=""),
                ]

        # Step 4 & 5 elements — hidden initially
        final += [
            gr.update(visible=False, value=None),     # video_player
            gr.update(visible=False, value=""),       # timeline_html
            gr.update(visible=False, value=""),       # hidden_json
            gr.update(visible=False, value=None),     # eaf_dl
            gr.update(visible=False, value=None),     # pfsx_dl
            tier_names,                               # template_tiers_state
        ]

        yield final

    except Exception as e:
        traceback.print_exc()
        _log_error(e)
        raise gr.Error(f"处理失败: {str(e)}")


# ---------------------------------------------------------------------------
# Advance to timeline editor (Step 3 → Step 4)
# ---------------------------------------------------------------------------

def advance_to_timeline(*args):
    """
    Apply speaker → tier name mapping to all segments, build the timeline
    editor HTML, and transition from Step 3 to Step 4.

    Args layout:
        args[0]            — pipeline_state (dict)
        args[1]            — template_tiers (list[str])
        args[2] .. args[11] — name input strings for each card
    """
    pipeline_state = args[0]
    template_tiers = args[1]
    name_values = args[2 : 2 + MAX_SPEAKERS]

    if not pipeline_state or not pipeline_state.get("speakers"):
        return [
            gr.update(visible=True),                              # step3
            gr.update(visible=False),                             # step4
            gr.update(visible=False, value=None),                 # video
            gr.update(visible=False, value=""),                   # timeline
            gr.update(visible=False, value=""),                   # hidden_json
            gr.update(value="⚠️  Please process a video first.", visible=True),
        ]

    speakers = pipeline_state["speakers"]
    aligned = pipeline_state["aligned_segments"]
    tier_names = pipeline_state.get("_tier_names", template_tiers if template_tiers else [])
    filepath = pipeline_state.get("_video_path", "")

    # Build speaker → tier name mapping from user input
    speaker_names: dict[str, str] = {}
    for i, spk in enumerate(speakers):
        name = name_values[i].strip() if i < len(name_values) else ""
        if name == "":
            name = spk  # default to speaker ID
        speaker_names[spk] = name

    # Apply mapping: replace speaker IDs with tier names in segments
    applied_segments = []
    for seg in aligned:
        seg = dict(seg)  # shallow copy
        old_speaker = seg.get("speaker", "SPEAKER_UNKNOWN")
        new_name = speaker_names.get(old_speaker, old_speaker)
        seg["speaker"] = new_name
        seg["tierName"] = new_name
        applied_segments.append(seg)

    # Store applied segments back into pipeline state
    pipeline_state["_applied_segments"] = applied_segments
    pipeline_state["_speaker_names"] = speaker_names

    # Build tier options for the timeline dropdown (use the mapped names)
    tier_options = tier_names if tier_names else list(speaker_names.values())
    if not tier_options:
        tier_options = list(speaker_names.values())

    tier_hierarchy_map = pipeline_state.get("_tier_hierarchy", {})

    audio_duration = max((s.get("end", 0) for s in applied_segments), default=60.0)

    # Serialise for hidden JSON
    segment_data = {
        "segments": applied_segments,
        "speakers": list(speaker_names.values()),
        "tierOptions": tier_options,
        "audioDuration": audio_duration,
        "tierHierarchy": tier_hierarchy_map,
    }
    json_str = json.dumps(segment_data, ensure_ascii=False)

    # Build timeline editor HTML
    html_content = build_timeline_html(
        segments=applied_segments,
        speakers=list(speaker_names.values()),
        tier_names=tier_options,
        video_path=filepath,
        audio_duration=audio_duration,
        tier_hierarchy=tier_hierarchy_map,
    )

    tier_summary = "\n".join(
        f"- **{spk}** → **{name}**" for spk, name in speaker_names.items()
    )

    return [
        gr.update(visible=False),                              # step3 (hide speaker cards)
        gr.update(visible=True),                               # step4 (show timeline)
        gr.update(visible=False),                              # video_player (hidden — editor has its own)
        gr.update(value=html_content, visible=True),           # timeline_html
        gr.update(value=json_str, visible=False),              # hidden_json
        gr.update(                                             # advance_status
            value=f"✅ **Mapping applied!**\n\n{tier_summary}",
            visible=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Generate EAF + PFSX files
# ---------------------------------------------------------------------------

def generate_files(pipeline_state, hidden_json, template_tiers, output_dir):
    """
    Read edited segments from the hidden JSON textbox, build speaker name
    mappings from the user-assigned ``tierName`` fields, generate EAF +
    PFSX files, and return download button updates.
    """
    def _err(msg: str):
        """Return error status so it's displayed in the Step 5 status area."""
        return [
            gr.update(visible=False),                              # step5_col
            gr.update(visible=False, value=None),                  # eaf_dl
            gr.update(visible=False, value=None),                  # pfsx_dl
            gr.update(value=f"❌ **Export failed:** {msg}", visible=True),  # gen_status
        ]

    try:
        # Defensive: if output_dir looks like JSON data (passed by mistake), use default
        if output_dir and output_dir.strip().startswith("{") and len(output_dir.strip()) > 200:
            import logging
            logging.warning("output_dir appears to be JSON data (length=%d), using ~/Desktop fallback", len(output_dir))
            out_dir = os.path.expanduser("~/Desktop")
        else:
            out_dir = os.path.expanduser(output_dir.strip()) if output_dir and output_dir.strip() else os.path.expanduser("~/Desktop")
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        return _err(f"无法创建输出目录 \"{output_dir}\": {exc}")

    if not pipeline_state or not pipeline_state.get("speakers"):
        return _err("No pipeline state. Please process a video first.")

    edited_segments = []
    print(f"[Export] hidden_json present: {bool(hidden_json)}, "
          f"length: {len(hidden_json) if hidden_json else 0}")
    if hidden_json:
        try:
            data = json.loads(hidden_json)
            edited_segments = data.get("segments", [])
            # Diagnostic: show first 3 segment texts so user can verify edits
            sample_texts = [(s.get("text", "")[:40], s.get("start", 0), s.get("end", 0))
                          for s in edited_segments[:3]]
            print(f"[Export] Parsed {len(edited_segments)} segments from frontend. "
                  f"hidden_json={len(hidden_json)} chars. "
                  f"Sample: {sample_texts}")
        except (json.JSONDecodeError, TypeError) as exc:
            preview = (hidden_json or "")[:200]
            print(f"[Export] JSON parse FAILED: {exc}. hidden_json preview: {preview}")
            return _err(f"Failed to parse edited segment data from the timeline editor: {exc}\n\n"
                         "Please go back to Step 4 and verify all edits are complete, "
                         "then click the generate button again.")

    fallback_used = False
    if not edited_segments:
        fallback_used = True
        edited_segments = pipeline_state.get("_applied_segments",
                          pipeline_state.get("aligned_segments", []))
        fallback_sample = [(s.get("text", "")[:40],) for s in edited_segments[:3]]
        print(f"[Export] WARNING: Using FALLBACK data ({len(edited_segments)} segments). "
              f"Sample: {fallback_sample}")

    if not edited_segments:
        return _err("No edited segments found. Please return to Step 4 and verify your edits before exporting.")

    audio_path = pipeline_state.get("audio_path", "")

    # Build speaker → tier name mapping from the edited segments
    spk_tier_votes: dict[str, dict[str, int]] = {}
    for seg in edited_segments:
        spk = seg.get("speaker", "SPEAKER_UNKNOWN")
        tier = seg.get("tierName", spk)
        if spk not in spk_tier_votes:
            spk_tier_votes[spk] = {}
        spk_tier_votes[spk][tier] = spk_tier_votes[spk].get(tier, 0) + 1

    speaker_names: dict[str, str] = {}
    for spk, votes in spk_tier_votes.items():
        best_tier = max(votes, key=votes.get)
        speaker_names[spk] = best_tier

    # Ensure all known speakers have a mapping
    known_speakers = set()
    for seg in edited_segments:
        known_speakers.add(seg.get("speaker", "SPEAKER_UNKNOWN"))
    for spk in known_speakers:
        if spk not in speaker_names:
            speaker_names[spk] = spk

    # Build aligned_segments for exporter
    aligned_for_export = []
    for seg in edited_segments:
        aligned_for_export.append({
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", ""),
            "speaker": seg.get("speaker", "SPEAKER_UNKNOWN"),
            "words": seg.get("words", []),
        })

    # Generate files in user-specified output directory
    eaf_path = os.path.join(out_dir, "output.eaf")
    pfsx_path = os.path.join(out_dir, "output.pfsx")

    try:
        template_eaf_path = pipeline_state.get("_template_eaf_path")
        generate_eaf(aligned_for_export, speaker_names, audio_path, eaf_path, template_eaf_path)
        generate_pfsx(speaker_names, pfsx_path, template_tiers if template_tiers else None)
    except Exception as exc:
        return _err(f"File generation error: {exc}")

    tier_list = "\n".join(
        f"- **{name}** ({spk})" for spk, name in speaker_names.items()
    )

    source_note = ""
    if fallback_used:
        source_note = ("\n\n⚠️ **Note:** Timeline edits were NOT applied — "
                       "the frontend did not send edited segment data. "
                       "Exported data comes from the original transcription.")

    return [
        gr.update(visible=True),
        gr.update(value=eaf_path, visible=True),
        gr.update(value=pfsx_path, visible=True),
        gr.update(
            value=(
                f"✅ **Files generated!**\n\n"
                f"**Tiers created:**\n{tier_list}\n\n"
                f"**Segments:** {len(aligned_for_export)}"
                f"{source_note}\n\n"
                f"Click the download buttons below."
            ),
            visible=True,
        ),
    ]


# ---------------------------------------------------------------------------
# Build the UI
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    with gr.Blocks(
        css=CUSTOM_CSS,
        title="Auto Transcriber & Speaker Diarization",
        theme=gr.themes.Soft(),
        head="""
<script>
// Listen for timeline editor sync messages from the iframe.
// The iframe calls syncToParent() after every edit, which sends
// edited segments via postMessage (with DOM direct-write as primary).
window.addEventListener('message', function(event) {
    if (event.data && event.data.type === 'TIMELINE_SYNC') {
        try {
            var hiddenEl = document.querySelector('#hidden-json-input textarea, #hidden-json-input input');
            if (hiddenEl) {
                hiddenEl.value = event.data.json;
                hiddenEl.dispatchEvent(new Event('input', {bubbles: true}));
                console.log('[Parent] Received TIMELINE_SYNC: ' + event.data.segCount + ' segments via postMessage');
            }
        } catch(e) {
            console.error('[Parent] Failed to process TIMELINE_SYNC:', e);
        }
    }
});
</script>
""",
    ) as app:

        # ---- Hidden state ----
        pipeline_state = gr.State({})

        # ==============================================================
        # STEP 1 — Upload & Process
        # ==============================================================
        with gr.Column(visible=True) as step1_col:
            gr.Markdown("# 🎙️ Auto Transcriber & Speaker Diarization")
            gr.Markdown(
                "Upload an MP4 video to automatically transcribe speech, "
                "identify speakers, and export ELAN-format annotation files "
                "ready for linguistic analysis."
            )

            with gr.Row():
                video_input = gr.File(
                    label="📁 Upload MP4 Video",
                    file_types=[".mp4"],
                    file_count="single",
                    scale=3,
                )
                process_btn = gr.Button(
                    "🚀 Start Processing",
                    variant="primary",
                    scale=1,
                    size="lg",
                )

            status_md = gr.Markdown(
                value="👆 Upload an MP4 file and click **Start Processing**.",
                visible=True,
            )

            num_speakers_input = gr.Number(
                label="👥 Number of Speakers (optional)",
                value=None,
                precision=0,
                minimum=1,
                maximum=10,
                info="Leave blank for auto-detection. Set to 2, 3, etc. if you know the exact count.",
            )

            template_eaf_input = gr.File(
                label="📋 EAF Template (optional)",
                file_types=[".eaf"],
                file_count="single",
            )

            output_dir_input = gr.Textbox(
                label="📁 Output Directory",
                value=os.path.expanduser("~/Desktop"),
                placeholder="e.g. ~/Desktop or /Users/name/Documents",
                info="EAF and PFSX files will be saved here on export.",
            )

        # Hidden state: stores parsed tier names from the template EAF
        template_tiers_state = gr.State([])

        # ==============================================================
        # STEP 3 — Speaker Mapping Cards
        # ==============================================================
        with gr.Column(visible=False) as step3_col:
            gr.Markdown("## 🗣️ Step 3 — Identify Each Speaker")

            with gr.Column(elem_classes=["instruction-box"]):
                gr.Markdown(
                    "🔊 **Listen** to the voice sample → 📝 **Read** "
                    "the transcription → ✏️ **Name** the speaker:"
                )
                gr.Markdown(
                    "- **Adult** → any tier name (e.g., `Teacher`, `Narrator`, `Mom`)\n"
                    "- **Child** → enter `xxx`\n"
                    "- **Leave blank** → defaults to speaker ID"
                )

            card_cols: list[gr.Column] = []
            audio_components: list[gr.Audio] = []
            samples_boxes: list[gr.Textbox] = []
            name_inputs: list[gr.Dropdown] = []

            for i in range(MAX_SPEAKERS):
                with gr.Column(visible=False, elem_classes=["speaker-card"]) as card:
                    gr.Markdown(
                        f"### 👤 Speaker {i + 1}",
                        elem_classes=["speaker-header"],
                    )
                    with gr.Row():
                        with gr.Column(scale=1):
                            audio = gr.Audio(
                                label="🔊 Voice Sample",
                                type="filepath",
                                interactive=False,
                                show_download_button=False,
                            )
                        with gr.Column(scale=2):
                            samples = gr.Textbox(
                                label="📝 Transcription Samples",
                                interactive=False,
                                lines=3,
                                max_lines=3,
                            )
                            name_input = gr.Dropdown(
                                label="✏️ Speaker Name",
                                choices=[],
                                allow_custom_value=True,
                            )
                card_cols.append(card)
                audio_components.append(audio)
                samples_boxes.append(samples)
                name_inputs.append(name_input)

            advance_btn = gr.Button(
                "✂️ 进入时间轴编辑 →",
                variant="primary",
                size="lg",
            )
            advance_status = gr.Markdown(value="", visible=True)

        # ==============================================================
        # STEP 4 — Timeline Editor
        # ==============================================================
        with gr.Column(visible=False, elem_id="step4-col") as step4_col:
            gr.Markdown("## ✂️ Step 4 — Review & Edit Timeline")
            with gr.Column(elem_classes=["instruction-box"]):
                gr.Markdown(
                    "🎬 **Watch** the video → ✂️ **Drag** segment edges to adjust timing → "
                    "🖊️ **Click** a segment to edit text and tier assignment. "
                    "**Double-click** an empty area to add a new segment."
                )

            video_player = gr.Video(
                label="",
                interactive=True,
                height=240,
                visible=False,
            )
            timeline_html = gr.HTML(
                label="",
                value="<p style='color:#888;text-align:center;padding:40px;'>"
                      "👆 Complete speaker mapping first to see the timeline editor.</p>",
            )
            hidden_json = gr.Textbox(visible=False, elem_id="hidden-json-input")

            with gr.Row():
                generate_btn = gr.Button(
                    "📦 应用修改并生成 EAF",
                    variant="primary",
                    size="lg",
                )
            gen_status = gr.Markdown(value="", visible=True)

        # ==============================================================
        # STEP 5 — Download
        # ==============================================================
        with gr.Column(visible=False) as step5_col:
            gr.Markdown("## 📥 Step 5 — Download Files")
            gr.Markdown(
                "Your annotation files are ready. Click the buttons below "
                "to download them."
            )
            with gr.Row():
                eaf_dl = gr.File(
                    label="📄 EAF File (.eaf)",
                    visible=False,
                    type="filepath",
                    file_count="single",
                    interactive=False,
                )
                pfsx_dl = gr.File(
                    label="📄 PFSX File (.pfsx)",
                    visible=False,
                    type="filepath",
                    file_count="single",
                    interactive=False,
                )

        # ==============================================================
        # Event wiring
        # ==============================================================

        # --- Dynamic outputs (order MUST match yields in process_video) ---
        dynamic_outputs = [
            pipeline_state,         # 0
            step1_col,              # 1
            step3_col,              # 2
            step4_col,              # 3
            step5_col,              # 4
            status_md,              # 5
        ]
        for i in range(MAX_SPEAKERS):
            dynamic_outputs += [
                card_cols[i],       # 6 + i*4
                audio_components[i],# 7 + i*4
                samples_boxes[i],   # 8 + i*4
                name_inputs[i],     # 9 + i*4
            ]
        dynamic_outputs += [
            video_player,           # 46
            timeline_html,          # 47
            hidden_json,            # 48
            eaf_dl,                 # 49
            pfsx_dl,                # 50
            template_tiers_state,   # 51
        ]

        # Process button → run pipeline with gr.Progress
        process_btn.click(
            fn=process_video,
            inputs=[video_input, num_speakers_input, template_eaf_input,
                    pipeline_state, template_tiers_state],
            outputs=dynamic_outputs,
        )

        # Advance button → apply mapping, transition Step 3 → Step 4
        advance_outputs = [
            step3_col, step4_col, video_player, timeline_html, hidden_json, advance_status,
        ]
        advance_btn.click(
            fn=advance_to_timeline,
            inputs=[pipeline_state, template_tiers_state] + name_inputs,
            outputs=advance_outputs,
        )

        # Generate button → create EAF + PFSX, show Step 5
        # The timeline iframe continuously syncs edited data to #hidden-json-input
        # via syncToParent(), so Gradio naturally reads the latest edits from
        # the textarea without needing a JS hook.
        gen_outputs = [step5_col, eaf_dl, pfsx_dl, gen_status]
        generate_btn.click(
            fn=generate_files,
            inputs=[pipeline_state, hidden_json, template_tiers_state, output_dir_input],
            outputs=gen_outputs,
        )

        # --- Cleanup temp files on page unload ---
        def _on_unload():
            cleanup_temp(TEMP_DIR)
            return {}

        app.unload(fn=_on_unload)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.environ.setdefault("no_proxy", "localhost,127.0.0.1")
    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1")

    validate_token()
    cleanup_temp(TEMP_DIR)

    print("=" * 60)
    print("  🎙️  Auto Transcriber & Speaker Diarization")
    print("     Opening http://localhost:7860 ...")
    print("=" * 60)

    ui = build_ui()
    ui.queue(max_size=10).launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        inbrowser=True,
    )
