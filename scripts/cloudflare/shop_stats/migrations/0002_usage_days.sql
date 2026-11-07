-- Activity is independent of immutable shop runs and their permanent deduplication.
CREATE TABLE usage_days (
    installation_id TEXT NOT NULL,
    day TEXT NOT NULL,
    version_major INTEGER NOT NULL,
    version_minor INTEGER NOT NULL,
    version_patch INTEGER NOT NULL,
    features INTEGER NOT NULL CHECK(features BETWEEN 0 AND 7),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(installation_id, day)
);
CREATE INDEX usage_day ON usage_days(day, installation_id);
