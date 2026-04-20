"""
Task Splitter for AutoPattern.

Splits a task description string into a list of self-contained subtask
strings using pure string heuristics (no LLM calls).

Usage:
    from .task_splitter import split_task

    subtasks = split_task("1. Go to google.com\\n2. Search for Python\\n3. Click first result")
    # → ["Go to google.com", "Search for Python", "Click first result"]
"""

import re


# Action verbs that typically start a new browser instruction
_ACTION_VERBS = re.compile(
    r"^(go\s+to|navigate\s+to|open|click|tap|press|search|type|enter|fill|"
    r"submit|scroll|select|wait|check|uncheck|toggle|hover|drag|close|switch|log\s*in|sign\s*in|sign\s*up)\b",
    re.IGNORECASE,
)

# Numbered step pattern: "1." or "1)" or "Step 1:" at start of line
_NUMBERED_STEP = re.compile(
    r"^\s*(?:step\s*)?\d+[\.\)\:\-]\s*",
    re.IGNORECASE,
)

# Bullet patterns: "- " or "• " or "* "
_BULLET = re.compile(r"^\s*[\-\u2022\*]\s+")

# Natural-language connectors that signal a new subtask
_CONNECTORS = re.compile(
    r"\b(?:and\s+then|after\s+that|once\s+done\s*,|finally\s*,|next\s*,)\b|(?<=\S)\s+then\s+(?=\S)",
    re.IGNORECASE,
)


def split_task(task_description: str) -> list[str]:
    """Split a task description into a list of self-contained subtask strings.

    Splitting priorities:
    1. Numbered steps  (``1. Do X  2. Do Y``)
    2. Bullet points   (``- Do X  - Do Y``)
    3. Action-verb sentences on separate lines
    4. If nothing clear is found, return the whole string as-is

    Returns:
        A list of non-empty subtask strings. Always contains at least one
        element — never returns an empty list.
    """
    if not task_description or not task_description.strip():
        return [task_description or ""]

    text = task_description.strip()

    # ------------------------------------------------------------------
    # Strategy 1: Numbered steps
    # ------------------------------------------------------------------
    numbered = _split_numbered(text)
    if len(numbered) > 1:
        return numbered

    # ------------------------------------------------------------------
    # Strategy 2: Bullet points
    # ------------------------------------------------------------------
    bulleted = _split_bullets(text)
    if len(bulleted) > 1:
        return bulleted

    # ------------------------------------------------------------------
    # Strategy 3: Natural-language connectors ("and then", "after that")
    # ------------------------------------------------------------------
    by_connectors = _split_connectors(text)
    if len(by_connectors) > 1:
        return by_connectors

    # ------------------------------------------------------------------
    # Strategy 4: Lines starting with action verbs
    # ------------------------------------------------------------------
    by_actions = _split_action_lines(text)
    if len(by_actions) > 1:
        return by_actions

    # ------------------------------------------------------------------
    # Strategy 5: No clear split — return as single task
    # ------------------------------------------------------------------
    return [text]


def _split_numbered(text: str) -> list[str]:
    """Split on numbered step patterns."""
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    preamble: list[str] = []
    found_numbered = False

    for line in lines:
        if _NUMBERED_STEP.match(line):
            found_numbered = True
            # Flush current chunk
            if current:
                chunks.append("\n".join(current).strip())
                current = []
            # Strip the number prefix to make the subtask self-contained
            cleaned = _NUMBERED_STEP.sub("", line).strip()
            if cleaned:
                current.append(cleaned)
        elif found_numbered and current:
            # Continuation line of current numbered step
            current.append(line)
        else:
            # Lines before any numbered step (preamble)
            preamble.append(line)

    # Flush last chunk
    if current:
        chunks.append("\n".join(current).strip())

    if not chunks:
        return [text]

    # Prepend preamble context to first subtask if it exists
    preamble_text = "\n".join(preamble).strip()
    if preamble_text and chunks:
        chunks[0] = preamble_text + "\n" + chunks[0]

    return [c for c in chunks if c]


def _split_bullets(text: str) -> list[str]:
    """Split on bullet-point patterns."""
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    preamble: list[str] = []
    found_bullet = False

    for line in lines:
        if _BULLET.match(line):
            found_bullet = True
            if current:
                chunks.append("\n".join(current).strip())
                current = []
            cleaned = _BULLET.sub("", line).strip()
            if cleaned:
                current.append(cleaned)
        elif found_bullet and current:
            current.append(line)
        else:
            preamble.append(line)

    if current:
        chunks.append("\n".join(current).strip())

    if not chunks:
        return [text]

    preamble_text = "\n".join(preamble).strip()
    if preamble_text and chunks:
        chunks[0] = preamble_text + "\n" + chunks[0]

    return [c for c in chunks if c]


def _split_connectors(text: str) -> list[str]:
    """Split on natural-language connectors like 'and then', 'after that', etc."""
    parts = _CONNECTORS.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _split_action_lines(text: str) -> list[str]:
    """Split on lines that start with action verbs."""
    lines = text.splitlines()
    chunks: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _ACTION_VERBS.match(stripped) and current:
            # New action verb line — flush previous chunk
            chunks.append("\n".join(current).strip())
            current = [stripped]
        else:
            current.append(stripped)

    if current:
        chunks.append("\n".join(current).strip())

    return [c for c in chunks if c]
