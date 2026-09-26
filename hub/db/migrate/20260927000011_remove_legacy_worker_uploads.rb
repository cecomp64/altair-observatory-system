# The rig agent no longer uploads or stacks frames (Altair owns all image
# data), so the legacy upload path goes: its data products, the upload URL,
# the target preview URL it set, and the files:write scope (api_revision 2).
class RemoveLegacyWorkerUploads < ActiveRecord::Migration[8.1]
  LEGACY_KINDS = [ 0, 1, 2, 3 ].freeze # sub, stacked, preview, log

  def up
    execute "UPDATE object_showcases SET data_product_id = NULL WHERE data_product_id IN (SELECT id FROM data_products WHERE kind IN (#{LEGACY_KINDS.join(', ')}))"
    execute "DELETE FROM data_products WHERE kind IN (#{LEGACY_KINDS.join(', ')})"
    remove_column :data_products, :url
    remove_column :targets, :preview_image_url
    execute "UPDATE api_keys SET scopes = array_remove(scopes, 'files:write')"
    execute "DELETE FROM target_events WHERE event_type = 1" # file_added
  end

  def down
    add_column :targets, :preview_image_url, :string
    add_column :data_products, :url, :string
  end
end
