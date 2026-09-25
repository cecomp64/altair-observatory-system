# Projects sit above targets. Every existing target gets its own project
# (same name and owner, status mapped from the target), then project_id
# becomes required.
class CreateProjects < ActiveRecord::Migration[8.1]
  def up
    create_table :projects do |t|
      t.references :user, null: false, foreign_key: true
      t.string :name, null: false
      t.text :description
      t.string :status, null: false, default: "planning"
      t.integer :priority, null: false, default: 0
      t.string :visibility, null: false, default: "private"
      t.string :completion_basis, null: false, default: "acquired"
      t.jsonb :processing_settings, null: false, default: {}
      t.bigint :legacy_target_id # set only by the backfill below
      t.timestamps
    end
    add_index :projects, :status

    change_table :targets do |t|
      t.references :project, foreign_key: true
      t.references :astro_object, foreign_key: true
      t.references :optical_train, foreign_key: true
      t.decimal :rotation_deg, precision: 6, scale: 2
      t.string :panel
      t.boolean :is_primary, null: false, default: true
      t.decimal :min_altitude_deg, precision: 5, scale: 2
      t.jsonb :processing_settings, null: false, default: {}
      t.string :schedule_count_basis
    end

    # Target status enum: draft 0, submitted 1, active 2, in_progress 3, completed 4, cancelled 5.
    execute <<~SQL
      INSERT INTO projects (user_id, name, status, priority, legacy_target_id, created_at, updated_at)
      SELECT user_id, name,
             CASE status WHEN 0 THEN 'planning' WHEN 4 THEN 'completed' WHEN 5 THEN 'archived' ELSE 'active' END,
             priority, id, created_at, NOW()
      FROM targets;
      UPDATE targets SET project_id = projects.id FROM projects WHERE projects.legacy_target_id = targets.id;
    SQL
    change_column_null :targets, :project_id, false
    remove_column :projects, :legacy_target_id
  end

  def down
    change_table :targets do |t|
      t.remove_references :project, :astro_object, :optical_train
      t.remove :rotation_deg, :panel, :is_primary, :min_altitude_deg, :processing_settings, :schedule_count_basis
    end
    drop_table :projects
  end
end
