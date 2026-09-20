class CreateTargetEvents < ActiveRecord::Migration[8.1]
  def change
    create_table :target_events do |t|
      t.references :target, null: false, foreign_key: true
      t.integer :event_type, null: false
      t.jsonb :payload, null: false, default: {}

      t.timestamps
    end
  end
end
