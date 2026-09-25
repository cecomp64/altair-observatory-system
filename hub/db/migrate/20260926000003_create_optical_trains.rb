# One optical train = one Altair rig (key = rig name). Every existing telescope
# gets a default train keyed by its slug so existing targets stay valid.
class CreateOpticalTrains < ActiveRecord::Migration[8.1]
  def up
    create_table :optical_trains do |t|
      t.references :telescope, null: false, foreign_key: true
      t.string :key, null: false
      t.string :name, null: false
      t.string :camera_name
      t.string :camera_type, null: false, default: "mono"
      t.string :bayer_pattern
      t.decimal :pixel_size_um, precision: 6, scale: 3
      t.integer :sensor_width_px
      t.integer :sensor_height_px
      t.decimal :focal_length_mm, precision: 8, scale: 2
      t.boolean :has_rotator, null: false, default: false
      t.jsonb :filters, null: false, default: []
      t.jsonb :header_aliases, null: false, default: {}
      t.boolean :active, null: false, default: true
      t.timestamps
    end
    add_index :optical_trains, [ :telescope_id, :key ], unique: true
    add_foreign_key :telescopes, :optical_trains, column: :default_optical_train_id

    execute <<~SQL
      INSERT INTO optical_trains (telescope_id, key, name, camera_type, created_at, updated_at)
      SELECT id, slug, name || ' (default train)', 'mono', NOW(), NOW() FROM telescopes;
      UPDATE telescopes SET default_optical_train_id = optical_trains.id
      FROM optical_trains WHERE optical_trains.telescope_id = telescopes.id;
    SQL
  end

  def down
    remove_foreign_key :telescopes, column: :default_optical_train_id
    drop_table :optical_trains
  end
end
