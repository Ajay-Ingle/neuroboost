-- Every RLS policy
-- ============================================================
-- NeuroBoost v2 · policies.sql
-- Run order: schema.sql → functions.sql → policies.sql
-- Re-runnable: every policy is dropped before creation.
--
-- Naming: <table>_<command>_<who>. Unique descriptive names make a
-- duplicate visible. v1 shipped two policies differing only by a
-- trailing period, and because permissive policies OR together, one
-- of them with USING(true) voided isolation on the whole table.
--
-- Every policy targets `authenticated` explicitly. Omitting the role
-- defaults to `public`, which includes `anon` — so an unauthenticated
-- visitor is excluded structurally rather than incidentally.
--
-- Commands with no policy are DENIED by default. Omissions below are
-- deliberate and noted.
-- ============================================================


-- ------------------------------------------------------------
-- profiles
--   patient : read + write own row
--   doctor  : read all, write none
--   nobody  : delete
-- ------------------------------------------------------------

drop policy if exists profiles_select_own    on public.profiles;
drop policy if exists profiles_select_doctor on public.profiles;
drop policy if exists profiles_insert_own    on public.profiles;
drop policy if exists profiles_update_own    on public.profiles;

-- profiles.id IS the auth uid, which is why this reads = id
-- rather than = user_id like the other tables.
create policy profiles_select_own
  on public.profiles
  for select
  to authenticated
  using (auth.uid() = id);

-- ORs with the policy above: a doctor matches here, a patient there.
create policy profiles_select_doctor
  on public.profiles
  for select
  to authenticated
  using (public.get_user_role() = 'doctor');

-- The signup trigger is SECURITY DEFINER and bypasses RLS entirely;
-- this policy exists for client-side upserts from the profile form.
create policy profiles_insert_own
  on public.profiles
  for insert
  to authenticated
  with check (auth.uid() = id);

-- USING selects which rows may be modified; WITH CHECK validates the
-- result. Both stated explicitly rather than letting WITH CHECK
-- silently default to the USING clause.
create policy profiles_update_own
  on public.profiles
  for update
  to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);

-- No DELETE policy: clinical records are not client-deletable.
-- No doctor INSERT/UPDATE: doctor access is read-only by design.


-- ------------------------------------------------------------
-- user_roles
--   patient : read own row only
--   nobody  : insert, update, or delete — under any role
--
-- The absence of write policies IS this table's reason to exist.
-- In v1 the role column lived on profiles, and because RLS is
-- row-level and not column-level, the UPDATE policy `auth.uid() = id`
-- permitted a user to rewrite their own role to 'doctor'. Isolating
-- the column into a table with no write path closes that.
--
-- get_user_role() is deliberately NOT used here — it reads this
-- table, so referencing it in a policy on this table builds the
-- recursion cycle SECURITY DEFINER exists to rescue. Own-row access
-- needs only auth.uid().
-- ------------------------------------------------------------

drop policy if exists user_roles_select_own on public.user_roles;

create policy user_roles_select_own
  on public.user_roles
  for select
  to authenticated
  using (auth.uid() = user_id);

-- Roles are granted only via dashboard or service_role.
-- No doctor SELECT either: reading the full roster is an admin
-- concern, not a clinical one.


-- ------------------------------------------------------------
-- session_logs
--   patient : read own, insert own
--   doctor  : read all
--   nobody  : update or delete — append-only audit trail
-- ------------------------------------------------------------

drop policy if exists session_logs_select_own    on public.session_logs;
drop policy if exists session_logs_select_doctor on public.session_logs;
drop policy if exists session_logs_insert_own    on public.session_logs;

create policy session_logs_select_own
  on public.session_logs
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy session_logs_select_doctor
  on public.session_logs
  for select
  to authenticated
  using (public.get_user_role() = 'doctor');

-- WITH CHECK is what makes a forged user_id in the client payload
-- impossible. The app may send anything; the database rejects it.
create policy session_logs_insert_own
  on public.session_logs
  for insert
  to authenticated
  with check (auth.uid() = user_id);

-- No UPDATE, no DELETE, for any role. A clinical measurement is a
-- historical fact — it cannot be edited or erased after the session.
-- This is what makes the table an audit trail rather than storage.


-- ------------------------------------------------------------
-- user_stats
--   patient : read, insert, update own row
--   doctor  : read all
--   nobody  : delete
--
-- Doctor SELECT added in v2. v1 omitted it, so a doctor could read a
-- patient's entire session history but not their current engine
-- state — inconsistent, since the state is derived from the history
-- they could already see.
-- ------------------------------------------------------------

drop policy if exists user_stats_select_own    on public.user_stats;
drop policy if exists user_stats_select_doctor on public.user_stats;
drop policy if exists user_stats_insert_own    on public.user_stats;
drop policy if exists user_stats_update_own    on public.user_stats;

create policy user_stats_select_own
  on public.user_stats
  for select
  to authenticated
  using (auth.uid() = user_id);

create policy user_stats_select_doctor
  on public.user_stats
  for select
  to authenticated
  using (public.get_user_role() = 'doctor');

-- Separate INSERT and UPDATE rather than FOR ALL: v1 used FOR ALL,
-- which silently grants DELETE too. Naming each command means the
-- absence of DELETE is visible in the policy list.
create policy user_stats_insert_own
  on public.user_stats
  for insert
  to authenticated
  with check (auth.uid() = user_id);

create policy user_stats_update_own
  on public.user_stats
  for update
  to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- No DELETE policy.


-- ------------------------------------------------------------
-- Composition audit
-- Run after applying. Read the qual column as an OR-chain per
-- (table, command) group and confirm nothing widens to `true`.
-- ------------------------------------------------------------
-- select tablename, cmd, policyname, roles, qual, with_check
-- from pg_policies
-- where schemaname = 'public'
-- order by tablename, cmd, policyname;