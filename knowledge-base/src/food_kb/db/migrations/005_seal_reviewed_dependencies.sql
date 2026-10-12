CREATE FUNCTION guard_reviewed_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM knowledge_evidence WHERE record_id=NEW.record_id AND evidence_id=NEW.evidence_id)
 AND EXISTS(SELECT 1 FROM review_record WHERE record_id=NEW.record_id AND action='approved') THEN
  RAISE EXCEPTION 'REVIEWED_EVIDENCE_SEALED: create a new knowledge revision'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reviewed_evidence BEFORE INSERT ON knowledge_evidence FOR EACH ROW EXECUTE FUNCTION guard_reviewed_evidence();

CREATE FUNCTION guard_reviewed_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM binding_knowledge WHERE binding_id=NEW.binding_id AND record_id=NEW.record_id)
 AND EXISTS(SELECT 1 FROM review_record WHERE binding_id=NEW.binding_id AND action='approved') THEN
  RAISE EXCEPTION 'REVIEWED_BINDING_SEALED: create a new binding'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER reviewed_binding BEFORE INSERT ON binding_knowledge FOR EACH ROW EXECUTE FUNCTION guard_reviewed_binding();

CREATE FUNCTION guard_review_domain() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT u.synthetic FROM review_record r JOIN reviewer u USING(actor) WHERE r.review_id=NEW.review_id)
    IS DISTINCT FROM (SELECT synthetic FROM kb_release WHERE release_id=NEW.release_id) THEN
  RAISE EXCEPTION 'RELEASE_REVIEW_DOMAIN_MISMATCH'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER release_binding_domain BEFORE INSERT ON release_binding FOR EACH ROW EXECUTE FUNCTION guard_review_domain();
