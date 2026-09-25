# A result: an Altair master (night, multi-night, reference, provisional) or
# a legacy worker upload (sub, stacked, preview, log). Was TargetFile.
class DataProduct < ApplicationRecord
  LEGACY_KINDS = %w[sub stacked preview log].freeze
  ALTAIR_KINDS = %w[night_master multi_night_master project_reference provisional_noflat].freeze

  belongs_to :target
  belongs_to :project, optional: true
  belongs_to :optical_train, optional: true
  belongs_to :processing_node, optional: true
  belongs_to :superseded_by, class_name: "DataProduct", optional: true

  enum :kind, {
    sub: 0, stacked: 1, preview: 2, log: 3,
    night_master: 4, multi_night_master: 5, project_reference: 6, provisional_noflat: 7
  }

  before_validation { self.project_id ||= target&.project_id }

  # Legacy uploads come from the worker API and are rendered as links, so only
  # http(s) is allowed (no javascript: or data: URLs).
  validates :url, presence: true, if: :legacy?
  validates :url, format: { with: %r{\Ahttps?://\S+\z}i, message: "must be an http(s) URL" }, allow_nil: true

  scope :recent_first, -> { order(captured_at: :desc, created_at: :desc) }
  scope :legacy, -> { where(kind: LEGACY_KINDS) }
  scope :masters, -> { where(kind: ALTAIR_KINDS) }
  scope :current, -> { where(superseded_by_id: nil) }

  def legacy?
    LEGACY_KINDS.include?(kind)
  end
end
