"""Tests for the Mermaid parser and render-brief composer."""

from __future__ import annotations

import pytest

from mcp_bildsprache.diagrams import (
    FlowEdge,
    FlowNode,
    MermaidParseError,
    SequenceMessage,
    StateTransition,
    compose_render_brief,
    parse_mermaid,
)


# ---------------------------------------------------------------------------
# parse_mermaid — header detection + rejection
# ---------------------------------------------------------------------------


class TestHeaderDetection:
    def test_empty_source_raises(self):
        with pytest.raises(MermaidParseError, match="Empty"):
            parse_mermaid("")

    def test_comment_only_source_raises(self):
        with pytest.raises(MermaidParseError, match="Empty"):
            parse_mermaid("%% just a comment\n%% nothing else")

    def test_unsupported_class_diagram_rejected(self):
        with pytest.raises(MermaidParseError) as exc_info:
            parse_mermaid("classDiagram\n  Foo --|> Bar")
        assert "class diagrams" in str(exc_info.value)
        assert "v1" in str(exc_info.value).lower()

    def test_unsupported_er_diagram_rejected(self):
        with pytest.raises(MermaidParseError) as exc_info:
            parse_mermaid("erDiagram\n  CUSTOMER ||--o{ ORDER : places")
        assert "ER diagrams" in str(exc_info.value)

    def test_unsupported_gantt_rejected(self):
        with pytest.raises(MermaidParseError):
            parse_mermaid("gantt\n  title Sprint")

    def test_unsupported_pie_rejected(self):
        with pytest.raises(MermaidParseError):
            parse_mermaid('pie title Pets\n  "Dogs" : 386')

    def test_unsupported_mindmap_rejected(self):
        with pytest.raises(MermaidParseError):
            parse_mermaid("mindmap\n  root((Idea))")

    def test_unrecognised_header_rejected(self):
        with pytest.raises(MermaidParseError, match="Unrecognised"):
            parse_mermaid("notARealType\n  foo")


# ---------------------------------------------------------------------------
# Flowchart parsing
# ---------------------------------------------------------------------------


class TestFlowchartParser:
    def test_simple_flowchart(self):
        source = """
        flowchart TD
            A[Start] --> B{Decision}
            B -->|yes| C[End]
            B -->|no| D[Retry]
        """
        parsed = parse_mermaid(source)

        assert parsed.format == "flow"
        assert parsed.direction == "TD"

        node_ids = {n.id for n in parsed.nodes}
        assert node_ids == {"A", "B", "C", "D"}

        labels_by_id = {n.id: n.label for n in parsed.nodes}
        assert labels_by_id["A"] == "Start"
        assert labels_by_id["B"] == "Decision"
        assert labels_by_id["C"] == "End"
        assert labels_by_id["D"] == "Retry"

        assert len(parsed.edges) == 3

        labelled_edges = {(e.source, e.target, e.label) for e in parsed.edges}
        assert ("A", "B", None) in labelled_edges
        assert ("B", "C", "yes") in labelled_edges
        assert ("B", "D", "no") in labelled_edges

    def test_graph_alias_works(self):
        parsed = parse_mermaid("graph LR\n  A[Start] --> B[End]")
        assert parsed.format == "flow"
        assert parsed.direction == "LR"
        assert {n.id for n in parsed.nodes} == {"A", "B"}

    def test_default_direction_td(self):
        parsed = parse_mermaid("flowchart\n  A --> B")
        assert parsed.direction == "TD"

    def test_six_node_decision_tree(self):
        source = """
        flowchart TD
            Start[User opens form] --> Validate{Valid input?}
            Validate -->|yes| Submit[Submit to API]
            Validate -->|no| ShowError[Show error message]
            Submit --> Response{Success?}
            Response -->|yes| Done[Confirmation]
            Response -->|no| ShowError
        """
        parsed = parse_mermaid(source)

        assert len(parsed.nodes) == 6
        assert {n.id for n in parsed.nodes} == {
            "Start",
            "Validate",
            "Submit",
            "ShowError",
            "Response",
            "Done",
        }
        assert len(parsed.edges) == 6

    def test_flowchart_no_nodes_raises(self):
        with pytest.raises(MermaidParseError, match="no nodes"):
            parse_mermaid("flowchart TD\n%% just a comment, no nodes")

    def test_comments_skipped(self):
        source = """
        flowchart TD
            %% This should be ignored
            A --> B
        """
        parsed = parse_mermaid(source)
        assert {n.id for n in parsed.nodes} == {"A", "B"}


