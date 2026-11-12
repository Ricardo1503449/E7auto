-- Keep only anonymous identities permanently; daily activity still expires.
CREATE TABLE IF NOT EXISTS usage_installations (
    installation_id TEXT PRIMARY KEY NOT NULL
);

-- Include app-only and all retained historical days, without adding daily counts.
INSERT OR IGNORE INTO usage_installations (installation_id)
    SELECT DISTINCT installation_id FROM usage_days;
