-- Where a raw row came from, without its values. The proposal trace needs file, row and checksum for each core row; the
-- governance reviewer may not read raw, which holds bank numbers. This function returns only landing metadata, so the
-- reviewer can see the same trace as `p2p trace` and still cannot read a single source field.

-- Grain of the result: one raw row, identified by raw_id, which is unique across the raw schema.
create function ops.raw_origin(p_raw_ids bigint[])
returns table (raw_table text, raw_id bigint, source_system text, batch_id text, source_file text, file_row integer,
               extracted_at timestamptz, cdc_op text, checksum text)
language plpgsql stable security definer set search_path = ops, pg_temp as $$
declare
    t text;
begin
    -- Table names come from the catalogue, never from the caller; the caller supplies only ids.
    for t in
        select c.relname from pg_class c
        where c.relnamespace = 'raw'::regnamespace and c.relkind = 'r' and c.relname <> 'meta_template'
        order by c.relname
    loop
        return query execute format(
            $q$
            select %L::text, r.raw_id, r.source_system, r.batch_id, r.source_file, r.file_row, r.extracted_at, r.cdc_op,
                   f.checksum
            from raw.%I r
            left join ops.ingest_file f on f.source_system = r.source_system
                 and f.file_name = r.batch_id || '/' || r.source_file and f.run_id = r.run_id
            where r.raw_id = any($1)
            $q$, 'raw.' || t, t)
        using p_raw_ids;
    end loop;
end;
$$;
revoke execute on function ops.raw_origin(bigint[]) from public;
