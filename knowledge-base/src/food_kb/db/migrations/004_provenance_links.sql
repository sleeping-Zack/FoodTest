-- Each input revision/importer may resolve to an existing immutable candidate.
CREATE TABLE legacy_knowledge (
 legacy_id uuid NOT NULL REFERENCES legacy_record,
 record_id uuid NOT NULL REFERENCES knowledge_record,
 PRIMARY KEY(legacy_id,record_id)
);
INSERT INTO legacy_knowledge SELECT legacy_id,record_id FROM knowledge_record WHERE legacy_id IS NOT NULL;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON legacy_knowledge FOR EACH ROW EXECUTE FUNCTION immutable_row();

CREATE FUNCTION guard_category_definition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (SELECT version_id FROM knowledge_record WHERE record_id=NEW.definition_record_id) IS DISTINCT FROM NEW.version_id THEN
  RAISE EXCEPTION 'CATEGORY_DEFINITION_VERSION_MISMATCH'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER category_definition_version BEFORE INSERT ON category_node FOR EACH ROW EXECUTE FUNCTION guard_category_definition();
