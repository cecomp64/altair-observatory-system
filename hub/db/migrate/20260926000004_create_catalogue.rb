# The object catalogue, ported from astrophotography-database (objects,
# object_aliases, object_showcases).
class CreateCatalogue < ActiveRecord::Migration[8.1]
  def change
    create_table :astro_objects do |t|
      t.string :primary_name, null: false
      t.decimal :ra_deg, precision: 9, scale: 5
      t.decimal :dec_deg, precision: 9, scale: 5
      t.string :object_type
      t.decimal :magnitude, precision: 5, scale: 2
      t.decimal :size_major_arcmin, precision: 8, scale: 2
      t.decimal :size_minor_arcmin, precision: 8, scale: 2
      t.decimal :position_angle_deg, precision: 6, scale: 2
      t.string :constellation
      t.string :source, null: false, default: "custom"
      t.string :source_ref
      t.references :created_by, foreign_key: { to_table: :users }
      t.timestamps
    end
    add_index :astro_objects, :primary_name, using: :gin, opclass: :gin_trgm_ops, name: "index_astro_objects_on_primary_name_trgm"
    add_index :astro_objects, :dec_deg
    add_index :astro_objects, :object_type
    add_index :astro_objects, :constellation

    create_table :object_aliases do |t|
      t.references :astro_object, null: false, foreign_key: { on_delete: :cascade }
      t.string :name, null: false
      t.string :normalized_name, null: false
      t.string :catalog
      t.timestamps
    end
    add_index :object_aliases, :normalized_name
    add_index :object_aliases, :normalized_name, using: :gin, opclass: :gin_trgm_ops, name: "index_object_aliases_on_normalized_name_trgm"
    add_index :object_aliases, [ :astro_object_id, :normalized_name ], unique: true

    create_table :object_showcases do |t|
      t.references :astro_object, null: false, foreign_key: { on_delete: :cascade }, index: { unique: true }
      t.string :source_type, null: false, default: "upload"
      t.bigint :data_product_id
      t.string :survey_name
      t.timestamps
    end
  end
end
