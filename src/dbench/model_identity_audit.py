"""Schema authority for the one canonical-model identity migration audit."""

from __future__ import annotations

MODEL_IDENTITY_AUDIT_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "identity_event_id",
        "identity_event_authority",
        "migrated_at",
        "predecessor_repository_commit",
        "repository_commit",
        "predecessor_authority",
        "authority",
        "predecessor_authority_sha256",
        "authority_sha256",
        "predecessor_release_manifest_sha256",
        "predecessor_state_sha256",
        "predecessor_run_manifest_sha256",
        "authenticated_catalog_sha256",
        "predecessor_call_record_sha256",
        "requested_model_id",
        "resolved_model_id",
        "endpoint",
        "provider",
        "run_id_map",
        "trial_id_map",
        "attempts_retained",
        "remaining_attempts",
        "active_attempt",
    }
)
