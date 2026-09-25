class CalibrationMaster < ApplicationRecord
  KINDS = %w[bias dark flat darkflat].freeze

  belongs_to :processing_node
  belongs_to :optical_train

  validates :altair_id, uniqueness: { scope: :processing_node_id }
  validates :kind, inclusion: { in: KINDS }
  validates :sha256, presence: true

  scope :current, -> { where(superseded: false) }
end
