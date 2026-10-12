CREATE TYPE identity_state AS ENUM ('verified','unverified','draft','conflicting');
CREATE TYPE review_state AS ENUM ('extracted','machine_checked','review_pending','approved','rejected');
CREATE TYPE quality_state AS ENUM ('not_extracted','usable','partial','needs_ocr','unreadable');
CREATE TYPE value_state AS ENUM ('present','missing','not_extracted','unreadable','not_applicable');

CREATE TABLE source_asset (
 asset_id text PRIMARY KEY CHECK (asset_id ~ '^[0-9a-f]{64}$'),
 sha256 text NOT NULL UNIQUE CHECK (sha256 = asset_id),
 media_type text NOT NULL, size bigint NOT NULL CHECK(size>=0), storage_key text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(storage_key = 'objects/' || left(sha256,2) || '/' || sha256)
);
CREATE TABLE source_document (
 document_id uuid PRIMARY KEY, source_key text NOT NULL UNIQUE,
 kind text NOT NULL CHECK(kind IN ('standard','announcement','guide','legacy')),
 standard_family text, issuer text, title text NOT NULL
);
CREATE TABLE document_version (
 version_id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES source_document,
 designation text NOT NULL, revision text NOT NULL, title text NOT NULL,
 publication_date date, implementation_date date, status_claim text,
 identity_status identity_state NOT NULL DEFAULT 'unverified',
 transition_text text, metadata_state value_state NOT NULL DEFAULT 'not_extracted',
 metadata jsonb NOT NULL DEFAULT '{}',
 UNIQUE(document_id,designation,revision), UNIQUE(version_id,document_id)
);
CREATE TABLE version_asset (
 version_id uuid NOT NULL REFERENCES document_version,
 asset_id text NOT NULL REFERENCES source_asset,
 role text NOT NULL CHECK(role IN ('original','amendment','draft','metadata')),
 PRIMARY KEY(version_id,asset_id)
);
CREATE TABLE source_registration (
 registration_id uuid PRIMARY KEY, asset_id text NOT NULL REFERENCES source_asset,
 source_key text NOT NULL, source_url text, retrieved_at text,
 index_asset_id text REFERENCES source_asset, json_pointer text,
 identity_status identity_state NOT NULL DEFAULT 'unverified',
 quality quality_state NOT NULL DEFAULT 'not_extracted', metadata jsonb NOT NULL DEFAULT '{}',
 UNIQUE(source_key,asset_id,index_asset_id,json_pointer)
);
CREATE TABLE legacy_record (
 legacy_id uuid PRIMARY KEY, index_asset_id text NOT NULL REFERENCES source_asset,
 source_key text NOT NULL, json_pointer text NOT NULL, legacy_key text NOT NULL,
 adapter text NOT NULL, importer_version text NOT NULL,
 raw_payload jsonb NOT NULL, unknown_fields jsonb NOT NULL DEFAULT '[]',
 UNIQUE(index_asset_id,json_pointer,importer_version)
);
CREATE TABLE document_claim (
 legacy_id uuid PRIMARY KEY REFERENCES legacy_record,
 version_id uuid NOT NULL REFERENCES document_version
);
CREATE TABLE parse_run (
 parse_id uuid PRIMARY KEY, asset_id text NOT NULL REFERENCES source_asset,
 parser_version text NOT NULL, config_hash text NOT NULL, output_hash text NOT NULL,
 quality quality_state NOT NULL, UNIQUE(asset_id,parser_version,config_hash), UNIQUE(parse_id,asset_id)
);
CREATE TABLE content_block (
 block_id uuid PRIMARY KEY, parse_id uuid NOT NULL REFERENCES parse_run,
 structural_key text NOT NULL, parent_id uuid, kind text NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=0), text_raw text NOT NULL, text_search text,
 locator jsonb NOT NULL CHECK(jsonb_typeof(locator)='object' AND locator <> '{}'),
 UNIQUE(parse_id,structural_key), UNIQUE(block_id,parse_id),
 FOREIGN KEY(parent_id,parse_id) REFERENCES content_block(block_id,parse_id)
);
CREATE TABLE evidence_anchor (
 evidence_id uuid PRIMARY KEY, asset_id text NOT NULL REFERENCES source_asset,
 parse_id uuid, block_id uuid, locator jsonb NOT NULL,
 quote text, quote_hash text, locator_quality text NOT NULL CHECK(locator_quality IN ('source_pointer','page_claim','verified','synthetic')),
 FOREIGN KEY(parse_id,asset_id) REFERENCES parse_run(parse_id,asset_id),
 FOREIGN KEY(block_id,parse_id) REFERENCES content_block(block_id,parse_id),
 CHECK(block_id IS NULL OR parse_id IS NOT NULL),
 CHECK(jsonb_typeof(locator)='object' AND locator <> '{}'),
 CHECK(NOT locator ? 'pdf_page' OR (jsonb_typeof(locator->'pdf_page')='number' AND (locator->>'pdf_page')::int>0)),
 CHECK((quote IS NULL AND quote_hash IS NULL) OR (quote IS NOT NULL AND quote_hash=encode(sha256(convert_to(quote,'UTF8')),'hex')))
);
CREATE TABLE knowledge_record (
 record_id uuid PRIMARY KEY, logical_key text NOT NULL, revision text NOT NULL,
 record_type text NOT NULL, version_id uuid REFERENCES document_version,
 legacy_id uuid REFERENCES legacy_record, payload jsonb NOT NULL,
 extraction_run text NOT NULL, review_state review_state NOT NULL DEFAULT 'extracted',
 field_states jsonb NOT NULL DEFAULT '{}', synthetic boolean NOT NULL DEFAULT false,
 UNIQUE(logical_key,revision), UNIQUE(record_id,version_id)
);
CREATE TABLE knowledge_evidence (
 record_id uuid NOT NULL REFERENCES knowledge_record,
 evidence_id uuid NOT NULL REFERENCES evidence_anchor,
 version_id uuid, asset_id text NOT NULL,
 role text NOT NULL CHECK(role IN ('direct','provenance','supporting')),
 PRIMARY KEY(record_id,evidence_id),
 FOREIGN KEY(record_id,version_id) REFERENCES knowledge_record(record_id,version_id),
 FOREIGN KEY(version_id,asset_id) REFERENCES version_asset,
 FOREIGN KEY(asset_id) REFERENCES source_asset
);
CREATE TABLE category_namespace (
 namespace text PRIMARY KEY, version_id uuid NOT NULL REFERENCES document_version,
 UNIQUE(namespace,version_id)
);
CREATE TABLE category_node (
 category_id uuid PRIMARY KEY, namespace text NOT NULL, version_id uuid NOT NULL,
 code text NOT NULL, label text NOT NULL, parent_id uuid,
 definition_record_id uuid NOT NULL REFERENCES knowledge_record,
 UNIQUE(namespace,version_id,code), UNIQUE(category_id,namespace,version_id),
 FOREIGN KEY(namespace,version_id) REFERENCES category_namespace(namespace,version_id),
 FOREIGN KEY(parent_id,namespace,version_id) REFERENCES category_node(category_id,namespace,version_id),
 CHECK(parent_id IS DISTINCT FROM category_id)
);
CREATE TABLE relation (
 relation_id uuid PRIMARY KEY, from_record uuid NOT NULL REFERENCES knowledge_record,
 to_record uuid REFERENCES knowledge_record, target_claim text,
 kind text NOT NULL, scope jsonb NOT NULL DEFAULT '{}', version_binding jsonb NOT NULL DEFAULT '{}',
 condition_ref uuid REFERENCES knowledge_record, evidence_id uuid NOT NULL REFERENCES evidence_anchor,
 review_state review_state NOT NULL DEFAULT 'extracted',
 CHECK(to_record IS NOT NULL OR target_claim IS NOT NULL)
);
CREATE TABLE rule_template (
 template_id text NOT NULL, version text NOT NULL, handler text NOT NULL,
 input_schema jsonb NOT NULL, parameter_schema jsonb NOT NULL, outcome_schema jsonb NOT NULL,
 tests_ref text NOT NULL, PRIMARY KEY(template_id,version)
);
CREATE TABLE rule_binding (
 binding_id uuid PRIMARY KEY, template_id text NOT NULL, template_version text NOT NULL,
 condition_ref uuid REFERENCES knowledge_record, scope jsonb NOT NULL,
 review_state review_state NOT NULL DEFAULT 'review_pending',
 FOREIGN KEY(template_id,template_version) REFERENCES rule_template
);
CREATE TABLE binding_knowledge (
 binding_id uuid NOT NULL REFERENCES rule_binding, record_id uuid NOT NULL REFERENCES knowledge_record,
 PRIMARY KEY(binding_id,record_id)
);
CREATE TABLE reviewer (
 actor text PRIMARY KEY, identity_reference text NOT NULL CHECK(length(identity_reference)>0),
 synthetic boolean NOT NULL DEFAULT false
);
CREATE TABLE review_record (
 review_id uuid PRIMARY KEY, record_id uuid REFERENCES knowledge_record,
 binding_id uuid REFERENCES rule_binding,
 action review_state NOT NULL, actor text NOT NULL REFERENCES reviewer,
 reviewed_at timestamptz NOT NULL DEFAULT now(), reason text NOT NULL CHECK(length(reason)>0),
 evidence_id uuid REFERENCES evidence_anchor,
 CHECK((record_id IS NULL) <> (binding_id IS NULL))
);
CREATE TABLE governance_event (
 event_id uuid PRIMARY KEY, record_id uuid NOT NULL REFERENCES knowledge_record,
 action text NOT NULL CHECK(action IN ('superseded','withdrawn','disputed','reinstated')),
 actor text NOT NULL REFERENCES reviewer, reason text NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE kb_release (
 release_id uuid PRIMARY KEY, manifest_hash text NOT NULL UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now(), parent_release uuid REFERENCES kb_release,
 synthetic boolean NOT NULL DEFAULT false, index_readiness jsonb NOT NULL,
 manifest jsonb NOT NULL, state text NOT NULL CHECK(state='published')
);
CREATE TABLE release_knowledge (
 release_id uuid NOT NULL REFERENCES kb_release, record_id uuid NOT NULL REFERENCES knowledge_record,
 review_id uuid NOT NULL REFERENCES review_record, PRIMARY KEY(release_id,record_id)
);
CREATE TABLE release_binding (
 release_id uuid NOT NULL REFERENCES kb_release, binding_id uuid NOT NULL REFERENCES rule_binding,
 review_id uuid NOT NULL REFERENCES review_record, PRIMARY KEY(release_id,binding_id)
);
CREATE TABLE chunk (
 chunk_id uuid PRIMARY KEY, parse_id uuid NOT NULL REFERENCES parse_run,
 strategy_version text NOT NULL, content_hash text NOT NULL, retrieval_text text NOT NULL,
 parent_block_id uuid, token_count integer CHECK(token_count>=0),
 FOREIGN KEY(parent_block_id,parse_id) REFERENCES content_block(block_id,parse_id)
);
CREATE TABLE chunk_evidence (
 chunk_id uuid NOT NULL REFERENCES chunk, evidence_id uuid NOT NULL REFERENCES evidence_anchor,
 PRIMARY KEY(chunk_id,evidence_id)
);
CREATE TABLE chunk_context_link (
 chunk_id uuid NOT NULL REFERENCES chunk, target_block_id uuid NOT NULL REFERENCES content_block,
 role text NOT NULL, PRIMARY KEY(chunk_id,target_block_id,role)
);
CREATE TABLE coverage_ledger (
 scope_id uuid NOT NULL, target_ref text NOT NULL, domain text NOT NULL CHECK(domain IN ('knowledge','audit')),
 stage text NOT NULL, status text NOT NULL CHECK(status IN ('passed','candidate_issue','needs_evidence','not_covered','not_applicable','execution_failed')),
 reason text NOT NULL, evidence_id uuid REFERENCES evidence_anchor,
 PRIMARY KEY(scope_id,target_ref,stage)
);
CREATE TABLE ingest_job (
 job_id uuid PRIMARY KEY, task_key text NOT NULL, stage text NOT NULL,
 input_fingerprint text NOT NULL, config_hash text NOT NULL, processor_version text NOT NULL,
 started_at timestamptz NOT NULL, finished_at timestamptz,
 status text NOT NULL CHECK(status IN ('running','completed','completed_with_issues','failed')),
 report jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ingest_job_key ON ingest_job(task_key);
CREATE TABLE ingest_result (
 job_id uuid NOT NULL REFERENCES ingest_job, item_key text NOT NULL,
 legacy_id uuid REFERENCES legacy_record,
 status text NOT NULL CHECK(status IN ('success','duplicate','quarantined','source_missing','identity_conflict','unrecognized','failed')),
 error_code text, retryable boolean NOT NULL DEFAULT false, details jsonb NOT NULL,
 PRIMARY KEY(job_id,item_key)
);

CREATE FUNCTION immutable_row() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'IMMUTABLE_HISTORY: append a new revision'; END $$;
DO $$ DECLARE t text; BEGIN
 FOR t IN SELECT unnest(ARRAY['source_asset','source_document','document_version','version_asset','source_registration',
 'legacy_record','document_claim','parse_run','content_block','evidence_anchor','knowledge_record','knowledge_evidence',
 'category_namespace','category_node','relation','rule_template','rule_binding','binding_knowledge','reviewer','review_record',
 'governance_event','kb_release','release_knowledge','release_binding','chunk','chunk_evidence','chunk_context_link','coverage_ledger','ingest_result'])
 LOOP EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_row()',t); END LOOP;
END $$;

CREATE FUNCTION guard_knowledge() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.review_state NOT IN ('extracted','review_pending') THEN RAISE EXCEPTION 'REVIEW_REQUIRED: use append-only review_record'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_initial_state BEFORE INSERT ON knowledge_record FOR EACH ROW EXECUTE FUNCTION guard_knowledge();
CREATE TRIGGER binding_initial_state BEFORE INSERT ON rule_binding FOR EACH ROW EXECUTE FUNCTION guard_knowledge();

CREATE FUNCTION guard_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a evidence_anchor; BEGIN
 SELECT * INTO a FROM evidence_anchor WHERE evidence_id=NEW.evidence_id;
 IF a.asset_id IS DISTINCT FROM NEW.asset_id THEN RAISE EXCEPTION 'EVIDENCE_ASSET_MISMATCH'; END IF;
 IF NEW.role='direct' AND NEW.version_id IS NULL THEN RAISE EXCEPTION 'DIRECT_EVIDENCE_REQUIRES_VERSION'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_evidence_asset BEFORE INSERT ON knowledge_evidence FOR EACH ROW EXECUTE FUNCTION guard_evidence();

CREATE FUNCTION guard_review() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous review_state; k knowledge_record; reviewer_synthetic boolean; BEGIN
 SELECT synthetic INTO reviewer_synthetic FROM reviewer WHERE actor=NEW.actor;
 IF NEW.record_id IS NOT NULL THEN
  SELECT * INTO k FROM knowledge_record WHERE record_id=NEW.record_id FOR UPDATE;
  SELECT action INTO previous FROM review_record WHERE record_id=NEW.record_id ORDER BY reviewed_at DESC,review_id DESC LIMIT 1;
  previous := coalesce(previous,k.review_state);
  IF reviewer_synthetic IS DISTINCT FROM k.synthetic THEN RAISE EXCEPTION 'REVIEW_DOMAIN_MISMATCH'; END IF;
 ELSE
  PERFORM 1 FROM rule_binding WHERE binding_id=NEW.binding_id FOR UPDATE;
  SELECT action INTO previous FROM review_record WHERE binding_id=NEW.binding_id ORDER BY reviewed_at DESC,review_id DESC LIMIT 1;
  previous := coalesce(previous,'review_pending');
 END IF;
 IF NOT ((previous='extracted' AND NEW.action IN ('machine_checked','review_pending','rejected')) OR
         (previous='machine_checked' AND NEW.action IN ('review_pending','rejected')) OR
         (previous='review_pending' AND NEW.action IN ('approved','rejected'))) THEN
  RAISE EXCEPTION 'ILLEGAL_REVIEW_TRANSITION';
 END IF;
 IF NEW.action='approved' AND NEW.record_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM knowledge_evidence ke JOIN evidence_anchor e USING(evidence_id)
  JOIN document_version v ON v.version_id=ke.version_id
  WHERE ke.record_id=NEW.record_id AND ke.role='direct'
   AND (e.locator_quality='verified' OR (k.synthetic AND e.locator_quality='synthetic'))
   AND v.identity_status='verified'
 ) THEN RAISE EXCEPTION 'VERIFIED_DIRECT_EVIDENCE_REQUIRED'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER review_transition BEFORE INSERT ON review_record FOR EACH ROW EXECUTE FUNCTION guard_review();

CREATE FUNCTION guard_release_member() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r review_record; synthetic_release boolean; BEGIN
 SELECT * INTO r FROM review_record WHERE review_id=NEW.review_id;
 SELECT synthetic INTO synthetic_release FROM kb_release WHERE release_id=NEW.release_id;
 IF r.action IS DISTINCT FROM 'approved' THEN RAISE EXCEPTION 'APPROVED_REVIEW_REQUIRED'; END IF;
 IF TG_TABLE_NAME='release_knowledge' THEN
  IF r.record_id IS DISTINCT FROM NEW.record_id OR (SELECT synthetic FROM knowledge_record WHERE record_id=NEW.record_id) IS DISTINCT FROM synthetic_release THEN
   RAISE EXCEPTION 'RELEASE_REVIEW_MISMATCH'; END IF;
 ELSE
  IF r.binding_id IS DISTINCT FROM NEW.binding_id THEN RAISE EXCEPTION 'RELEASE_REVIEW_MISMATCH'; END IF;
  IF NOT EXISTS (SELECT 1 FROM binding_knowledge WHERE binding_id=NEW.binding_id) OR EXISTS (
   SELECT 1 FROM binding_knowledge b WHERE b.binding_id=NEW.binding_id AND NOT EXISTS
   (SELECT 1 FROM release_knowledge k WHERE k.release_id=NEW.release_id AND k.record_id=b.record_id)) THEN
   RAISE EXCEPTION 'BINDING_KNOWLEDGE_NOT_IN_RELEASE'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER release_knowledge_guard BEFORE INSERT ON release_knowledge FOR EACH ROW EXECUTE FUNCTION guard_release_member();
CREATE TRIGGER release_binding_guard BEFORE INSERT ON release_binding FOR EACH ROW EXECUTE FUNCTION guard_release_member();

CREATE FUNCTION guard_release() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM release_knowledge WHERE release_id=NEW.release_id) THEN RAISE EXCEPTION 'EMPTY_RELEASE'; END IF;
 IF NEW.manifest->'knowledge' IS DISTINCT FROM (SELECT jsonb_agg(record_id::text ORDER BY record_id::text) FROM release_knowledge WHERE release_id=NEW.release_id) THEN
  RAISE EXCEPTION 'RELEASE_MANIFEST_MISMATCH'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER release_complete AFTER INSERT ON kb_release DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION guard_release();

CREATE VIEW knowledge_status AS
 SELECT k.*,coalesce((SELECT action FROM review_record r WHERE r.record_id=k.record_id ORDER BY reviewed_at DESC,review_id DESC LIMIT 1),k.review_state) AS effective_review_state
 FROM knowledge_record k;