# ---------------------------------------------------------------------------
# Sequence parsing
# ---------------------------------------------------------------------------


class TestSequenceParser:
    def test_simple_sequence(self):
        source = """
        sequenceDiagram
            participant Browser
            participant API
            Browser->>API: GET /search
            API-->>Browser: 200 OK
        """
        parsed = parse_mermaid(source)

        assert parsed.format == "sequence"
        assert parsed.participants == ["Browser", "API"]
        assert len(parsed.messages) == 2
        assert parsed.messages[0].sender == "Browser"
        assert parsed.messages[0].receiver == "API"
        assert parsed.messages[0].label == "GET /search"
        assert parsed.messages[1].arrow == "-->>"

    def test_implicit_participant_declaration(self):
        # No `participant` keyword — first appearance defines the participant.
        source = """
        sequenceDiagram
            Browser->>API: hello
            API->>Cache: lookup
            Cache-->>API: hit
            API-->>Browser: result
        """
        parsed = parse_mermaid(source)

        assert parsed.participants == ["Browser", "API", "Cache"]
        assert len(parsed.messages) == 4

    def test_four_participant_async_flow(self):
        source = """
        sequenceDiagram
            participant Client
            participant Gateway
            participant Worker
            participant DB
            Client->>Gateway: POST /jobs
            Gateway->>Worker: enqueue
            Worker->>DB: write
            DB-->>Worker: ack
            Worker-->>Gateway: enqueued
            Gateway-->>Client: 202 Accepted
        """
        parsed = parse_mermaid(source)
        assert parsed.participants == ["Client", "Gateway", "Worker", "DB"]
        assert len(parsed.messages) == 6

    def test_activations_tolerated_not_extracted(self):
        source = """
        sequenceDiagram
            A->>B: hello
            activate B
            B-->>A: response
            deactivate B
        """
        parsed = parse_mermaid(source)
        # Activate/deactivate are skipped; messages preserved.
        assert len(parsed.messages) == 2

    def test_sequence_no_messages_raises(self):
        with pytest.raises(MermaidParseError, match="no messages"):
            parse_mermaid("sequenceDiagram\n  participant Foo")


# ---------------------------------------------------------------------------
# State parsing
# ---------------------------------------------------------------------------


class TestStateParser:
    def test_simple_state_diagram(self):
        source = """
        stateDiagram-v2
            [*] --> Idle
            Idle --> Working : start
            Working --> Done : finish
            Done --> [*]
        """
        parsed = parse_mermaid(source)

        assert parsed.format == "state"
        assert "[*]" in parsed.states
        assert "Idle" in parsed.states
        assert "Working" in parsed.states
        assert "Done" in parsed.states
        assert len(parsed.transitions) == 4

        labels = {(t.source, t.target, t.label) for t in parsed.transitions}
        assert ("[*]", "Idle", None) in labels
        assert ("Idle", "Working", "start") in labels
        assert ("Working", "Done", "finish") in labels
        assert ("Done", "[*]", None) in labels

    def test_state_diagram_v1_keyword(self):
        # Without -v2 suffix.
        parsed = parse_mermaid("stateDiagram\n  [*] --> Active\n  Active --> [*]")
        assert parsed.format == "state"

    def test_five_state_lifecycle(self):
        source = """
        stateDiagram-v2
            [*] --> Created
            Created --> Pending : submit
            Pending --> Approved : approve
            Pending --> Rejected : reject
            Approved --> Closed : finalize
            Rejected --> [*]
            Closed --> [*]
        """
        parsed = parse_mermaid(source)
        # 5 named states + [*] terminal marker.
        named = [s for s in parsed.states if s != "[*]"]
        assert set(named) == {"Created", "Pending", "Approved", "Rejected", "Closed"}
        assert len(parsed.transitions) == 7

    def test_state_no_transitions_raises(self):
        with pytest.raises(MermaidParseError, match="no transitions"):
            parse_mermaid("stateDiagram-v2\n  state A")


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_parse_error_includes_line_number(self):
        try:
            parse_mermaid("erDiagram\n  CUSTOMER")
        except MermaidParseError as e:
            assert e.line == 1
            assert "line 1:" in str(e)


# ---------------------------------------------------------------------------
# compose_render_brief
# ---------------------------------------------------------------------------


