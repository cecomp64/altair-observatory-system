# target_files become data_products: legacy worker uploads keep their url and
# kind, and Altair's masters gain archive pointers and metrics.
# kind enum: sub 0, stacked 1, preview 2, log 3 (unchanged) + night_master 4,
# multi_night_master 5, project_reference 6, provisional_noflat 7.
class RenameTargetFilesToDataProducts < ActiveRecord::Migration[8.1]
  def up
    rename_table :target_files, :data_products
    change_column_null :data_products, :url, true
    change_table :data_products do |t|
      t.references :project, foreign_key: true
      t.references :optical_train, foreign_key: true
      t.bigint :processing_node_id
      t.bigint :altair_id
      t.date :night
      t.integer :version
      t.string :sha256
      t.bigint :size_bytes
      t.string :archive_uri
      t.string :nas_path
      t.jsonb :metrics, null: false, default: {}
      t.bigint :superseded_by_id
    end
    add_index :data_products, :processing_node_id
    add_index :data_products, [ :processing_node_id, :kind, :altair_id ], unique: true, where: "altair_id IS NOT NULL",
      name: "index_data_products_on_node_kind_altair_id"
    execute "UPDATE data_products SET project_id = targets.project_id FROM targets WHERE targets.id = data_products.target_id"
  end

  def down
    remove_index :data_products, name: "index_data_products_on_node_kind_altair_id"
    change_table :data_products do |t|
      t.remove_references :project, :optical_train
      t.remove :processing_node_id, :altair_id, :night, :version, :sha256, :size_bytes, :archive_uri,
        :nas_path, :metrics, :superseded_by_id
    end
    execute "DELETE FROM data_products WHERE url IS NULL"
    change_column_null :data_products, :url, false
    rename_table :data_products, :target_files
  end
end
