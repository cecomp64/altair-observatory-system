class CreateTargetFiles < ActiveRecord::Migration[8.1]
  def change
    create_table :target_files do |t|
      t.references :target, null: false, foreign_key: true
      t.string :url, null: false
      t.integer :kind, null: false, default: 0
      t.string :filter
      t.datetime :captured_at

      t.timestamps
    end
  end
end
