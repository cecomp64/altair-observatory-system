# A result reported by Altair: a night master, multi-night master version,
# project reference or provisional (no-flat) master. Was TargetFile.
class DataProduct < ApplicationRecord
  MASTER_KINDS = %w[night_master multi_night_master project_reference provisional_noflat].freeze
  # masters_bundle (api_revision 4): a target's zip of its latest multi-night masters.
  ALTAIR_KINDS = (MASTER_KINDS + %w[masters_bundle]).freeze

  belongs_to :target
  belongs_to :project, optional: true
  belongs_to :optical_train, optional: true
  belongs_to :processing_node, optional: true
  belongs_to :superseded_by, class_name: "DataProduct", optional: true
  has_one_attached :preview
  has_one_attached :thumbnail
  # Altair's Markdown report (api_revision 3), kept in Active Storage like the
  # previews (local disk or S3), never in the database.
  has_one_attached :report

  # 0–3 were the legacy worker uploads (removed in api_revision 2).
  enum :kind, {
    night_master: 4, multi_night_master: 5, project_reference: 6, provisional_noflat: 7, masters_bundle: 8
  }

  before_validation { self.project_id ||= target&.project_id }

  # Live updates: the target and project pages re-render with the new master.
  after_commit do
    target.broadcast_refresh_later
    project&.broadcast_refresh_later
  end

  scope :recent_first, -> { order(captured_at: :desc, created_at: :desc) }
  scope :masters, -> { where(kind: MASTER_KINDS) }
  scope :current, -> { where(superseded_by_id: nil) }

  # The night's calibrated subs, one zip in the archive (night masters only).
  def calibrated_bundle_uri
    calibrated_bundle&.dig("archive_uri")
  end
end
