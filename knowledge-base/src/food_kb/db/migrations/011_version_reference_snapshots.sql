-- Candidate governance snapshots; no amendment of old version or release rows.
CREATE TABLE version_reference_snapshot (
 snapshot_id uuid PRIMARY KEY,
 processor_version text NOT NULL,
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 payload_hash text NOT NULL CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(processor_version,input_hash),
 CHECK((payload->>'schema_version'='version-reference-1') IS TRUE),
 CHECK((payload->>'snapshot_id'=snapshot_id::text) IS TRUE),
 CHECK((payload->>'processor_version'=processor_version) IS TRUE),
 CHECK((payload->>'input_hash'=input_hash) IS TRUE),
 CHECK((payload->>'review_state'='review_pending') IS TRUE),
 CHECK((payload->'executable'='false'::jsonb) IS TRUE),
 CHECK((payload->'scope_complete'='false'::jsonb) IS TRUE),
 CHECK((jsonb_typeof(payload->'sources')='array' AND jsonb_array_length(payload->'sources')>0) IS TRUE),
 CHECK((jsonb_typeof(payload->'versions')='array') IS TRUE),
 CHECK((jsonb_typeof(payload->'references')='array') IS TRUE)
);
CREATE TABLE version_reference_snapshot_source (
 snapshot_id uuid NOT NULL REFERENCES version_reference_snapshot,
 source_key text NOT NULL CHECK(length(source_key)>0),
 asset_id text NOT NULL REFERENCES source_asset,
 legacy_id uuid REFERENCES legacy_record,
 PRIMARY KEY(snapshot_id,source_key)
);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON version_reference_snapshot FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON version_reference_snapshot_source FOR EACH ROW EXECUTE FUNCTION immutable_row();
CREATE TRIGGER version_reference_source_identity BEFORE INSERT ON version_reference_snapshot_source
 FOR EACH ROW EXECUTE FUNCTION guard_taxonomy_source();

CREATE FUNCTION guard_version_reference_members() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid uuid; expected jsonb; actual jsonb;
BEGIN
 sid := NEW.snapshot_id;
 SELECT jsonb_agg(jsonb_build_object('source_key',s->>'source_key','asset_id',s->>'asset_id',
     'legacy_id',s->>'legacy_id') ORDER BY s->>'source_key') INTO expected
 FROM version_reference_snapshot t, jsonb_array_elements(t.payload->'sources') s WHERE t.snapshot_id=sid;
 SELECT jsonb_agg(jsonb_build_object('source_key',source_key,'asset_id',asset_id,
     'legacy_id',legacy_id::text) ORDER BY source_key) INTO actual
 FROM version_reference_snapshot_source WHERE snapshot_id=sid;
 IF expected IS DISTINCT FROM actual THEN RAISE EXCEPTION 'VERSION_REFERENCE_SOURCE_MANIFEST_MISMATCH'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER version_reference_complete AFTER INSERT ON version_reference_snapshot
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_version_reference_members();
CREATE CONSTRAINT TRIGGER version_reference_sources_complete AFTER INSERT ON version_reference_snapshot_source
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_version_reference_members();
