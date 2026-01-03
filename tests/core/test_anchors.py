"""Tests for structural code anchoring and staleness detection."""

from visp_memory.core.anchors import (
    AnchorIndex,
    AnchorState,
    anchors_of,
    extract_anchors,
    inspect,
)
from visp_memory.core.injection import InjectionPolicy, select_for_injection


class TestExtraction:
    def test_finds_bracketed_warning_paths(self):
        anchors = extract_anchors("WARNING [src/auth/session.py]: cache writes race")
        assert anchors == ("src/auth/session.py",)

    def test_finds_capture_summary_paths(self):
        anchors = extract_anchors(
            "Commit: add recall | Modified: src/core/memory.py, src/core/ranking.py"
        )
        assert anchors == ("src/core/memory.py", "src/core/ranking.py")

    def test_finds_bare_source_filenames(self):
        assert extract_anchors("the bug is in tokens.py somewhere") == ("tokens.py",)

    def test_ignores_prose_that_is_not_a_path(self):
        """Ordinary sentences must not produce anchors, or everything looks anchored."""
        assert extract_anchors("We decided to use JWT because it scales. See notes.") == ()

    def test_ignores_vendored_directories(self):
        assert extract_anchors("node_modules/react/index.js changed") == ()
        assert extract_anchors(".venv/lib/site-packages/foo.py") == ()

    def test_deduplicates_preserving_order(self):
        anchors = extract_anchors("a/one.py then b/two.py then a/one.py again")
        assert anchors == ("a/one.py", "b/two.py")

    def test_tags_take_precedence_over_content(self):
        memory = {"content": "mentions other.py", "tags": ["anchor:src/real.py"]}
        assert anchors_of(memory) == ("src/real.py",)


class TestStaleness:
    def test_present_when_file_exists(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "live.py").write_text("x = 1")

        report = inspect({"content": "WARNING [src/live.py]: careful"}, tmp_path)

        assert report.state is AnchorState.PRESENT
        assert not report.fully_stale

    def test_missing_when_file_is_gone(self, tmp_path):
        report = inspect({"content": "WARNING [src/deleted.py]: careful"}, tmp_path)

        assert report.state is AnchorState.MISSING
        assert report.fully_stale
        assert report.missing == ("src/deleted.py",)

    def test_partially_stale_memory_is_still_live(self, tmp_path):
        """One deleted file out of three still leaves a memory about live code."""
        (tmp_path / "kept.py").write_text("x = 1")
        report = inspect({"content": "Modified: kept.py, gone.py"}, tmp_path)

        assert not report.fully_stale
        assert report.present == ("kept.py",)
        assert report.missing == ("gone.py",)

    def test_moved_file_resolves_by_name(self, tmp_path):
        """A file relocated within the repo should not mark its memories stale."""
        (tmp_path / "newdir").mkdir()
        (tmp_path / "newdir" / "moved.py").write_text("x = 1")

        report = inspect({"content": "WARNING [olddir/moved.py]: careful"}, tmp_path)
        assert not report.fully_stale

    def test_unverified_without_a_repo_root(self):
        report = inspect({"content": "WARNING [src/x.py]: careful"})
        assert report.state is AnchorState.UNVERIFIED
        assert not report.fully_stale

    def test_unanchored_memory_is_never_stale(self, tmp_path):
        report = inspect({"content": "We use JWT because it scales"}, tmp_path)
        assert not report.anchored
        assert not report.fully_stale

    def test_path_traversal_is_not_followed(self, tmp_path):
        report = inspect({"content": "see ../../etc/passwd.py"}, tmp_path)
        assert report.fully_stale


class TestAnchorIndex:
    def test_matches_on_path_suffix(self):
        index = AnchorIndex.build(
            [
                {"id": "a", "content": "WARNING [src/auth/session.py]: race"},
                {"id": "b", "content": "unrelated prose"},
            ]
        )
        assert index.for_files(["session.py"]) == {"a"}
        assert index.for_files(["src/auth/session.py"]) == {"a"}
        assert index.for_files(["other.py"]) == set()


class TestInjectionIntegration:
    def test_anchored_memory_bypasses_the_relevance_floor(self, tmp_path):
        """A warning naming the file you are editing is relevant for a structural
        reason that lexical scoring cannot see."""
        (tmp_path / "session.py").write_text("x = 1")
        candidates = [
            {
                "id": "anchored",
                "content": "WARNING [session.py]: cache writes race under refresh",
                "relevance_score": 0.20,
                "category": "fact",
                "tags": [],
            },
        ]
        result = select_for_injection(
            candidates,
            task="update the refresh handling logic",
            files=["session.py"],
            corpus_size=50,
            repo_root=tmp_path,
        )
        assert [m["id"] for m in result.memories] == ["anchored"]
        assert result.anchored_hits == 1

    def test_memory_about_deleted_code_is_withheld(self, tmp_path):
        # A resolvable anchor elsewhere in the pool is what makes a MISSING verdict
        # believable; see the self-calibration guard in select_for_injection.
        (tmp_path / "live.py").write_text("x = 1")
        candidates = [
            {
                "id": "stale",
                "content": "WARNING [removed_module.py]: careful with the global lock",
                "relevance_score": 0.99,
                "category": "fragile_area",
                "tags": [],
            },
            {
                "id": "live",
                "content": "WARNING [live.py]: unrelated but resolvable",
                "relevance_score": 0.10,
                "category": "fragile_area",
                "tags": [],
            },
        ]
        result = select_for_injection(
            candidates,
            task="review the locking strategy",
            files=["removed_module.py"],
            corpus_size=50,
            repo_root=tmp_path,
        )
        assert result.dropped_stale_anchor == 1
        assert "stale" not in [m["id"] for m in result.memories]

    def test_staleness_is_skipped_when_nothing_resolves(self, tmp_path):
        """Running from the wrong directory must not silently withhold everything."""
        candidates = [
            {
                "id": "a",
                "content": "WARNING [src/auth.py]: mutex required around refresh",
                "relevance_score": 0.99,
                "category": "fragile_area",
                "tags": [],
            },
        ]
        result = select_for_injection(
            candidates,
            task="review the refresh locking",
            files=["src/auth.py"],
            corpus_size=50,
            repo_root=tmp_path,
        )
        assert not result.abstained
        assert result.dropped_stale_anchor == 0

    def test_staleness_check_can_be_disabled(self, tmp_path):
        candidates = [
            {
                "id": "stale",
                "content": "WARNING [removed_module.py]: careful",
                "relevance_score": 0.99,
                "category": "fragile_area",
                "tags": [],
            },
        ]
        result = select_for_injection(
            candidates,
            task="review the locking strategy",
            files=["removed_module.py"],
            corpus_size=50,
            repo_root=tmp_path,
            policy=InjectionPolicy(drop_stale_anchors=False),
        )
        assert not result.abstained
