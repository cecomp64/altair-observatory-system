# Migration 10 (§4.2): projections of Altair's issues and jobs, and the
# Hub -> Altair command queue.
class CreateProcessingIssuesJobsCommands < ActiveRecord::Migration[8.1]
  def change
    create_table :processing_issues do |t|
      t.references :processing_node, null: false, foreign_key: true, index: false
      t.string :fingerprint, null: false
      t.bigint :altair_id
      t.string :kind, null: false
      t.string :severity, null: false
      t.string :status, null: false, default: "open"
      t.text :message, null: false
      t.jsonb :requirement
      t.jsonb :scope, null: false, default: {}
      t.references :telescope, foreign_key: true
      t.references :optical_train, foreign_key: true
      t.references :project, foreign_key: true
      t.references :target, foreign_key: true
      t.date :night
      t.string :filter
      t.datetime :opened_at, null: false
      t.datetime :resolved_at
      t.string :resolution
      t.datetime :last_notified_at
      t.timestamps
    end
    add_index :processing_issues, [ :processing_node_id, :fingerprint ], unique: true
    add_index :processing_issues, :status

    create_table :processing_jobs do |t|
      t.references :processing_node, null: false, foreign_key: true, index: false
      t.bigint :altair_id, null: false
      t.string :kind, null: false
      t.string :status, null: false
      t.references :target, foreign_key: true
      t.date :night
      t.string :filter
      t.datetime :started_at
      t.datetime :finished_at
      t.text :error
      t.timestamps
    end
    add_index :processing_jobs, [ :processing_node_id, :altair_id ], unique: true

    create_table :processing_commands do |t|
      t.references :processing_node, null: false, foreign_key: true
      t.string :kind, null: false
      t.jsonb :payload, null: false, default: {}
      t.references :requested_by, foreign_key: { to_table: :users }
      t.references :target, foreign_key: true
      t.string :state, null: false, default: "pending"
      t.jsonb :result
      t.datetime :delivered_at
      t.datetime :completed_at
      t.timestamps
    end
    add_index :processing_commands, [ :processing_node_id, :state ]
  end
end
