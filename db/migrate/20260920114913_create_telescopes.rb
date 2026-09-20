class CreateTelescopes < ActiveRecord::Migration[8.1]
  def change
    create_table :telescopes do |t|
      t.string :name, null: false
      t.string :slug, null: false
      t.decimal :latitude, precision: 8, scale: 5, null: false
      t.decimal :longitude, precision: 8, scale: 5, null: false
      t.decimal :elevation_m, precision: 7, scale: 2
      t.boolean :active, null: false, default: true
      t.boolean :self_serve_submit, null: false, default: false
      t.text :description

      t.timestamps
    end
    add_index :telescopes, :slug, unique: true
  end
end
