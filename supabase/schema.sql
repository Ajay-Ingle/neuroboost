-- ============================================================
-- NeuroBoost v2 · schema.sql
-- Tables, constraints, indexes, signup trigger.
-- Run order: schema.sql → functions.sql → policies.sql
-- Re-runnable during development.
-- ============================================================

-- ------------------------------------------------------------
-- 0. Teardown (dev only — cascade drops dependent policies)
--    Reverse dependency order: children before parents.
-- ------------------------------------------------------------
drop trigger if exists on_auth_user_created on auth.users;
drop function if exists public.handle_new_user();

drop table if exists public.user_stats   cascade;
drop table if exists public.session_logs cascade;
drop table if exists public.user_roles   cascade;
drop table if exists public.profiles     cascade;


-- ------------------------------------------------------------
-- 1. profiles — slow-changing identity + clinical baseline
--    id IS the auth user id: 1:1 guaranteed structurally,
--    no surrogate key, no separate user_id column.
-- ------------------------------------------------------------
create table public.profiles (
  id                   uuid primary key references auth.users(id) on delete cascade,
  full_name            text,
  age                  integer      check (age between 0 and 120),
  gender               text         check (gender in ('male','female','non-binary','other','prefer_not_to_say')),
  dominant_hand        text         check (dominant_hand in ('left','right')),
  primary_cohort       text,
  sleep_average_hours  numeric(3,1) check (sleep_average_hours between 0.0 and 24.0),
  medical_conditions   jsonb        not null default '[]'::jsonb,
  baseline_notes       text,
  created_at           timestamptz  not null default now(),
  updated_at           timestamptz  not null default now()
);

comment on table public.profiles is
  'One row per auth user. PK is the auth UUID, so duplicate profiles are impossible.';


-- ------------------------------------------------------------
-- 2. user_roles — role lives here, NOT on profiles.
--    RLS is row-level, not column-level: a role column on a
--    user-writable table is a self-promotion path. Separating it
--    lets policies.sql grant SELECT and deny INSERT/UPDATE.
-- ------------------------------------------------------------
create table public.user_roles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  role        text        not null default 'patient'
                          check (role in ('patient','doctor','admin')),
  granted_at  timestamptz not null default now(),
  granted_by  uuid        references auth.users(id) on delete set null
);

comment on table public.user_roles is
  'Separate from profiles so RLS can deny all writes. Default patient = least privilege.';


-- ------------------------------------------------------------
-- 3. session_logs — append-only clinical event stream
--    FK → profiles(id): a session without a clinical baseline is
--    uninterpretable, so profile-before-session is enforced by the
--    database rather than by application convention.
--    ON DELETE CASCADE: complete erasure under GDPR / DPDP.
--    Trade-off: account deletion destroys research data permanently.
-- ------------------------------------------------------------
create table public.session_logs (
  id            uuid        primary key default gen_random_uuid(),
  user_id       uuid        not null references public.profiles(id) on delete cascade,
  session_date  timestamptz not null default now(),
  mode          text        not null check (mode in ('reflex','memory','focus')),

  -- Raw measurements
  reaction_time_ms_avg          numeric(8,2) check (reaction_time_ms_avg >= 0),
  accuracy_rate                 numeric(5,2) check (accuracy_rate between 0.00 and 100.00),
  completion_time_seconds       numeric(8,2) check (completion_time_seconds >= 0),
  memory_span_level             integer      check (memory_span_level >= 1),
  difficulty_progression_level  integer      check (difficulty_progression_level >= 1),

  -- Replayability: persisting the source array makes every derived
  -- metric recomputable if a formula is later revised.
  raw_reaction_times            jsonb not null default '[]'::jsonb,

  -- Derived metrics — ratios, standard deviations and signed deltas,
  -- NOT percentages. Unbounded because clean() clamps to ±999.99
  -- client-side; nullable because derivation needs n > 2 samples.
  error_rate                      numeric(5,2) check (error_rate between 0 and 100),
  efficiency_score                numeric(5,2),
  performance_stability_variance  numeric(5,2),
  attention_stability_score       numeric(5,2),
  learning_improvement_rate       numeric(5,2),
  effectiveness_score             numeric(5,2),
  learnability_score              numeric(5,2),
  adaptation_accuracy_score       numeric(5,2) check (adaptation_accuracy_score between 0 and 100),

  -- Subjective self-report, captured post-session
  cognitive_load_perceived  smallint check (cognitive_load_perceived between 1 and 10),
  user_satisfaction         smallint check (user_satisfaction between 1 and 5),

  -- session_date = when the session happened (may predate the row for
  -- migrated guest sessions). created_at = when the row was written.
  created_at    timestamptz not null default now()
);

