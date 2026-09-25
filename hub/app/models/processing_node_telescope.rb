class ProcessingNodeTelescope < ApplicationRecord
  belongs_to :processing_node
  belongs_to :telescope

  validates :telescope_id, uniqueness: { scope: :processing_node_id }
end
