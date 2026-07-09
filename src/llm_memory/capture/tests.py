"""
Test Capture Module

Captures test results and failures as memories.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from llm_memory.capture.git import CaptureManifest, capture_content_hash


class TestCapture:
    __test__ = False

    """
    Capture memories from test execution.

    Usage:
        capture = TestCapture(memory)
        capture.on_pytest_session("report.xml")
    """

    def __init__(self, memory):
        self.memory = memory

    def on_pytest_session(self, report_path: str = "report.xml") -> List[str]:
        """
        Parse pytest JUnit XML report and record significant events.

        Args:
            report_path: Path to JUnit XML report

        Returns:
            List of memory IDs created
        """
        path = Path(report_path)
        if not path.exists():
            raise FileNotFoundError(f"Report not found: {report_path}")

        content_hash = capture_content_hash(path.read_text())
        manifest = CaptureManifest(self.memory)
        status, _entry = manifest.check("test_report", str(path), content_hash)
        if status == "unchanged":
            self.last_manifest_report = CaptureManifest.status_report(["unchanged"])
            return []

        try:
            tree = ET.parse(path)
            root = tree.getroot()
        except ET.ParseError as e:
            raise ValueError(f"Invalid XML report: {e}")

        memory_ids = []

        # Parse suites if multiple, or root as suite
        suites = root.findall(".//testsuite")
        if not suites:
            suites = [root]

        for suite in suites:
            # Check for failures
            failures = int(suite.get("failures", 0))
            errors = int(suite.get("errors", 0))

            if failures > 0 or errors > 0:
                # Record specific failures
                for case in suite.findall("testcase"):
                    failure = case.find("failure")
                    error = case.find("error")

                    if failure is not None or error is not None:
                        elem = failure if failure is not None else error
                        msg = elem.get("message", "Test failed")
                        details = elem.text
                        name = case.get("name", "Unknown test")
                        file = case.get("file", "Unknown file")

                        # Check if this is a known flaky test or recurring issue
                        # (Future enhancement: check deduplication here)

                        mem_id = self.memory.record(
                            event=f"Test Failed: {name} - {msg}",
                            category="bug_found",
                            importance=0.8,
                            context={
                                "file": file,
                                "test_name": name,
                                "error": msg,
                                "details": details[:500]
                                if details
                                else None,  # Truncate stack trace
                            },
                            tags=["test", "failure", "auto-captured"],
                        )
                        memory_ids.append(mem_id)

                        # Also potentially warn if it seems fragile
                        # self.memory.warn(file, f"Test {name} is failing: {msg}")

        manifest.record("test_report", str(path), content_hash, memory_ids, status=status)
        self.last_manifest_report = CaptureManifest.status_report([status])
        return memory_ids
