"""Mermaid parser and render-brief composer for the diagram-generation path.

The Mermaid input gives us deterministic structure; we don't render the
diagram ourselves. We parse the source into a structured ``ParsedDiagram``,
then compose a prompt that asks the image model (Gemini Nano Banana Pro
by default, OpenAI gpt-image-2 by opt-in) to render it with brand colors
and UML conventions.

v1 supports three formats: ``flowchart`` (also accepts ``graph``),
``sequenceDiagram``, and ``stateDiagram`` / ``stateDiagram-v2``. Other
graph types (``classDiagram``, ``erDiagram``, ``gantt``, ``pie``,
``gitGraph``, ``mindmap``, ``timeline``, ``journey``) are rejected — the
caller can use a free-text ``prompt`` as a fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from mcp_bildsprache.presets import (
    CASEY_PALETTE,
    CASEY_REGISTER_OVERLAYS,
)

DiagramFormat = Literal["flow", "sequence", "state"]
DiagramRegister = Literal["personal", "professional"]


class MermaidParseError(ValueError):
    """Raised when Mermaid input is malformed or uses an unsupported graph type.

    Includes ``line`` (1-indexed) when the parser can pin a fault to a
    specific line. Unsupported types set ``line=1`` with a hint pointing
    at the v1 supported set.
    """

    def __init__(self, message: str, line: int | None = None):
        self.line = line
        if line is not None:
            super().__init__(f"line {line}: {message}")
        else:
            super().__init__(message)


@dataclass(frozen=True, slots=True)
class FlowNode:
    id: str
    label: str | None = None  # None when the node is just an id without [].


@dataclass(frozen=True, slots=True)
class FlowEdge:
    source: str
    target: str
    label: str | None = None


@dataclass(frozen=True, slots=True)
class SequenceMessage:
    sender: str
    receiver: str
    arrow: str  # "->>" / "-->>" / "->" / "-->" / "--x" etc.
    label: str


@dataclass(frozen=True, slots=True)
class StateTransition:
    source: str
    target: str
    label: str | None = None


@dataclass(slots=True)
class ParsedDiagram:
    """Structured representation of a Mermaid source."""

    format: DiagramFormat
    direction: str | None = None  # "TD"/"LR"/"BT"/"RL" for flow; None for others
    # flowchart fields
    nodes: list[FlowNode] = field(default_factory=list)
    edges: list[FlowEdge] = field(default_factory=list)
    # sequence fields
    participants: list[str] = field(default_factory=list)
    messages: list[SequenceMessage] = field(default_factory=list)
    # state fields
    states: list[str] = field(default_factory=list)  # incl. "[*]"
    transitions: list[StateTransition] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------

_FLOW_HEADER = re.compile(
    r"^\s*(?:flowchart|graph)(?:\s+([A-Z]{2}))?\s*$",
    re.IGNORECASE,
)
_SEQUENCE_HEADER = re.compile(r"^\s*sequenceDiagram\s*$", re.IGNORECASE)
_STATE_HEADER = re.compile(r"^\s*stateDiagram(?:-v2)?\s*$", re.IGNORECASE)
_REJECTED_HEADERS: dict[str, str] = {
    "classdiagram": "class diagrams",
    "erdiagram": "ER diagrams",
    "gantt": "Gantt charts",
    "pie": "pie charts",
    "gitgraph": "Git graphs",
    "mindmap": "mind maps",
    "timeline": "timelines",
    "journey": "user journeys",
    "quadrantchart": "quadrant charts",
    "requirementdiagram": "requirement diagrams",
}


def _detect_header(source: str) -> tuple[DiagramFormat, str, str | None]:
    """Find the first non-blank, non-comment line and detect the graph type.

    Returns ``(format, header_line, direction_or_none)``. Raises
    ``MermaidParseError`` for unsupported types or empty input.
    """
    for raw_idx, raw in enumerate(source.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        m = _FLOW_HEADER.match(line)
        if m:
            return "flow", line, (m.group(1).upper() if m.group(1) else "TD")
        if _SEQUENCE_HEADER.match(line):
            return "sequence", line, None
        if _STATE_HEADER.match(line):
            return "state", line, None
        # Unsupported but recognised graph types → clear error.
        first_token = re.split(r"\s+", line, maxsplit=1)[0].lower()
        if first_token in _REJECTED_HEADERS:
            raise MermaidParseError(
                f"Mermaid type '{first_token}' ({_REJECTED_HEADERS[first_token]}) "
                "is not supported in v1. Use format='flow' (or 'sequence' or "
                "'state') with a free-text prompt instead.",
                line=raw_idx,
            )
        raise MermaidParseError(
            f"Unrecognised Mermaid header: {line!r}. v1 supports flowchart, "
            "graph, sequenceDiagram, stateDiagram, stateDiagram-v2.",
            line=raw_idx,
        )
    raise MermaidParseError("Empty or comment-only Mermaid source.")


# ---------------------------------------------------------------------------
# Flowchart parser
# ---------------------------------------------------------------------------

# Capture node-shape variants Mermaid supports:
#   [Square]   (Rounded)   ((Circle))   {Diamond}   {{Hexagon}}
#   >Asymmetric]   [/Parallelogram/]   [\Trapezoid\]
# Strategy: embed _FLOW_NODE_SHAPE_NONCAPTURING (no named groups) inside the
# edge regex twice, capturing the whole shape string with named outer
# groups (src_shape / dst_shape). Parse the captured string later with
# _SHAPE_LABEL (which has named groups for label extraction).
_FLOW_NODE_TOKEN = r"[A-Za-z0-9_]+"
_FLOW_NODE_SHAPE_NONCAPTURING = (
    r"(?:"
    r"\[\[[^\]]+\]\]|"
    r"\[/[^\]]+/\]|"
    r"\[\\[^\]]+\\\]|"
    r"\[[^\]]+\]|"
    r"\(\([^)]+\)\)|"
    r"\([^)]+\)|"
    r"\{\{[^}]+\}\}|"
    r"\{[^}]+\}|"
    r">[^\]]+\]"
    r")"
)
_SHAPE_LABEL = re.compile(
    r"^"
    r"(?:"
    r"\[\[(?P<lbl_subroutine>[^\]]+)\]\]|"
    r"\[/(?P<lbl_para>[^\]]+)/\]|"
    r"\[\\(?P<lbl_trap>[^\]]+)\\\]|"
    r"\[(?P<lbl_square>[^\]]+)\]|"
    r"\(\((?P<lbl_circle>[^)]+)\)\)|"
    r"\((?P<lbl_round>[^)]+)\)|"
    r"\{\{(?P<lbl_hex>[^}]+)\}\}|"
    r"\{(?P<lbl_diamond>[^}]+)\}|"
    r">(?P<lbl_async>[^\]]+)\]"
    r")"
    r"$"
)


def _parse_node_shape(text: str | None) -> str | None:
    """Parse a Mermaid shape clause like '[Label]' / '{Label}' / '((Label))' into the label string."""
    if not text:
        return None
    m = _SHAPE_LABEL.match(text)
    if not m:
        return None
    for group in (
        "lbl_subroutine",
        "lbl_para",
        "lbl_trap",
        "lbl_square",
        "lbl_circle",
        "lbl_round",
        "lbl_hex",
        "lbl_diamond",
        "lbl_async",
    ):
        val = m.group(group)
        if val:
            return val.strip()
    return None


_FLOW_EDGE = re.compile(
    r"^\s*"
    r"(?P<src>" + _FLOW_NODE_TOKEN + r")"
    r"(?P<src_shape>" + _FLOW_NODE_SHAPE_NONCAPTURING + r")?"
    r"\s*"
    r"(?P<arrow>--+>|==+>|-\.+->|-->|---|--x|--o)"
    r"\s*"
    r"(?:\|(?P<edge_label>[^|]+)\|)?"
    r"\s*"
    r"(?P<dst>" + _FLOW_NODE_TOKEN + r")"
    r"(?P<dst_shape>" + _FLOW_NODE_SHAPE_NONCAPTURING + r")?"
    r"\s*$"
)
_FLOW_BARE_NODE = re.compile(
    r"^\s*"
    r"(?P<id>" + _FLOW_NODE_TOKEN + r")"
    r"(?P<shape>" + _FLOW_NODE_SHAPE_NONCAPTURING + r")"
    r"\s*$"
)


def _parse_flow(source: str, header_line: str) -> ParsedDiagram:
    parsed = ParsedDiagram(format="flow")
    # Set direction from header.
    m = _FLOW_HEADER.match(header_line)
    parsed.direction = (m.group(1).upper() if m and m.group(1) else "TD")

    seen_nodes: dict[str, str | None] = {}
    saw_header = False

    def _record_node(node_id: str, label: str | None) -> None:
        if node_id not in seen_nodes:
            seen_nodes[node_id] = label
        elif label and not seen_nodes[node_id]:
            seen_nodes[node_id] = label

    for raw_idx, raw in enumerate(source.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if not saw_header:
            if _FLOW_HEADER.match(line):
                saw_header = True
            continue

        edge = _FLOW_EDGE.match(line)
        if edge:
            src = edge.group("src")
            dst = edge.group("dst")
            src_label = _parse_node_shape(edge.group("src_shape"))
            dst_label = _parse_node_shape(edge.group("dst_shape"))
            _record_node(src, src_label)
            _record_node(dst, dst_label)
            parsed.edges.append(
                FlowEdge(
                    source=src,
                    target=dst,
                    label=(edge.group("edge_label") or "").strip() or None,
                )
            )
            continue
        bare = _FLOW_BARE_NODE.match(line)
        if bare:
            _record_node(bare.group("id"), _parse_node_shape(bare.group("shape")))
            continue
        # Tolerate other Mermaid features (subgraph, classDef, etc.) without
        # erroring — we just don't extract them in v1.
        # Strict mode could raise here; we choose tolerance.

    for node_id, label in seen_nodes.items():
        parsed.nodes.append(FlowNode(id=node_id, label=label))

    if not parsed.nodes:
        raise MermaidParseError("Flowchart has no nodes.")
    return parsed


# ---------------------------------------------------------------------------
# Sequence parser
# ---------------------------------------------------------------------------

_SEQ_PARTICIPANT = re.compile(
    r"^\s*(?:participant|actor)\s+(?P<id>[A-Za-z0-9_]+)(?:\s+as\s+(?P<alias>.+))?\s*$",
    re.IGNORECASE,
)
_SEQ_MESSAGE = re.compile(
    r"^\s*"
    r"(?P<sender>[A-Za-z0-9_]+)"
    r"\s*"
    r"(?P<arrow>->>|-->>|->|-->|--x|--\)|-x|-\))"
    r"\s*"
    r"(?P<receiver>[A-Za-z0-9_]+)"
    r"\s*:\s*"
    r"(?P<label>.+?)\s*$"
)
# Activation lines, notes, loops etc. are tolerated but ignored in v1.
_SEQ_TOLERATED = re.compile(
    r"^\s*(?:activate|deactivate|note|loop|alt|else|opt|par|and|end|rect|critical)\b",
    re.IGNORECASE,
)


def _parse_sequence(source: str) -> ParsedDiagram:
    parsed = ParsedDiagram(format="sequence")
    saw_header = False
    seen_participants: list[str] = []

    def _ensure_participant(name: str) -> None:
        if name not in seen_participants:
            seen_participants.append(name)

    for raw_idx, raw in enumerate(source.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if not saw_header:
            if _SEQUENCE_HEADER.match(line):
                saw_header = True
            continue

        p = _SEQ_PARTICIPANT.match(line)
        if p:
            _ensure_participant(p.group("id"))
            continue
        m = _SEQ_MESSAGE.match(line)
        if m:
            sender = m.group("sender")
            receiver = m.group("receiver")
            _ensure_participant(sender)
            _ensure_participant(receiver)
            parsed.messages.append(
                SequenceMessage(
                    sender=sender,
                    receiver=receiver,
                    arrow=m.group("arrow"),
                    label=m.group("label").strip(),
                )
            )
            continue
        if _SEQ_TOLERATED.match(line):
            continue
        # Tolerate anything else without erroring (notes, comments missed
        # by the comment regex, etc.).

    parsed.participants = seen_participants
    if not parsed.participants:
        raise MermaidParseError("Sequence diagram has no participants.")
    if not parsed.messages:
        raise MermaidParseError("Sequence diagram has no messages.")
    return parsed


# ---------------------------------------------------------------------------
# State parser
# ---------------------------------------------------------------------------

_STATE_DIRECTION = re.compile(r"^\s*direction\s+([A-Z]{2})\s*$", re.IGNORECASE)
_STATE_DECLARATION = re.compile(
    r"^\s*state\s+(?P<id>[A-Za-z0-9_]+)(?:\s+as\s+\"?(?P<alias>[^\"]+)\"?)?\s*$",
    re.IGNORECASE,
)
_STATE_TRANSITION = re.compile(
    r"^\s*"
    r"(?P<src>\[\*\]|[A-Za-z0-9_]+)"
    r"\s*-->\s*"
    r"(?P<dst>\[\*\]|[A-Za-z0-9_]+)"
    r"(?:\s*:\s*(?P<label>.+))?\s*$"
)


def _parse_state(source: str) -> ParsedDiagram:
    parsed = ParsedDiagram(format="state")
    saw_header = False
    seen_states: list[str] = []

    def _ensure_state(name: str) -> None:
        if name not in seen_states:
            seen_states.append(name)

    for raw_idx, raw in enumerate(source.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("%%"):
            continue
        if not saw_header:
            if _STATE_HEADER.match(line):
                saw_header = True
            continue
        if _STATE_DIRECTION.match(line):
            continue
        decl = _STATE_DECLARATION.match(line)
        if decl:
            _ensure_state(decl.group("id"))
            continue
        t = _STATE_TRANSITION.match(line)
        if t:
            src = t.group("src")
            dst = t.group("dst")
            _ensure_state(src)
            _ensure_state(dst)
            parsed.transitions.append(
                StateTransition(
                    source=src,
                    target=dst,
                    label=(t.group("label") or "").strip() or None,
                )
            )
            continue

    parsed.states = seen_states
    if not parsed.states:
        raise MermaidParseError("State diagram has no states.")
    if not parsed.transitions:
        raise MermaidParseError("State diagram has no transitions.")
    return parsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_mermaid(source: str) -> ParsedDiagram:
    """Parse a Mermaid source string into a ParsedDiagram.

    Supported headers (case-insensitive): ``flowchart`` (with optional
    direction TD/LR/BT/RL), ``graph`` (alias for flowchart),
    ``sequenceDiagram``, ``stateDiagram``, ``stateDiagram-v2``.

    Raises ``MermaidParseError`` for empty input, unsupported graph types,
    or structurally invalid sources (e.g. a flowchart with no nodes).
    """
    fmt, header_line, _direction = _detect_header(source)
    if fmt == "flow":
        return _parse_flow(source, header_line)
    if fmt == "sequence":
        return _parse_sequence(source)
    return _parse_state(source)


# ---------------------------------------------------------------------------
# Render-brief composer
# ---------------------------------------------------------------------------


_PALETTE_CLAUSE: str = (
    "Brand palette (botanical, locked May 2026): "
    "background paper bone #F4EFE3 (~70% of surface), "
    "primary edges/nodes forest moss #2C4A38, "
    "body/labels pine ink #1F2E26, "
    "accent (≤5%) weathered ochre #B8884A, "
    "hairlines/rules soft moss #C7CFB8. "
    "Typography (when in-image text appears): Vollkorn-style serif, "
    "italic + roman, weights 400–900. NO all-caps anywhere — use weight "
    "or italic for emphasis. "
    "Avoid: chrome, lens flare, neon, gradient mesh, generic AI aesthetic."
)


def _flow_render_brief(parsed: ParsedDiagram) -> str:
    direction_label = {
        "TD": "top-to-bottom",
        "TB": "top-to-bottom",
        "BT": "bottom-to-top",
        "LR": "left-to-right",
        "RL": "right-to-left",
    }.get(parsed.direction or "TD", "top-to-bottom")

    lines: list[str] = []
    lines.append(
        f"Render a flowchart, layout {direction_label}. "
        "Boxes for steps, diamonds for decisions, rounded rectangles for "
        "endpoints. Connectors are clean orthogonal lines with labels "
        "above the connector."
    )
    lines.append("")
    lines.append("Nodes:")
    for n in parsed.nodes:
        label = n.label or n.id
        lines.append(f"  - {n.id}: \"{label}\"")
    lines.append("")
    lines.append("Edges:")
    for e in parsed.edges:
        if e.label:
            lines.append(f"  - {e.source} → {e.target} (labelled: \"{e.label}\")")
        else:
            lines.append(f"  - {e.source} → {e.target}")
    return "\n".join(lines)


def _sequence_render_brief(parsed: ParsedDiagram) -> str:
    lines: list[str] = []
    lines.append(
        "Render a UML sequence diagram. "
        "Vertical lifelines for each participant, equally spaced left to "
        "right in declaration order. Horizontal arrows between lifelines "
        "for messages — solid arrow for synchronous, dashed for response. "
        "Message labels above the arrow. Activation boxes (narrow filled "
        "rectangles) on a lifeline during processing of a received "
        "message. Time flows top-to-bottom."
    )
    lines.append("")
    lines.append("Participants (left to right):")
    for p in parsed.participants:
        lines.append(f"  - {p}")
    lines.append("")
    lines.append("Messages (in order):")
    for m in parsed.messages:
        arrow_kind = "solid" if "-->" not in m.arrow else "dashed"
        lines.append(
            f"  - {m.sender} → {m.receiver} ({arrow_kind}): \"{m.label}\""
        )
    return "\n".join(lines)


def _state_render_brief(parsed: ParsedDiagram) -> str:
    lines: list[str] = []
    lines.append(
        "Render a UML state diagram. "
        "Rounded rectangles for states. Filled circle for the start "
        "marker ([*] as source). Double-circle (target ring) for terminal "
        "states ([*] as target). Arrows between states with transition "
        "labels above or alongside each arrow. Layout flows naturally "
        "(top-to-bottom or left-to-right depending on which reads better)."
    )
    lines.append("")
    lines.append("States:")
    for s in parsed.states:
        if s == "[*]":
            lines.append("  - [*] (start/terminal marker)")
        else:
            lines.append(f"  - {s}")
    lines.append("")
    lines.append("Transitions:")
    for t in parsed.transitions:
        if t.label:
            lines.append(f"  - {t.source} → {t.target} (on: \"{t.label}\")")
        else:
            lines.append(f"  - {t.source} → {t.target}")
    return "\n".join(lines)


def compose_render_brief(
    parsed: ParsedDiagram | None,
    prompt: str | None,
    format: DiagramFormat,
    register: DiagramRegister = "professional",
) -> str:
    """Compose the engineered prompt sent to the image model.

    Exactly one of ``parsed`` or ``prompt`` must be provided. When
    ``parsed`` is set, the render brief is structured from the parsed
    Mermaid; otherwise the free-text prompt is wrapped with format-specific
    convention guidance.

    The brand palette clause is always injected. The register overlay
    tilts the rendering style (personal: warmer, hand-drawn quality;
    professional: crisper, schematic).
    """
    if parsed is None and not prompt:
        raise ValueError("compose_render_brief requires parsed or prompt")
    if parsed is not None and prompt:
        # Both supplied — caller error. Prefer parsed (Mermaid is more
        # deterministic) but warn so we can tighten the API later.
        prompt = None

    if parsed is not None:
        if format == "flow":
            structure = _flow_render_brief(parsed)
        elif format == "sequence":
            structure = _sequence_render_brief(parsed)
        else:
            structure = _state_render_brief(parsed)
    else:
        # Free-text path: still inject the format conventions.
        if format == "flow":
            convention = (
                "Render this as a flowchart. Boxes for steps, diamonds "
                "for decisions, rounded rectangles for endpoints. Clean "
                "orthogonal connectors with labels."
            )
        elif format == "sequence":
            convention = (
                "Render this as a UML sequence diagram. Vertical "
                "lifelines per participant, horizontal arrows for "
                "messages, labels above arrows, activation boxes for "
                "processing time, top-to-bottom time flow."
            )
        else:
            convention = (
                "Render this as a UML state diagram. Rounded rectangles "
                "for states, filled circle for start, double-circle for "
                "terminal, labelled arrows for transitions."
            )
        structure = f"{convention}\n\nDescription:\n{prompt}"

    register_overlay = CASEY_REGISTER_OVERLAYS.get(register, "")
    parts = [
        structure,
        "",
        _PALETTE_CLAUSE,
    ]
    if register_overlay:
        parts.append("")
        parts.append(register_overlay)

    return "\n".join(parts)


# Re-export palette for tests / direct callers.
__all__ = [
    "DiagramFormat",
    "DiagramRegister",
    "FlowEdge",
    "FlowNode",
    "MermaidParseError",
    "ParsedDiagram",
    "SequenceMessage",
    "StateTransition",
    "compose_render_brief",
    "parse_mermaid",
    "CASEY_PALETTE",
]
