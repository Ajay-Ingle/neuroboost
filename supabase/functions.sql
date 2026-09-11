-- get_user_role, get_user_streak

-- ============================================================
-- NeuroBoost v2 · functions.sql
-- Run order: schema.sql → functions.sql → policies.sql
-- Re-runnable.
-- ============================================================

-- ------------------------------------------------------------
-- get_user_role()
--
-- Returns the calling user's role. Used inside RLS policies so
-- "doctors see everything" is expressed once instead of being
-- duplicated as a subquery into every policy.
--
-- Three modifiers, each solving a distinct problem:
--
--   SECURITY DEFINER  breaks infinite recursion. A policy on
--                     user_roles calls this function; the function
--                     reads user_roles; that read would re-trigger
--                     the policy. Running as the function's owner
--                     bypasses RLS for this one lookup, ending the
--                     cycle.
--
--   STABLE            result cannot change within a single
--                     statement, so the planner evaluates it once
--                     per query instead of once per row. Without
--                     it, a doctor selecting 500 session_logs rows
--                     triggers 500 subqueries against user_roles.
--
--   set search_path   SECURITY DEFINER runs with elevated rights,
--                     so schema resolution must be pinned. Without
--                     it, a caller who can create objects could
--                     shadow user_roles in an earlier schema and
--                     have this function read their table instead.
--
-- Note: NO parameters. Identity comes from auth.uid() internally.
-- A SECURITY DEFINER function that accepts a user id is an IDOR —
-- RLS is no longer protecting it, so any caller could pass any
-- UUID and read another user's role.
-- ------------------------------------------------------------

drop function if exists public.get_user_role();

create or replace function public.get_user_role()
returns text
language sql
security definer
stable
set search_path = public
as $$
  select coalesce(
    (select role from public.user_roles where user_id = auth.uid()),
    'patient'
  );
$$;

comment on function public.get_user_role() is
  'Returns caller role from user_roles. SECURITY DEFINER to avoid policy recursion; STABLE for per-statement evaluation.';

-- authenticated users call this via policy evaluation; anon never needs it
revoke all on function public.get_user_role() from public;
grant execute on function public.get_user_role() to authenticated;