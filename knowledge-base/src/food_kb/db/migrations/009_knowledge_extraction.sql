-- Source-bound knowledge candidates and append-only reviews; no automatic approval.
CREATE TABLE extraction_run (
 extraction_id uuid PRIMARY KEY, chunk_run_id uuid NOT NULL REFERENCES chunk_run,
 version_id uuid NOT NULL REFERENCES document_version,
 processor_version text NOT NULL, config_hash text NOT NULL, output_hash text NOT NULL,
 report_key text NOT NULL, report_sha256 text NOT NULL,
 synthetic boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(chunk_run_id,processor_version,config_hash,synthetic)
);
CREATE TABLE record_extraction (
 record_id uuid PRIMARY KEY REFERENCES knowledge_record,
 extraction_id uuid NOT NULL REFERENCES extraction_run, source_chunk_ids jsonb NOT NULL,
 CHECK(jsonb_typeof(source_chunk_ids)='array' AND jsonb_array_length(source_chunk_ids)>0)
);
CREATE TABLE knowledge_validation (
 record_id uuid PRIMARY KEY REFERENCES knowledge_record,
 report jsonb NOT NULL, passed boolean NOT NULL, publication_ready boolean NOT NULL,
 schema_version text NOT NULL,
 CHECK(jsonb_typeof(report)='object'), CHECK(NOT publication_ready OR passed)
);
CREATE TABLE knowledge_field_evidence (
 record_id uuid NOT NULL REFERENCES knowledge_record, field_name text NOT NULL,
 evidence_id uuid NOT NULL REFERENCES evidence_anchor, PRIMARY KEY(record_id,field_name,evidence_id),
 CHECK(length(field_name)>0)
);
CREATE TABLE reviewer_role (
 actor text NOT NULL REFERENCES reviewer,
 role text NOT NULL CHECK(role IN ('knowledge_reviewer','knowledge_publisher')),
 PRIMARY KEY(actor,role)
);
CREATE TABLE knowledge_revision (
 record_id uuid PRIMARY KEY REFERENCES knowledge_record,
 previous_record_id uuid NOT NULL REFERENCES knowledge_record,
 actor text NOT NULL REFERENCES reviewer, reason text NOT NULL CHECK(length(trim(reason))>0),
 CHECK(record_id<>previous_record_id)
);
CREATE TABLE knowledge_review_note (
 note_id uuid PRIMARY KEY, record_id uuid NOT NULL REFERENCES knowledge_record,
 actor text NOT NULL REFERENCES reviewer,
 action text NOT NULL CHECK(action IN ('supplement_requested','disputed','comment')),
 reason text NOT NULL CHECK(length(trim(reason))>0),
 occurred_at timestamptz NOT NULL DEFAULT now(),
 event_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE
);
CREATE TABLE release_authorization (
 release_id uuid PRIMARY KEY REFERENCES kb_release, actor text NOT NULL REFERENCES reviewer
);
CREATE VIEW standard_scope_catalog AS
 SELECT k.record_id AS scope_record_id,k.version_id,k.revision,k.payload,k.effective_review_state AS review_state,
        k.synthetic,v.passed AS machine_checked,v.publication_ready
 FROM knowledge_status k JOIN knowledge_validation v USING(record_id)
 WHERE k.record_type='standard_scope_catalog';
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['extraction_run','record_extraction','knowledge_validation','knowledge_field_evidence',
 'reviewer_role','knowledge_revision','knowledge_review_note','release_authorization'] LOOP
  EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_row()',t);
 END LOOP;
END $$;

CREATE FUNCTION guard_field_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k knowledge_record; e evidence_anchor; x extraction_run;
BEGIN
 SELECT * INTO k FROM knowledge_record WHERE record_id=NEW.record_id FOR UPDATE;
 SELECT * INTO e FROM evidence_anchor WHERE evidence_id=NEW.evidence_id;
 IF NOT (k.payload->'fields' ? NEW.field_name) THEN RAISE EXCEPTION 'UNKNOWN_KNOWLEDGE_FIELD'; END IF;
 IF NOT EXISTS(SELECT 1 FROM version_asset WHERE version_id=k.version_id AND asset_id=e.asset_id) THEN
   RAISE EXCEPTION 'FIELD_EVIDENCE_VERSION_MISMATCH'; END IF;
 SELECT r.* INTO x FROM record_extraction re JOIN extraction_run r USING(extraction_id) WHERE re.record_id=NEW.record_id;
 IF FOUND AND e.parse_id IS DISTINCT FROM (SELECT parse_id FROM chunk_run WHERE run_id=x.chunk_run_id) THEN
   RAISE EXCEPTION 'FIELD_EVIDENCE_PARSE_MISMATCH'; END IF;
 IF EXISTS(SELECT 1 FROM review_record WHERE record_id=NEW.record_id AND action='approved') AND
 NOT EXISTS(SELECT 1 FROM knowledge_field_evidence WHERE record_id=NEW.record_id AND field_name=NEW.field_name AND evidence_id=NEW.evidence_id) THEN
   RAISE EXCEPTION 'REVIEWED_FIELDS_SEALED'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER field_evidence_guard BEFORE INSERT ON knowledge_field_evidence FOR EACH ROW EXECUTE FUNCTION guard_field_evidence();

