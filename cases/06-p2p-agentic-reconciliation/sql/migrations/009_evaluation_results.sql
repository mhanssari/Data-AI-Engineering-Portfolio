-- Evaluation results as rows, so that the admin Evaluations page and any trend read stored figures instead of parsing the
-- markdown reports in evidence/. Written by eval-retrieval, agent-eval and access-probe on the pipeline login.

-- Grain: one execution of one evaluation.
create table ops.evaluation_run (
    evaluation_run_id  bigint generated always as identity primary key,
    kind               text not null check (kind in ('retrieval', 'agents', 'access_probe')),
    provider           text,
    model              text,
    recorded_at        timestamptz not null default clock_timestamp(),
    parameters         jsonb not null default '{}'
);

-- Grain: one measure of one subject (a method, a planted line, a role and table) in one evaluation run.
-- value holds a number where there is one; passed says whether the measure met what was expected, where that applies.
create table ops.evaluation_result (
    evaluation_run_id  bigint not null references ops.evaluation_run,
    section            text not null,
    subject            text not null,
    measure            text not null,
    value              numeric,
    passed             boolean,
    detail             jsonb,
    primary key (evaluation_run_id, section, subject, measure)
);
create index evaluation_result_section on ops.evaluation_result (section, measure);
