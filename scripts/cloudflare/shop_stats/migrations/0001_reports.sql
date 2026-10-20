CREATE TABLE runs (
    run_id TEXT PRIMARY KEY NOT NULL,
    strategy TEXT NOT NULL CHECK (strategy IN ('transfer','continuous')),
    skystone_spent INTEGER NOT NULL CHECK (skystone_spent >= 0),
    covenant_purchases INTEGER NOT NULL CHECK (covenant_purchases >= 0),
    mystic_purchases INTEGER NOT NULL CHECK (mystic_purchases >= 0)
);

CREATE TABLE pools (
    strategy TEXT PRIMARY KEY NOT NULL,
    skystone_spent INTEGER NOT NULL DEFAULT 0,
    covenant_purchases INTEGER NOT NULL DEFAULT 0,
    mystic_purchases INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
INSERT INTO pools(strategy) VALUES ('transfer'), ('continuous');

-- The report insert and its aggregate change form one atomic SQLite statement.
-- Ignoring a duplicate insert never fires this trigger a second time.
CREATE TRIGGER accumulate_shop_run AFTER INSERT ON runs BEGIN
    UPDATE pools SET
        skystone_spent = skystone_spent + NEW.skystone_spent,
        covenant_purchases = covenant_purchases + NEW.covenant_purchases,
        mystic_purchases = mystic_purchases + NEW.mystic_purchases,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
    WHERE strategy = NEW.strategy;
END;
