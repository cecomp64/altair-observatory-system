# Migration 9 (§4.2): the Hub's projection of Altair's frames, FOV matches,
# observing nights, calibration masters and equipment events.
class CreateFramesAndNights < ActiveRecord::Migration[8.1]
  def change
    create_table :frames do |t|
      t.string :sha256, null: false, index: { unique: true }
      t.references :processing_node, foreign_key: true
      t.bigint :altair_frame_id
      t.references :telescope, null: false, foreign_key: true, index: false
      t.references :optical_train, null: false, foreign_key: true
      t.references :target, foreign_key: true, index: false
      t.references :project, foreign_key: true
      t.references :exposure_plan, foreign_key: true
      t.string :assignment_source, null: false, default: "unlinked"
      t.string :image_type, null: false
      t.date :night, null: false
      t.datetime :date_obs, null: false
      t.string :object_header
      t.string :filter
      t.boolean :filter_known, null: false, default: true
      t.decimal :exposure_s, precision: 10, scale: 3
      t.decimal :gain, precision: 8, scale: 2
      t.decimal :offset, precision: 8, scale: 2
      t.string :binning
      t.string :readout_mode
      t.decimal :sensor_temp_c, precision: 6, scale: 2
      t.decimal :rotator_pos, precision: 12, scale: 3
      t.string :rotator_units
      t.decimal :ra_deg, precision: 9, scale: 5
      t.decimal :dec_deg, precision: 9, scale: 5
      t.decimal :rotation_deg, precision: 6, scale: 2
      t.integer :width_px
      t.integer :height_px
      t.decimal :fov_width_deg, precision: 8, scale: 5
      t.decimal :fov_height_deg, precision: 8, scale: 5
      t.string :file_name, null: false
      t.string :logical_path, null: false
      t.string :status, null: false, default: "collected"
      t.string :status_reason
      t.jsonb :quality, null: false, default: {}
      t.jsonb :storage, null: false, default: {}
      t.jsonb :headers, null: false, default: {}
      t.string :origin, null: false, default: "collect"
      t.datetime :fov_matched_at
      t.timestamps
    end
    add_index :frames, [ :target_id, :filter ]
    add_index :frames, [ :telescope_id, :night ]
    add_index :frames, [ :optical_train_id, :night ]
    add_index :frames, :date_obs
    add_index :frames, :image_type
    add_index :frames, :filter
    add_index :frames, :dec_deg
    add_index :frames, :file_name, using: :gin, opclass: :gin_trgm_ops, name: "index_frames_on_file_name_trgm"
    add_index :frames, :target_id, where: "target_id IS NULL AND image_type = 'light'", name: "index_frames_unassigned_lights"

    create_table :frame_objects do |t|
      t.references :frame, null: false, foreign_key: { on_delete: :cascade }, index: false
      t.references :astro_object, null: false, foreign_key: { on_delete: :cascade }
      t.string :association_type, null: false, default: "in_fov"
      t.decimal :angular_distance_arcmin, precision: 8, scale: 2
      t.timestamps
    end
    add_index :frame_objects, [ :frame_id, :astro_object_id ], unique: true

    create_table :observing_nights do |t|
      t.references :telescope, null: false, foreign_key: true
      t.references :optical_train, null: false, foreign_key: true, index: false
      t.date :night, null: false
      t.string :state, null: false, default: "open"
      t.string :closed_by
      t.datetime :roof_open_at
      t.datetime :roof_closed_at
      t.datetime :session_end_at
      t.integer :lights_count, null: false, default: 0
      t.integer :calibration_count, null: false, default: 0
      t.decimal :light_seconds, precision: 12, scale: 2, null: false, default: 0
      t.string :manifest_sha256
      t.timestamps
    end
    add_index :observing_nights, [ :optical_train_id, :night ], unique: true

    create_table :calibration_masters do |t|
      t.references :processing_node, null: false, foreign_key: true, index: false
      t.bigint :altair_id, null: false
      t.references :optical_train, null: false, foreign_key: true
      t.string :kind, null: false
      t.string :filter
      t.decimal :exposure_s, precision: 10, scale: 3
      t.decimal :gain, precision: 8, scale: 2
      t.decimal :offset, precision: 8, scale: 2
      t.string :binning
      t.decimal :sensor_temp_c, precision: 6, scale: 2
      t.decimal :rotator_pos, precision: 12, scale: 3
      t.date :night
      t.integer :n_frames, null: false, default: 0
      t.string :sha256, null: false
      t.boolean :superseded, null: false, default: false
      t.timestamps
    end
    add_index :calibration_masters, [ :processing_node_id, :altair_id ], unique: true

    create_table :equipment_events do |t|
      t.references :optical_train, null: false, foreign_key: true
      t.datetime :at, null: false
      t.string :kind, null: false
      t.string :filter
      t.text :note
      t.references :created_by, foreign_key: { to_table: :users }
      t.datetime :altair_synced_at
      t.timestamps
    end

    add_column :telescopes, :worker_last_heartbeat_at, :datetime
    add_column :telescopes, :worker_status, :jsonb, null: false, default: {}
  end
end
