class CreateTargets < ActiveRecord::Migration[8.1]
  def change
    create_table :targets do |t|
      t.references :user, null: false, foreign_key: true
      t.references :telescope, null: false, foreign_key: true
      t.string :name, null: false
      t.decimal :ra_deg, precision: 9, scale: 5, null: false
      t.decimal :dec_deg, precision: 9, scale: 5, null: false
      t.integer :status, null: false, default: 0
      t.integer :priority, null: false, default: 0
      t.text :notes
      t.string :preview_image_url
      t.datetime :submitted_at

      t.timestamps
    end
    add_index :targets, :status
  end
end
