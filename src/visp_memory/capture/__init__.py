"""
Automatic Capture System

Captures memories automatically from development activity:
- Git commits, merges, and branch activity
- Test results and failures
- Conversation transcripts
"""

from visp_memory.capture.git import GitCapture

__all__ = ["GitCapture"]
