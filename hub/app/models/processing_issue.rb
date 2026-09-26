# The Hub's projection of an Altair issue (Altair detects and resolves them;
# people waive them or approve fetches through commands).
class ProcessingIssue < ApplicationRecord
  # §7.4: kinds a project owner hears about (plus the telescope's admins);
  # everything else is infrastructure and goes to admins only.
  PROJECT_KINDS = %w[FLAT_MISSING DARK_MISSING BIAS_MISSING DARKFLAT_MISSING LOW_OVERLAP QUALITY_OUTLIER
                     STALE_REFERENCE PROJECT_UNRESOLVED ROTATOR_POSITION_UNKNOWN HEADER_INCOMPLETE].freeze

  belongs_to :processing_node
  belongs_to :telescope, optional: true
  belongs_to :optical_train, optional: true
  belongs_to :project, optional: true
  belongs_to :target, optional: true

  validates :fingerprint, presence: true, uniqueness: { scope: :processing_node_id }
  validates :severity, inclusion: { in: %w[blocking warning info] }
  validates :status, inclusion: { in: %w[open resolved waived] }
  validates :kind, :message, presence: true

  # Live updates: open pages re-render (Turbo morph) when an issue changes.
  broadcasts_refreshes
  after_commit :broadcast_to_lists

  scope :open, -> { where(status: "open") }
  scope :recent_first, -> { order(opened_at: :desc) }

  def broadcast_to_lists
    broadcast_refresh_later_to(:processing_issues)
    project&.broadcast_refresh_later
  end

  def project_scoped?
    PROJECT_KINDS.include?(kind)
  end

  def open?
    status == "open"
  end

  def fetch_approval?
    kind.start_with?("FETCH_") || kind.in?(%w[RESTORE_APPROVAL_REQUIRED LARGE_FETCH])
  end
end
