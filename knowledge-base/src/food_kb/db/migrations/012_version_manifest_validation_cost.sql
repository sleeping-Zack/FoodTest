-- Large snapshots must not decompress their full payload once per source row.
-- Every deferred event still checks cardinality; parent and one member compare
-- exact identities. Immutable members cannot be changed while keeping that count.
ALTER TABLE version_reference_snapshot ADD COLUMN source_count integer
 GENERATED ALWAYS AS (jsonb_array_length(payload->'sources')) STORED;
ALTER TABLE version_reference_snapshot ADD CONSTRAINT version_source_count_positive CHECK(source_count>0);

CREATE OR REPLACE FUNCTION guard_version_reference_members() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid uuid; expected jsonb; actual jsonb; expected_count integer; actual_count bigint; first_key text;
BEGIN
 sid := NEW.snapshot_id;
 SELECT source_count INTO expected_count FROM version_reference_snapshot WHERE snapshot_id=sid;
 SELECT count(*),min(source_key) INTO actual_count,first_key
 FROM version_reference_snapshot_source WHERE snapshot_id=sid;
 IF expected_count IS DISTINCT FROM actual_count THEN
  RAISE EXCEPTION 'VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH';
 END IF;
 IF TG_TABLE_NAME='version_reference_snapshot_source' THEN
  IF NEW.source_key IS DISTINCT FROM first_key THEN RETURN NULL; END IF;
 END IF;
 SELECT jsonb_agg(jsonb_build_object('source_key',s->>'source_key','asset_id',s->>'asset_id',
     'legacy_id',s->>'legacy_id') ORDER BY s->>'source_key') INTO expected
 FROM version_reference_snapshot t, jsonb_array_elements(t.payload->'sources') s WHERE t.snapshot_id=sid;
 SELECT jsonb_agg(jsonb_build_object('source_key',source_key,'asset_id',asset_id,
     'legacy_id',legacy_id::text) ORDER BY source_key) INTO actual
 FROM version_reference_snapshot_source WHERE snapshot_id=sid;
 IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'; END IF;
 RETURN NULL;
END $$;
