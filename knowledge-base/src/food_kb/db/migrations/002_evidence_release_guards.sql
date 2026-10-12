CREATE FUNCTION guard_anchor() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE block content_block; BEGIN
 IF NEW.locator_quality='verified' THEN
  IF NEW.block_id IS NULL OR NEW.parse_id IS NULL OR NEW.quote IS NULL THEN
   RAISE EXCEPTION 'VERIFIED_ANCHOR_REQUIRES_PARSED_BLOCK_AND_QUOTE'; END IF;
  SELECT * INTO block FROM content_block WHERE block_id=NEW.block_id AND parse_id=NEW.parse_id;
  IF NOT FOUND OR length(NEW.quote)=0 OR position(NEW.quote in block.text_raw)=0 THEN
   RAISE EXCEPTION 'QUOTE_NOT_IN_BLOCK'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER anchor_validation BEFORE INSERT ON evidence_anchor FOR EACH ROW EXECUTE FUNCTION guard_anchor();

CREATE FUNCTION guard_manifest_member() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE manifest jsonb; latest review_record; event text; BEGIN
 SELECT r.manifest INTO manifest FROM kb_release r WHERE release_id=NEW.release_id;
 IF TG_TABLE_NAME='release_knowledge' THEN
  IF NOT (manifest->'knowledge' ? NEW.record_id::text) THEN RAISE EXCEPTION 'MEMBER_NOT_IN_MANIFEST'; END IF;
  SELECT * INTO latest FROM review_record WHERE record_id=NEW.record_id ORDER BY reviewed_at DESC,review_id DESC LIMIT 1;
  SELECT action INTO event FROM governance_event WHERE record_id=NEW.record_id ORDER BY occurred_at DESC,event_id DESC LIMIT 1;
  IF event IN ('withdrawn','disputed','superseded') THEN RAISE EXCEPTION 'GOVERNANCE_BLOCKED'; END IF;
 ELSE
  IF NOT (manifest->'bindings' ? NEW.binding_id::text) THEN RAISE EXCEPTION 'MEMBER_NOT_IN_MANIFEST'; END IF;
  SELECT * INTO latest FROM review_record WHERE binding_id=NEW.binding_id ORDER BY reviewed_at DESC,review_id DESC LIMIT 1;
 END IF;
 IF latest.review_id IS DISTINCT FROM NEW.review_id THEN RAISE EXCEPTION 'LATEST_APPROVAL_REQUIRED'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER release_manifest_knowledge BEFORE INSERT ON release_knowledge FOR EACH ROW EXECUTE FUNCTION guard_manifest_member();
CREATE TRIGGER release_manifest_binding BEFORE INSERT ON release_binding FOR EACH ROW EXECUTE FUNCTION guard_manifest_member();

CREATE OR REPLACE FUNCTION guard_release() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM release_knowledge WHERE release_id=NEW.release_id) THEN RAISE EXCEPTION 'EMPTY_RELEASE'; END IF;
 IF NEW.manifest_hash <> encode(sha256(convert_to(NEW.manifest::text,'UTF8')),'hex') THEN RAISE EXCEPTION 'MANIFEST_HASH_MISMATCH'; END IF;
 IF NEW.manifest->'knowledge' IS DISTINCT FROM (SELECT jsonb_agg(record_id::text ORDER BY record_id::text) FROM release_knowledge WHERE release_id=NEW.release_id) THEN
  RAISE EXCEPTION 'RELEASE_MANIFEST_MISMATCH'; END IF;
 IF NEW.manifest->'bindings' IS DISTINCT FROM coalesce((SELECT jsonb_agg(binding_id::text ORDER BY binding_id::text) FROM release_binding WHERE release_id=NEW.release_id),'[]'::jsonb) THEN
  RAISE EXCEPTION 'RELEASE_BINDING_MANIFEST_MISMATCH'; END IF;
 RETURN NULL;
END $$;

CREATE FUNCTION guard_job_transition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status <> 'running' OR NEW.status='running' OR NEW.finished_at IS NULL THEN
  RAISE EXCEPTION 'JOB_HISTORY_IMMUTABLE'; END IF;
 IF (to_jsonb(OLD)-ARRAY['status','finished_at','report']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','finished_at','report']) THEN
  RAISE EXCEPTION 'JOB_IDENTITY_IMMUTABLE'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER job_transition BEFORE UPDATE OR DELETE ON ingest_job FOR EACH ROW EXECUTE FUNCTION guard_job_transition();
