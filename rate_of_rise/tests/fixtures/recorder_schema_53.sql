-- Home Assistant recorder schema 53: the tables app/backfill touches, copied verbatim from
-- the live database on 2026-10-07 (read-only, immutable=1). Regenerate when adding a
-- version to recorder.SUPPORTED_SCHEMAS.
CREATE TABLE schema_changes (
	change_id INTEGER NOT NULL,
	schema_version INTEGER,
	changed DATETIME NOT NULL,
	PRIMARY KEY (change_id)
);
CREATE TABLE state_attributes (
	attributes_id INTEGER NOT NULL,
	hash BIGINT,
	shared_attrs TEXT,
	PRIMARY KEY (attributes_id)
);
CREATE TABLE states_meta (
	metadata_id INTEGER NOT NULL,
	entity_id VARCHAR(255),
	PRIMARY KEY (metadata_id)
);
CREATE TABLE states (
	state_id INTEGER NOT NULL,
	entity_id CHAR(0),
	state VARCHAR(255),
	attributes CHAR(0),
	event_id SMALLINT,
	last_changed CHAR(0),
	last_changed_ts FLOAT,
	last_reported_ts FLOAT,
	last_updated CHAR(0),
	last_updated_ts FLOAT,
	old_state_id INTEGER,
	attributes_id INTEGER,
	context_id CHAR(0),
	context_user_id CHAR(0),
	context_parent_id CHAR(0),
	origin_idx SMALLINT,
	context_id_bin BLOB,
	context_user_id_bin BLOB,
	context_parent_id_bin BLOB,
	metadata_id INTEGER,
	PRIMARY KEY (state_id),
	FOREIGN KEY(old_state_id) REFERENCES states (state_id),
	FOREIGN KEY(attributes_id) REFERENCES state_attributes (attributes_id),
	FOREIGN KEY(metadata_id) REFERENCES states_meta (metadata_id)
);
CREATE TABLE statistics_meta (
	id INTEGER NOT NULL,
	statistic_id VARCHAR(255),
	source VARCHAR(32),
	unit_of_measurement VARCHAR(255),
	has_mean BOOLEAN,
	has_sum BOOLEAN,
	name VARCHAR(255), mean_type INTEGER NOT NULL DEFAULT 0, unit_class VARCHAR(255),
	PRIMARY KEY (id)
);
CREATE INDEX ix_state_attributes_hash ON state_attributes (hash);
CREATE INDEX ix_states_attributes_id ON states (attributes_id);
CREATE INDEX ix_states_context_id_bin ON states (context_id_bin);
CREATE INDEX ix_states_last_updated_ts ON states (last_updated_ts);
CREATE UNIQUE INDEX ix_states_meta_entity_id ON states_meta (entity_id);
CREATE INDEX ix_states_metadata_id_last_updated_ts ON states (metadata_id, last_updated_ts);
CREATE INDEX ix_states_old_state_id ON states (old_state_id);
CREATE UNIQUE INDEX ix_statistics_meta_statistic_id ON statistics_meta (statistic_id);
