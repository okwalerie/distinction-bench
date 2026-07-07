"""Extraction tests against small, checked-in real eval logs.

Fixtures are trimmed copies of the two smallest real logs in ``logs/`` as of
2026-07-04 (``logs/`` itself is local working data, excluded from git via
``.git/info/exclude``, so it cannot be a test fixture directly):

- ``single_small.eval``: the smallest single-task log (2 samples, canonical
  renderer, claude-sonnet-4).
- ``composite_small_items_schema.eval``: the smallest composite-task log
  using the older ``{"items": [...], "total_marked": ...}`` scorer-answer
  schema (10 groups x group_size 8, canonical renderer, gpt-5.2).
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai.log import read_eval_log

from lofbench.pipeline import (
    extract_calls,
    extract_items,
    extract_transcripts,
    reasoning_setting_of,
    resolve_provenance,
    run_pipeline,
    stable_form_id,
)

FIXTURES = Path(__file__).parent / "fixtures" / "logs"


class TestSingleTaskExtraction:
    def setup_method(self):
        self.log = read_eval_log(str(FIXTURES / "single_small.eval"))

    def test_one_call_and_one_item_per_sample(self):
        calls = extract_calls(self.log)
        items = extract_items(self.log)
        assert len(calls) == 2
        assert len(items) == 2
        assert all(i.item_index == 0 for i in items)

    def test_correctness_matches_scorer(self):
        items = extract_items(self.log)
        # Both samples in this fixture score "C" (correct) per the scorer.
        assert all(i.correct for i in items)
        assert {i.target for i in items} == {"marked", "unmarked"}

    def test_form_string_and_form_id_present(self):
        items = extract_items(self.log)
        for item in items:
            assert item.form_string  # original_form, never empty for single-task
            assert item.form_id == stable_form_id(item.form_string)

    def test_dialect_falls_back_to_canonical(self):
        items = extract_items(self.log)
        assert all(i.dialect_id == "canonical" for i in items)
        assert all(i.family == "parens" for i in items)
        assert all(i.modality == "text" and i.format == "text" for i in items)

    def test_reasoning_setting_none_when_no_reasoning_configured(self):
        assert reasoning_setting_of(self.log) is None

    def test_cost_populated_for_known_model(self):
        calls = extract_calls(self.log)
        assert all(c.cost_usd is not None and c.cost_usd >= 0 for c in calls)

    def test_transcripts_one_per_item(self):
        transcripts = extract_transcripts(self.log)
        assert len(transcripts) == 2
        assert all(t.is_image is False for t in transcripts)
        assert all(t.rendered_input for t in transcripts)


class TestCompositeTaskExtraction:
    def setup_method(self):
        self.log = read_eval_log(str(FIXTURES / "composite_small_items_schema.eval"))

    def test_call_count_matches_sample_epoch_pairs(self):
        calls = extract_calls(self.log)
        assert len(calls) == len(self.log.samples)
        assert len(calls) == 10

    def test_items_explode_by_group_size(self):
        items = extract_items(self.log)
        # 10 groups * group_size 8 = 80 exploded item rows, none dropped.
        assert len(items) == 80

    def test_old_items_total_marked_schema_parses_cleanly(self):
        """This fixture predates the current lof_composite_scorer and uses
        the {"items": [...], "total_marked": ...} answer shape. It must
        parse without falling back to "unknown" for every item."""
        items = extract_items(self.log)
        assert not all(i.predicted == "unknown" for i in items)

    def test_first_group_correctness_matches_hand_count(self):
        # comp_001: targets vs items answer, hand-verified against the raw
        # log (see task notes): 6 correct out of 8.
        items = extract_items(self.log)
        comp_001_items = [i for i in items if i.call_id.endswith(":comp_001:1")]
        assert len(comp_001_items) == 8
        assert sum(i.correct for i in comp_001_items) == 6

    def test_depth_and_steps_are_none_for_composite(self):
        items = extract_items(self.log)
        assert all(i.depth is None and i.steps is None for i in items)

    def test_group_size_on_calls(self):
        calls = extract_calls(self.log)
        assert all(c.group_size == 8 for c in calls)

    def test_transcripts_explode_by_group_size(self):
        transcripts = extract_transcripts(self.log)
        assert len(transcripts) == 80


class TestUnscoredCompositeSamples:
    """An errored/unscored composite sample (no score object at all, as in the
    real errored gemini-3.0-pro log) is not schema drift: its items must
    still be emitted (predicted='unknown', scoring incorrect -- counted,
    never dropped) and it must NOT be recorded as an answer-schema parse
    failure. It is instead counted via calls_with_no_scored_items."""

    def _fake_composite_log(self):
        from types import SimpleNamespace

        sample = SimpleNamespace(
            id="comp_001",
            epoch=1,
            scores=None,  # errored before scoring
            target="marked,unmarked",
            output=None,
            metadata={
                "targets": ["marked", "unmarked"],
                "original_expressions": ["()", "(())"],
                "difficulty": "1. easy",
                "group_size": 2,
                # note: no render_metadata key at all, like real composite samples
            },
        )
        eval_ns = SimpleNamespace(
            task="composite_lof_task",
            task_id="FAKE123",
            model="anthropic/claude-sonnet-4-20250514",
            task_args={"renderer": "canonical"},
            model_generate_config=None,
            dataset=None,
            config=None,
        )
        return SimpleNamespace(eval=eval_ns, samples=[sample], location="fake.eval")

    def test_items_emitted_and_not_counted_as_parse_failure(self):
        from lofbench.pipeline import PipelineSummary

        log = self._fake_composite_log()
        summary = PipelineSummary()
        items = extract_items(log, summary=summary)
        assert len(items) == 2  # counted, never dropped
        assert all(i.predicted == "unknown" for i in items)
        assert all(i.correct is False for i in items)
        assert summary.items_parse_failures == []  # not schema drift

    def test_unscored_call_is_counted(self):
        from lofbench.pipeline import PipelineSummary

        log = self._fake_composite_log()
        summary = PipelineSummary()
        calls = extract_calls(log, summary=summary)
        assert len(calls) == 1
        assert summary.calls_with_no_scored_items == 1
        assert calls[0].all_correct is None


class TestResolveProvenanceNoneSafety:
    def test_missing_render_metadata_key_does_not_raise(self):
        """Composite samples omit the render_metadata key entirely (not {}).
        A bare sample.metadata['render_metadata'] would KeyError; the
        None-safe .get(...) or {} must not."""
        log_meta = {"dialect": "canonical", "renderer": "canonical"}
        metadata_without_key = {"targets": ["marked"], "original_expressions": ["()"]}
        prov = resolve_provenance(metadata_without_key, "()", log_meta)
        assert prov.dialect_id == "canonical"
        assert prov.applied_injectors is None

    def test_empty_dict_render_metadata_does_not_raise(self):
        log_meta = {"dialect": "canonical", "renderer": "canonical"}
        metadata_with_empty = {"render_metadata": {}}
        prov = resolve_provenance(metadata_with_empty, "()", log_meta)
        assert prov.dialect_id == "canonical"


class TestRunPipelineAgainstFixtures:
    """End-to-end reconciliation against the two checked-in fixture logs."""

    def test_reconciliation(self, tmp_path):
        summary = run_pipeline(str(FIXTURES), str(tmp_path), suite_version="pilot-v0")
        assert summary.logs_seen == 2
        assert summary.logs_processed == 2
        assert summary.logs_skipped == []
        # single: 2 calls; composite: 10 calls
        assert summary.calls_written == 12
        # single: 2 items; composite: 80 items
        assert summary.items_written == 82
        assert summary.items_parse_failures == []
        assert summary.calls_with_no_scored_items == 0
        assert summary.transcripts_written == 82

        import pandas as pd

        items_df = pd.read_parquet(tmp_path / "pilot-v0" / "items.parquet")
        calls_df = pd.read_parquet(tmp_path / "pilot-v0" / "calls.parquet")
        assert len(items_df) == 82
        assert len(calls_df) == 12
        assert set(items_df["schema_version"]) == {"pipeline-schema-v1"}
        assert set(calls_df["schema_version"]) == {"pipeline-schema-v1"}