comment on table public.session_logs is
  'Append-only. No DELETE policy is defined, so rows are immutable audit records.';


-- ------------------------------------------------------------
-- 4. user_stats — mutable adaptive-engine checkpoint, 1 row/user
--    Per-mode columns because the three modes have structurally
--    different settings: a spawn interval in ms, a sequence length,
--    a distractor count. One scalar cannot hold all three, and a
--    single "difficulty level" WOULD be derivable from the last log
--    row — which is exactly why this table would then be redundant.
-- ------------------------------------------------------------
create table public.user_stats (
  user_id                    uuid primary key references public.profiles(id) on delete cascade,

  -- Aggregate counters
  total_sessions_completed   integer not null default 0 check (total_sessions_completed >= 0),
  overall_play_time_seconds  integer not null default 0 check (overall_play_time_seconds >= 0),
  current_streak_days        integer not null default 0 check (current_streak_days >= 0),
  longest_streak_days        integer not null default 0 check (longest_streak_days >= 0),
  last_session_at            timestamptz,

  -- Live engine state: what the user resumes at on a new device
  current_reflex_spawn_rate  integer check (current_reflex_spawn_rate between 500 and 3000),
  current_memory_sequence    integer check (current_memory_sequence >= 3),
  current_focus_distractors  integer check (current_focus_distractors >= 1),

  -- EWMA baselines (0.9 * previous + 0.1 * current)
  baseline_reaction_time_ms  numeric(8,2),
  baseline_accuracy_rate     numeric(5,2) check (baseline_accuracy_rate between 0 and 100),

  updated_at                 timestamptz not null default now()
);

comment on table public.user_stats is
  'Engine checkpoint, not a summary. Cannot be derived from session_logs.';


-- ------------------------------------------------------------
-- 5. Signup trigger
--    session_logs.user_id references profiles, so a user with no
--    profile row cannot log a session. This guarantees both the
--    profile and the default role exist the moment auth succeeds.
--    SECURITY DEFINER: the trigger runs as owner, because the
--    signing-up user has no privileges yet.
-- ------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id) values (new.id)
    on conflict (id) do nothing;

  insert into public.user_roles (user_id) values (new.id)
    on conflict (user_id) do nothing;

  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();


-- ------------------------------------------------------------
-- 6. Keep updated_at honest
--    A DEFAULT fires only on INSERT, so without this trigger the
--    column would silently report the creation time forever.
-- ------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_touch_updated
  before update on public.profiles
  for each row execute function public.touch_updated_at();

create trigger user_stats_touch_updated
  before update on public.user_stats
  for each row execute function public.touch_updated_at();


-- ------------------------------------------------------------
-- 7. Indexes — only what a real query path needs.
--    Every PK already has a unique index; user_stats.user_id being
--    the PK is what makes upsert onConflict work without extra DDL.
-- ------------------------------------------------------------
-- Dashboard and AI service: "latest N sessions for this user"
create index idx_session_logs_user_date
  on public.session_logs (user_id, session_date desc);

-- Mode-filtered trend queries: "this user's reflex history"
create index idx_session_logs_user_mode
  on public.session_logs (user_id, mode, session_date desc);


-- ------------------------------------------------------------
-- 8. Lock everything.
--    RLS is OFF by default on new tables and the anon key is public,
--    so an unlocked table is world-readable. Enabled with zero
--    policies = deny all, which is the correct state until
--    policies.sql opens each path deliberately.
-- ------------------------------------------------------------
alter table public.profiles     enable row level security;
alter table public.user_roles   enable row level security;
alter table public.session_logs enable row level security;
alter table public.user_stats   enable row level security;