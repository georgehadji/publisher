-- builds.error_kind accepts every StageError kind. 001's list predates E2.2's
-- 'timeout' and 'resource_exhausted', which the executor raises: the worker's
-- failure write then broke the CHECK, the worker died inside its own error
-- handler, and the build sat 'running' until its lease ran out.
-- The list is ErrorKind (publisher_stages) plus the worker's own two markers;
-- tests/test_single_source.py fails if they drift apart again.
ALTER TABLE builds DROP CONSTRAINT IF EXISTS builds_error_kind_check;
ALTER TABLE builds ADD CONSTRAINT builds_error_kind_check CHECK (
    error_kind IS NULL OR error_kind IN (
        'bad_input', 'policy_violation', 'engine_bug', 'infra',
        'external_limit', 'timeout', 'resource_exhausted', 'INTERNAL', 'exhausted'
    )
);
