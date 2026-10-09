class CreateDynamicCatalogues < ActiveRecord::Migration[8.1]
  def change
    # Lists that change on their own (AAVSO alerts and campaigns, bright
    # comets...), refreshed on a schedule. One row per source in
    # Catalogue::Dynamic::SOURCES, holding the state of its last refresh.
    create_table :dynamic_catalogues do |t|
      t.string :key, null: false, index: { unique: true }
      t.datetime :attempted_at
      t.datetime :refreshed_at
      t.string :last_error
      t.jsonb :last_result, null: false, default: {}
      t.timestamps
    end

    # An object's membership of a list. Objects stay in the catalogue when
    # they drop off a list; the entry gets removed_at instead.
    create_table :dynamic_catalogue_entries do |t|
      t.references :dynamic_catalogue, null: false, foreign_key: true
      t.references :astro_object, null: false, foreign_key: true
      t.datetime :first_seen_at, null: false
      t.datetime :last_seen_at, null: false
      t.datetime :removed_at
      t.jsonb :details, null: false, default: {}
      t.timestamps
    end
    add_index :dynamic_catalogue_entries, [ :dynamic_catalogue_id, :astro_object_id ], unique: true, name: "index_dynamic_catalogue_entries_uniqueness"
  end
end
