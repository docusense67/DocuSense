-- Supabase-only hardening, applied by deploy/cloud/migrate.sh after every migration (idempotent).
-- Can also be pasted into the Supabase SQL Editor. One statement, so it runs in one call.
--
-- 1. ADGVS never uses Supabase's Data API (PostgREST). Supabase grants its `anon` and
--    `authenticated` roles access to every new table in `public` by default, which would let
--    anyone holding the publishable (anon) key read or change ADGVS tables while the Data API
--    is on. Take those grants away, now and for tables that future migrations create.
--    Defence in depth with backend migration c4e8a1f2b9d0, which also turns on Row Level
--    Security for every app table; `setup-db-roles` adds the policies for the API login only.
-- 2. The private Storage bucket for issued PDFs and scan images. `public = false` means every
--    file needs the server's secret key; the browser only ever downloads through the API.
DO $hardening$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
        REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated;
        -- Default privileges of the role running the migrations (the owner, `postgres`).
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
    END IF;

    IF to_regclass('storage.buckets') IS NOT NULL THEN
        INSERT INTO storage.buckets (id, name, public)
        VALUES ('adgvs-private', 'adgvs-private', false)
        ON CONFLICT (id) DO UPDATE SET public = false;
    END IF;
END
$hardening$;
