# Processing nodes (Altair installations) and scoped, polymorphic API keys.
# Existing keys become Telescope-owned with the worker scopes.
class CreateProcessingNodesAndScopeApiKeys < ActiveRecord::Migration[8.1]
  WORKER_SCOPES = %w[targets:read progress:write events:write sessions:write files:write heartbeat:write].freeze

  def up
    create_table :processing_nodes do |t|
      t.string :name, null: false, index: { unique: true }
      t.text :description
      t.boolean :active, null: false, default: true
      t.datetime :last_heartbeat_at
      t.jsonb :status, null: false, default: {}
      t.timestamps
    end

    create_table :processing_node_telescopes do |t|
      t.references :processing_node, null: false, foreign_key: true
      t.references :telescope, null: false, foreign_key: true
      t.timestamps
    end
    add_index :processing_node_telescopes, [ :processing_node_id, :telescope_id ], unique: true,
      name: "index_processing_node_telescopes_uniqueness"
    add_foreign_key :data_products, :processing_nodes

    add_column :api_keys, :owner_type, :string
    add_column :api_keys, :owner_id, :bigint
    add_column :api_keys, :scopes, :string, array: true, null: false, default: []
    scopes = "ARRAY[#{WORKER_SCOPES.map { |s| connection.quote(s) }.join(', ')}]::varchar[]"
    execute "UPDATE api_keys SET owner_type = 'Telescope', owner_id = telescope_id, scopes = #{scopes}"
    change_column_null :api_keys, :owner_type, false
    change_column_null :api_keys, :owner_id, false
    add_index :api_keys, [ :owner_type, :owner_id ]
    remove_foreign_key :api_keys, :telescopes
    remove_column :api_keys, :telescope_id
  end

  def down
    add_reference :api_keys, :telescope, foreign_key: true
    execute "DELETE FROM api_keys WHERE owner_type <> 'Telescope'"
    execute "UPDATE api_keys SET telescope_id = owner_id"
    change_column_null :api_keys, :telescope_id, false
    remove_index :api_keys, [ :owner_type, :owner_id ]
    remove_column :api_keys, :scopes
    remove_column :api_keys, :owner_id
    remove_column :api_keys, :owner_type
    remove_foreign_key :data_products, :processing_nodes
    drop_table :processing_node_telescopes
    drop_table :processing_nodes
  end
end