class TestComposeRenderBrief:
    def test_palette_clause_always_present(self):
        # Use a parsed flow to avoid prompt/parsed validation.
        from mcp_bildsprache.diagrams import ParsedDiagram

        parsed = ParsedDiagram(
            format="flow",
            nodes=[FlowNode(id="A", label="Start")],
            edges=[],
            direction="TD",
        )
        brief = compose_render_brief(
            parsed=parsed, prompt=None, format="flow", register="professional"
        )
        # Palette tokens.
        assert "#F4EFE3" in brief
        assert "#2C4A38" in brief
        assert "#1F2E26" in brief
        assert "#B8884A" in brief
        assert "#C7CFB8" in brief
        # Vollkorn typography.
        assert "Vollkorn" in brief
        # No-all-caps rule.
        assert "all-caps" in brief.lower()

    def test_register_personal_overlay(self):
        brief = compose_render_brief(
            parsed=None, prompt="A simple workflow", format="flow", register="personal"
        )
        assert "Register: personal" in brief or "personal" in brief.lower()
        assert "kitchen-table" in brief.lower() or "warmer" in brief.lower()

    def test_register_professional_overlay(self):
        brief = compose_render_brief(
            parsed=None, prompt="A simple workflow", format="flow", register="professional"
        )
        assert "Register: professional" in brief or "professional" in brief.lower()
        assert "schematic" in brief.lower() or "workshop" in brief.lower()

    def test_flow_convention_in_freetext_brief(self):
        brief = compose_render_brief(
            parsed=None, prompt="user submits a form", format="flow", register="professional"
        )
        assert "flowchart" in brief.lower()
        assert "diamond" in brief.lower()  # decision shape
        assert "user submits a form" in brief

    def test_sequence_convention_in_freetext_brief(self):
        brief = compose_render_brief(
            parsed=None, prompt="API call flow", format="sequence", register="professional"
        )
        assert "sequence" in brief.lower()
        assert "lifeline" in brief.lower()
        assert "horizontal arrows" in brief.lower()
        assert "activation" in brief.lower()

    def test_state_convention_in_freetext_brief(self):
        brief = compose_render_brief(
            parsed=None, prompt="order lifecycle", format="state", register="professional"
        )
        assert "state diagram" in brief.lower()
        assert "rounded" in brief.lower()
        assert "double-circle" in brief.lower() or "filled circle" in brief.lower()

    def test_parsed_flow_structure_in_brief(self):
        from mcp_bildsprache.diagrams import ParsedDiagram

        parsed = ParsedDiagram(
            format="flow",
            direction="TD",
            nodes=[FlowNode(id="A", label="Start"), FlowNode(id="B", label="End")],
            edges=[FlowEdge(source="A", target="B", label="next")],
        )
        brief = compose_render_brief(
            parsed=parsed, prompt=None, format="flow", register="professional"
        )
        assert "Start" in brief
        assert "End" in brief
        assert "A → B" in brief
        assert "next" in brief

    def test_parsed_sequence_structure_in_brief(self):
        from mcp_bildsprache.diagrams import ParsedDiagram

        parsed = ParsedDiagram(
            format="sequence",
            participants=["Browser", "API"],
            messages=[
                SequenceMessage(
                    sender="Browser",
                    receiver="API",
                    arrow="->>",
                    label="GET /search",
                )
            ],
        )
        brief = compose_render_brief(
            parsed=parsed, prompt=None, format="sequence", register="professional"
        )
        assert "Browser" in brief
        assert "API" in brief
        assert "GET /search" in brief
        assert "(solid)" in brief

    def test_parsed_state_structure_in_brief(self):
        from mcp_bildsprache.diagrams import ParsedDiagram

        parsed = ParsedDiagram(
            format="state",
            states=["[*]", "Idle", "Active"],
            transitions=[
                StateTransition(source="[*]", target="Idle", label=None),
                StateTransition(source="Idle", target="Active", label="start"),
            ],
        )
        brief = compose_render_brief(
            parsed=parsed, prompt=None, format="state", register="professional"
        )
        assert "[*]" in brief
        assert "Idle" in brief
        assert "Active" in brief
        assert "start" in brief

    def test_compose_requires_prompt_or_parsed(self):
        with pytest.raises(ValueError):
            compose_render_brief(
                parsed=None, prompt=None, format="flow", register="professional"
            )

    def test_both_inputs_prefers_parsed(self):
        from mcp_bildsprache.diagrams import ParsedDiagram

        parsed = ParsedDiagram(
            format="flow",
            direction="TD",
            nodes=[FlowNode(id="X", label="Parsed")],
            edges=[],
        )
        brief = compose_render_brief(
            parsed=parsed,
            prompt="this should be ignored",
            format="flow",
            register="professional",
        )
        assert "Parsed" in brief
        assert "this should be ignored" not in brief