CREATE FUNCTION guard_record_extraction() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k knowledge_record; x extraction_run; source_parse uuid;
BEGIN
 SELECT * INTO k FROM knowledge_record WHERE record_id=NEW.record_id FOR UPDATE;
 SELECT * INTO x FROM extraction_run WHERE extraction_id=NEW.extraction_id;
 SELECT parse_id INTO source_parse FROM chunk_run WHERE run_id=x.chunk_run_id;
 IF k.version_id IS DISTINCT FROM x.version_id OR k.synthetic IS DISTINCT FROM x.synthetic OR
 NOT EXISTS(SELECT 1 FROM parse_run p JOIN version_asset v USING(asset_id)
   WHERE p.parse_id=source_parse AND v.version_id=k.version_id) THEN
   RAISE EXCEPTION 'EXTRACTION_SOURCE_IDENTITY_MISMATCH'; END IF;
 IF k.payload ? 'source_chunk_ids' AND k.payload->'source_chunk_ids' IS DISTINCT FROM NEW.source_chunk_ids THEN
   RAISE EXCEPTION 'EXTRACTION_CHUNKS_PAYLOAD_MISMATCH'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.source_chunk_ids) j WHERE jsonb_typeof(j)<>'string' OR NOT EXISTS(
   SELECT 1 FROM chunk c WHERE c.chunk_id::text=j#>>'{}' AND c.chunk_run_id=x.chunk_run_id)) OR
 (SELECT count(*) FROM jsonb_array_elements(NEW.source_chunk_ids)) <>
 (SELECT count(DISTINCT j) FROM jsonb_array_elements(NEW.source_chunk_ids) j) THEN
   RAISE EXCEPTION 'EXTRACTION_CHUNK_RUN_MISMATCH'; END IF;
 IF EXISTS(SELECT 1 FROM knowledge_field_evidence f JOIN evidence_anchor e USING(evidence_id)
   WHERE f.record_id=NEW.record_id AND e.parse_id IS DISTINCT FROM source_parse) THEN
   RAISE EXCEPTION 'FIELD_EVIDENCE_PARSE_MISMATCH'; END IF;
 IF EXISTS(SELECT 1 FROM review_record WHERE record_id=NEW.record_id AND action='approved') AND
 NOT EXISTS(SELECT 1 FROM record_extraction WHERE record_id=NEW.record_id AND extraction_id=NEW.extraction_id
   AND source_chunk_ids=NEW.source_chunk_ids) THEN RAISE EXCEPTION 'REVIEWED_EXTRACTION_SEALED'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER extraction_source_guard BEFORE INSERT ON record_extraction FOR EACH ROW EXECUTE FUNCTION guard_record_extraction();

CREATE FUNCTION kb07_require_ready(target uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE k knowledge_record; validation knowledge_validation; source_parse uuid; governance text;
BEGIN
 SELECT * INTO k FROM knowledge_record WHERE record_id=target FOR UPDATE;
 SELECT * INTO validation FROM knowledge_validation WHERE record_id=target;
 IF NOT FOUND OR NOT validation.passed OR NOT validation.publication_ready OR validation.schema_version<>'knowledge-1' THEN
   RAISE EXCEPTION 'KNOWLEDGE_VALIDATION_BLOCKED'; END IF;
 IF k.record_type NOT IN ('requirement','definition','method','practice','standard_scope_catalog') OR
 jsonb_typeof(k.payload->'fields') IS DISTINCT FROM 'object' OR k.payload->'fields'='{}'::jsonb THEN
   RAISE EXCEPTION 'KNOWLEDGE_TYPE_OR_FIELDS_INVALID'; END IF;
 IF (jsonb_typeof(validation.report->'issues')='array' AND jsonb_array_length(validation.report->'issues')>0) OR
 (jsonb_typeof(validation.report->'publication_blockers')='array' AND jsonb_array_length(validation.report->'publication_blockers')>0) THEN
   RAISE EXCEPTION 'KNOWLEDGE_VALIDATION_BLOCKED'; END IF;
 IF EXISTS(SELECT 1 FROM knowledge_review_note WHERE record_id=target AND action='supplement_requested') THEN
   RAISE EXCEPTION 'SUPPLEMENT_REQUIRES_NEW_REVISION'; END IF;
 SELECT action INTO governance FROM governance_event WHERE record_id=target ORDER BY event_sequence DESC LIMIT 1;
 IF governance IN ('withdrawn','disputed','superseded') THEN RAISE EXCEPTION 'GOVERNANCE_BLOCKED'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_each(k.payload->'fields') f WHERE f.value->'value'<>'null'::jsonb AND NOT EXISTS(
   SELECT 1 FROM knowledge_field_evidence fe WHERE fe.record_id=target AND fe.field_name=f.key)) THEN
   RAISE EXCEPTION 'FIELD_EVIDENCE_MISSING'; END IF;
 SELECT c.parse_id INTO source_parse FROM record_extraction re JOIN extraction_run er USING(extraction_id)
   JOIN chunk_run c ON c.run_id=er.chunk_run_id WHERE re.record_id=target;
 IF NOT FOUND AND NOT k.synthetic THEN RAISE EXCEPTION 'SOURCE_EXTRACTION_REQUIRED'; END IF;
 IF NOT EXISTS(SELECT 1 FROM knowledge_field_evidence WHERE record_id=target) OR EXISTS(
   SELECT 1 FROM knowledge_field_evidence fe JOIN evidence_anchor e USING(evidence_id)
   LEFT JOIN knowledge_evidence ke ON ke.record_id=fe.record_id AND ke.evidence_id=fe.evidence_id
   LEFT JOIN content_block b ON b.block_id=e.block_id AND b.parse_id=e.parse_id
   WHERE fe.record_id=target AND (ke.role IS DISTINCT FROM 'direct' OR ke.version_id IS DISTINCT FROM k.version_id OR
   ke.asset_id IS DISTINCT FROM e.asset_id OR
   (e.locator_quality<>'verified' AND NOT(k.synthetic AND e.locator_quality='synthetic')) OR
   (source_parse IS NOT NULL AND e.parse_id IS DISTINCT FROM source_parse) OR
   (e.locator_quality='verified' AND (b.block_id IS NULL OR e.quote IS NULL OR position(e.quote in b.text_raw)=0 OR
      NOT(e.locator @> b.locator))))) THEN RAISE EXCEPTION 'VERIFIED_FIELD_EVIDENCE_REQUIRED'; END IF;
END $$;

CREATE FUNCTION guard_knowledge_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE old_record knowledge_record; new_record knowledge_record;
BEGIN
 SELECT * INTO old_record FROM knowledge_record WHERE record_id=NEW.previous_record_id;
 SELECT * INTO new_record FROM knowledge_record WHERE record_id=NEW.record_id;
 IF old_record.logical_key<>new_record.logical_key OR old_record.version_id IS DISTINCT FROM new_record.version_id
 OR old_record.record_type<>new_record.record_type OR old_record.synthetic<>new_record.synthetic THEN
   RAISE EXCEPTION 'KNOWLEDGE_REVISION_IDENTITY_MISMATCH'; END IF;
 IF NOT EXISTS(SELECT 1 FROM reviewer_role WHERE actor=NEW.actor AND role='knowledge_reviewer') THEN
   RAISE EXCEPTION 'KNOWLEDGE_REVIEWER_ROLE_REQUIRED'; END IF;
 IF (SELECT synthetic FROM reviewer WHERE actor=NEW.actor) IS DISTINCT FROM new_record.synthetic THEN
   RAISE EXCEPTION 'REVISION_REVIEW_DOMAIN_MISMATCH'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER revision_identity BEFORE INSERT ON knowledge_revision FOR EACH ROW EXECUTE FUNCTION guard_knowledge_revision();

CREATE FUNCTION guard_kb07_review() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE validation knowledge_validation; identity reviewer;
BEGIN
 SELECT * INTO validation FROM knowledge_validation WHERE record_id=NEW.record_id;
 IF NOT FOUND THEN RETURN NEW; END IF;
 IF NOT EXISTS(SELECT 1 FROM reviewer_role WHERE actor=NEW.actor AND role='knowledge_reviewer') THEN
   RAISE EXCEPTION 'KNOWLEDGE_REVIEWER_ROLE_REQUIRED'; END IF;
 SELECT * INTO identity FROM reviewer WHERE actor=NEW.actor;
 IF identity.synthetic AND (position('synthetic' in lower(identity.identity_reference))=0 OR
   position('fixture' in lower(identity.identity_reference))=0) THEN RAISE EXCEPTION 'SYNTHETIC_FIXTURE_IDENTITY_REQUIRED'; END IF;
 IF NEW.action='machine_checked' THEN RAISE EXCEPTION 'MACHINE_STATE_USES_VALIDATION_RECORD'; END IF;
 IF NEW.action='approved' THEN
   PERFORM kb07_require_ready(NEW.record_id);
   IF NEW.evidence_id IS NULL OR NOT EXISTS(SELECT 1 FROM knowledge_field_evidence
     WHERE record_id=NEW.record_id AND evidence_id=NEW.evidence_id) THEN RAISE EXCEPTION 'REVIEW_FIELD_EVIDENCE_MISMATCH'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER kb07_review_guard BEFORE INSERT ON review_record FOR EACH ROW EXECUTE FUNCTION guard_kb07_review();

CREATE FUNCTION guard_kb07_note() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k knowledge_record; current_state review_state;
BEGIN
 SELECT * INTO k FROM knowledge_record WHERE record_id=NEW.record_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM reviewer_role WHERE actor=NEW.actor AND role='knowledge_reviewer') OR
 (SELECT synthetic FROM reviewer WHERE actor=NEW.actor) IS DISTINCT FROM (SELECT synthetic FROM knowledge_record WHERE record_id=NEW.record_id) THEN
  RAISE EXCEPTION 'KNOWLEDGE_REVIEWER_ROLE_REQUIRED'; END IF;
 SELECT action INTO current_state FROM review_record WHERE record_id=NEW.record_id ORDER BY event_sequence DESC LIMIT 1;
 current_state:=coalesce(current_state,k.review_state);
 IF NEW.action='supplement_requested' AND current_state NOT IN ('extracted','machine_checked','review_pending') THEN
   RAISE EXCEPTION 'SUPPLEMENT_REQUIRES_NEW_REVISION'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER kb07_note_guard BEFORE INSERT ON knowledge_review_note FOR EACH ROW EXECUTE FUNCTION guard_kb07_note();

