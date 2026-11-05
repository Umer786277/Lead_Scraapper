"""Add per-campaign template options and enrollment-time assignment metadata.

Run only after reviewing and approving the migration:
    python -m scripts.migrate_campaign_template_options
"""

import db


MIGRATIONS = [
    "ALTER TABLE campaigns "
    "ADD COLUMN IF NOT EXISTS template_selection_mode TEXT NOT NULL "
    "DEFAULT 'sequential' "
    "CHECK (template_selection_mode IN ('sequential', 'random'))",
    "ALTER TABLE campaigns "
    "ADD COLUMN IF NOT EXISTS template_cycle_index INTEGER NOT NULL DEFAULT 0 "
    "CHECK (template_cycle_index >= 0)",
    "ALTER TABLE campaigns "
    "ADD COLUMN IF NOT EXISTS template_cycle_order JSONB NOT NULL "
    "DEFAULT '[]'::jsonb",
    "CREATE TABLE IF NOT EXISTS campaign_template_options ("
    "campaign_id INTEGER NOT NULL "
    "REFERENCES campaigns(id) ON DELETE CASCADE, "
    "sequence_id INTEGER NOT NULL "
    "REFERENCES outreach_sequences(id) ON DELETE RESTRICT, "
    "option_position SMALLINT NOT NULL CHECK (option_position >= 0), "
    "PRIMARY KEY (campaign_id, sequence_id), "
    "UNIQUE (campaign_id, option_position))",
    "CREATE INDEX IF NOT EXISTS idx_campaign_template_options_sequence "
    "ON campaign_template_options(sequence_id, campaign_id)",
    "ALTER TABLE campaign_script_assignments "
    "ADD COLUMN IF NOT EXISTS selected_sequence_id INTEGER "
    "REFERENCES outreach_sequences(id) ON DELETE SET NULL",
    "ALTER TABLE campaign_script_assignments "
    "ADD COLUMN IF NOT EXISTS option_position SMALLINT",
]


def run():
    print("Applying campaign template options migration...")
    with db.get_conn() as connection:
        for statement in MIGRATIONS:
            print(f"  {statement}")
            connection.execute(statement)
    print("Migration committed.")


if __name__ == "__main__":
    run()
