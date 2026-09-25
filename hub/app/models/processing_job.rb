class ProcessingJob < ApplicationRecord
  belongs_to :processing_node
  belongs_to :target, optional: true

  validates :altair_id, uniqueness: { scope: :processing_node_id }
  validates :kind, :status, presence: true
end
