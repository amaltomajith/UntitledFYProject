"""
puppeteer_code.assistant
========================
The conversational coding assistant: talk to it, and it plans, writes, runs and
tests code in your project. The terminal chat and the browser IDE are two front
ends on `AssistantSession`.

It works in the user's folder, unlike `pcode fix`, which works in a sandbox. Every
request is protected by a snapshot outside the project, so Undo always works.
"""

from puppeteer_code.assistant.session import AssistantSession

__all__ = ["AssistantSession"]
