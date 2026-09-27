# Custom objects an admin imported (import:astrodb) are for everyone, as are
# custom objects with no creator, which no member could otherwise see.
class ShareAdminImportedObjects < ActiveRecord::Migration[8.0]
  def up
    execute <<~SQL.squish
      UPDATE astro_objects SET shared = TRUE
      WHERE source = 'custom'
        AND (created_by_id IS NULL
             OR (source_ref LIKE 'astrodb:%' AND created_by_id IN (SELECT id FROM users WHERE role = 1)))
    SQL
  end

  def down; end
end