CREATE FUNCTION kb07_note_governance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.action='disputed' THEN
   INSERT INTO governance_event(event_id,record_id,action,actor,reason)
   VALUES(NEW.note_id,NEW.record_id,'disputed',NEW.actor,NEW.reason);
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER kb07_dispute_governance AFTER INSERT ON knowledge_review_note FOR EACH ROW EXECUTE FUNCTION kb07_note_governance();

CREATE FUNCTION guard_kb07_governance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE k knowledge_record; previous text;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM knowledge_validation WHERE record_id=NEW.record_id) THEN RETURN NEW; END IF;
 SELECT * INTO k FROM knowledge_record WHERE record_id=NEW.record_id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM reviewer_role WHERE actor=NEW.actor AND role='knowledge_reviewer') OR
 (SELECT synthetic FROM reviewer WHERE actor=NEW.actor) IS DISTINCT FROM k.synthetic THEN
   RAISE EXCEPTION 'KNOWLEDGE_REVIEWER_ROLE_REQUIRED'; END IF;
 SELECT action INTO previous FROM governance_event WHERE record_id=NEW.record_id ORDER BY event_sequence DESC LIMIT 1;
 IF NEW.action='reinstated' AND (previous IS NULL OR previous NOT IN ('withdrawn','disputed','superseded')) THEN
   RAISE EXCEPTION 'ILLEGAL_GOVERNANCE_TRANSITION'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER kb07_governance_guard BEFORE INSERT ON governance_event FOR EACH ROW EXECUTE FUNCTION guard_kb07_governance();

