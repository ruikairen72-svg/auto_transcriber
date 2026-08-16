"""
Export aligned & labelled segments to ELAN (.eaf) and PFSX formats.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LINGUISTIC_TYPE_ID = "transcription"
PFSX_NAMESPACE = "http://www.mpi.nl/tools/elan/Prefs_v1.1.xsd"

# ---------------------------------------------------------------------------
# EAF generation via pympi-ling
# ---------------------------------------------------------------------------

def generate_eaf(
    aligned_segments: list[dict],
    speaker_names: dict[str, str],
    audio_path: str,
    output_path: str,
    template_eaf_path: str | None = None,
    tiers: list[str] | None = None,
    hierarchy: dict[str, str | None] | None = None,
) -> str:
    """
    Create an ELAN .eaf file with one tier per speaker.

    When *template_eaf_path* is provided the template's tier structure
    (linguistic types, tier order, tier properties) is preserved —
    annotations are written only to tiers that match a mapped speaker;
    unmapped template tiers are left empty.  Any speaker mapped to a name
    that does **not** appear in the template gets an additional tier.

    Parameters
    ----------
    aligned_segments : list[dict]
        ``[{start, end, text, speaker}, ...]``
    speaker_names : dict[str, str]
        Mapping from speaker id (e.g. ``"SPEAKER_00"``) to the user-supplied
        tier name.  Missing keys fall back to the speaker id.
    audio_path : str
        Path to the extracted WAV file, linked as media in the EAF.
    output_path : str
        Where to write the .eaf file.
    template_eaf_path : str or None
        Optional path to a template EAF whose tier structure is preserved.
    tiers : list[str] or None
        Optional ordered tier names (used as fallback when template_eaf_path
        is unavailable, e.g. after temp cleanup).
    hierarchy : dict[str, str | None] or None
        Optional tier parent→child hierarchy (PARENT_REF), used as fallback
        when template_eaf_path is unavailable.

    Returns
    -------
    str
        The *output_path* for convenience.
    """
    from pympi import Elan

    eaf = Elan.Eaf(author="auto_transcriber_web")

    if template_eaf_path and os.path.exists(template_eaf_path):
        # ---- Template mode: copy structure from the template EAF ----
        template = Elan.Eaf(template_eaf_path)

        # Copy linguistic types
        for lt_id in template.linguistic_types:
            lt_info = template.linguistic_types.get(lt_id, {})
            time_alignable = (
                str(lt_info.get("TIME_ALIGNABLE", "true")).lower() == "true"
            )
            eaf.add_linguistic_type(lt_id, timealignable=time_alignable)

        template_tier_ids: set[str] = set()
        template_participants: dict[str, str] = {}  # tier_id → participant

        # Copy every template tier (preserving tier id, linguistic type, and
        # participant name).  Segments are written only for speakers whose
        # mapped name matches the tier id or participant.
        for tier_id in template.tiers:
            template_tier_ids.add(tier_id)
            try:
                props = template.get_parameters_for_tier(tier_id)
            except Exception:
                props = {}
            ling_type = props.get("LINGUISTIC_TYPE_ID", LINGUISTIC_TYPE_ID)
            participant = props.get("PARTICIPANT", tier_id)
            template_participants[tier_id] = participant

            eaf.add_tier(tier_id, ling=ling_type, part=participant)

            # Write annotations for every speaker mapped to this tier
            for spk_id, spk_name in speaker_names.items():
                if spk_name == tier_id or spk_name == participant:
                    for seg in aligned_segments:
                        if seg["speaker"] != spk_id:
                            continue
                        ts1 = int(seg["start"] * 1000)
                        ts2 = int(seg["end"] * 1000)
                        if ts2 <= ts1:
                            ts2 = ts1 + 1
                        eaf.add_annotation(tier_id, ts1, ts2, seg["text"])

        # Any speaker mapped to a name not in the template → extra tier
        used_tier_ids = set(template_tier_ids)
        all_template_names = set(template_tier_ids) | set(template_participants.values())
        for spk_id, spk_name in speaker_names.items():
            if spk_name not in all_template_names:
                tier_id = _unique_tier_id(spk_name, used_tier_ids)
                used_tier_ids.add(tier_id)
                eaf.add_tier(tier_id, ling=LINGUISTIC_TYPE_ID, part=spk_name)
                for seg in aligned_segments:
                    if seg["speaker"] != spk_id:
                        continue
                    ts1 = int(seg["start"] * 1000)
                    ts2 = int(seg["end"] * 1000)
                    if ts2 <= ts1:
                        ts2 = ts1 + 1
                    eaf.add_annotation(tier_id, ts1, ts2, seg["text"])

    else:
        # ---- Original behaviour: one tier per speaker ----
        eaf.add_linguistic_type(LINGUISTIC_TYPE_ID, timealignable=True)

        # Collect unique speakers sorted by first appearance
        seen: list[str] = []
        for seg in aligned_segments:
            spk = seg["speaker"]
            if spk not in seen:
                seen.append(spk)

        # Track used tier IDs to handle duplicates
        used_tier_ids: set[str] = set()

        for spk in seen:
            desired_name = speaker_names.get(spk, spk)
            tier_id = _unique_tier_id(desired_name, used_tier_ids)
            used_tier_ids.add(tier_id)

            eaf.add_tier(tier_id, ling=LINGUISTIC_TYPE_ID, part=desired_name)

            for seg in aligned_segments:
                if seg["speaker"] != spk:
                    continue
                ts1 = int(seg["start"] * 1000)
                ts2 = int(seg["end"] * 1000)
                # Guard: ensure strictly positive duration
                if ts2 <= ts1:
                    ts2 = ts1 + 1
                eaf.add_annotation(tier_id, ts1, ts2, seg["text"])

    # Link the audio file as media
    audio_relpath = os.path.basename(str(audio_path))
    eaf.add_linked_file(
        file_path=str(audio_path),
        relpath=audio_relpath,
        mimetype="audio/x-wav",
    )

    eaf.to_file(output_path)
    return output_path


def _unique_tier_id(desired: str, used: set[str]) -> str:
    """Return *desired* if unused, otherwise append ``_1``, ``_2``, ..."""
    if desired not in used:
        return desired
    counter = 1
    while True:
        candidate = f"{desired}_{counter}"
        if candidate not in used:
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# PFSX generation (ELAN preference file)
# ---------------------------------------------------------------------------

def generate_pfsx(
    speaker_names: dict[str, str],
    output_path: str,
    template_tiers: list[str] | None = None,
) -> str:
    """
    Generate an ELAN .pfsx preference file controlling tier order and visibility.

    When *template_tiers* is provided those names are used for tier order
    (preserving the template's original ordering); only template tiers that
    are actually mapped to a speaker are included.

    Parameters
    ----------
    speaker_names : dict[str, str]
        Mapping from speaker id to user-provided tier name.
    output_path : str
        Where to write the .pfsx file.
    template_tiers : list[str] or None
        Optional ordered list of tier names from a template EAF.

    Returns
    -------
    str
        The *output_path*.
    """
    ET.register_namespace("", PFSX_NAMESPACE)

    root = ET.Element(f"{{{PFSX_NAMESPACE}}}preferences", version="1.1")

    if template_tiers:
        # Preserve template tier order; only include mapped tiers
        mapped = set(speaker_names.values())
        tier_names = [t for t in template_tiers if t in mapped]
        # Also include any custom names typed by user that aren't in the template
        for name in sorted(set(speaker_names.values()), key=str.lower):
            if name not in tier_names:
                tier_names.append(name)
    else:
        tier_names = sorted(set(speaker_names.values()), key=str.lower)

    # Tier order
    tier_order = ET.SubElement(root, f"{{{PFSX_NAMESPACE}}}tier-order")
    for name in tier_names:
        ET.SubElement(tier_order, f"{{{PFSX_NAMESPACE}}}tier", id=name)

    # Visible tiers
    visible_tiers = ET.SubElement(root, f"{{{PFSX_NAMESPACE}}}visible-tiers")
    for name in tier_names:
        ET.SubElement(
            visible_tiers,
            f"{{{PFSX_NAMESPACE}}}tier",
            id=name,
            visible="true",
        )

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="UTF-8", xml_declaration=True)
    return output_path
