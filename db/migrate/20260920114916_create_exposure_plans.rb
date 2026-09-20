class CreateExposurePlans < ActiveRecord::Migration[8.1]
  def change
    create_table :exposure_plans do |t|
      t.references :target, null: false, foreign_key: true
      t.string :filter, null: false
      t.integer :exposure_seconds, null: false
      t.integer :desired_count, null: false, default: 1
      t.integer :completed_count, null: false, default: 0

      t.timestamps
    end
  end
end
