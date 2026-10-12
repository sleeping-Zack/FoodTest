-- now() is identical inside a transaction; random UUIDs are not event order.
-- This migration is for the unpublished M1 registry. Do not invent order for existing approvals.
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM review_record) OR EXISTS(SELECT 1 FROM governance_event) THEN
  RAISE EXCEPTION 'EVENT_ORDER_MIGRATION_REQUIRES_REVIEW_HISTORY_ASSESSMENT'; END IF;
END $$;
ALTER TABLE review_record ADD COLUMN event_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE;
ALTER TABLE governance_event ADD COLUMN event_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE;

DO $$ DECLARE f text; definition text; BEGIN
 FOREACH f IN ARRAY ARRAY['guard_review','guard_manifest_member'] LOOP
  SELECT pg_get_functiondef(p.oid) INTO definition FROM pg_proc p
   JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() AND p.proname=f;
  IF position('ORDER BY reviewed_at DESC,review_id DESC' in definition)=0 THEN
   RAISE EXCEPTION 'REVIEW_FUNCTION_DEFINITION_CHANGED'; END IF;
  definition:=replace(definition,'ORDER BY reviewed_at DESC,review_id DESC','ORDER BY event_sequence DESC');
  definition:=replace(definition,'ORDER BY occurred_at DESC,event_id DESC','ORDER BY event_sequence DESC');
  EXECUTE definition;
 END LOOP;
END $$;
CREATE OR REPLACE VIEW knowledge_status AS
 SELECT k.*,coalesce((SELECT action FROM review_record r WHERE r.record_id=k.record_id ORDER BY event_sequence DESC LIMIT 1),k.review_state) AS effective_review_state
 FROM knowledge_record k;