CREATE FUNCTION guard_kb07_release() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE publisher text; member release_knowledge; identity reviewer;
BEGIN
 IF NOT EXISTS(SELECT 1 FROM release_knowledge rk JOIN knowledge_validation v USING(record_id) WHERE rk.release_id=NEW.release_id) THEN RETURN NULL; END IF;
 SELECT actor INTO publisher FROM release_authorization WHERE release_id=NEW.release_id;
 IF publisher IS NULL OR NOT EXISTS(SELECT 1 FROM reviewer_role WHERE actor=publisher AND role='knowledge_publisher') OR
 (SELECT synthetic FROM reviewer WHERE actor=publisher) IS DISTINCT FROM NEW.synthetic THEN
   RAISE EXCEPTION 'KNOWLEDGE_PUBLISHER_ROLE_REQUIRED'; END IF;
 SELECT * INTO identity FROM reviewer WHERE actor=publisher;
 IF identity.synthetic AND (position('synthetic' in lower(identity.identity_reference))=0 OR
   position('fixture' in lower(identity.identity_reference))=0) THEN RAISE EXCEPTION 'SYNTHETIC_FIXTURE_IDENTITY_REQUIRED'; END IF;
 FOR member IN SELECT rk.* FROM release_knowledge rk JOIN knowledge_validation v USING(record_id)
   WHERE rk.release_id=NEW.release_id LOOP
   PERFORM kb07_require_ready(member.record_id);
   IF NOT EXISTS(SELECT 1 FROM review_record r JOIN reviewer_role u USING(actor)
     WHERE r.review_id=member.review_id AND u.role='knowledge_reviewer') THEN
     RAISE EXCEPTION 'KNOWLEDGE_REVIEWER_ROLE_REQUIRED'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER kb07_release_guard AFTER INSERT ON kb_release DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_kb07_release();
