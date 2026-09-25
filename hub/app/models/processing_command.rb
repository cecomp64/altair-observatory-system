# A Hub -> Altair instruction, polled by the node (§5.4).
class ProcessingCommand < ApplicationRecord
  KINDS = %w[assign_frames rerun night_include night_exclude issue_waive rereference set_mode
             approve_fetch deny_fetch equipment_event refresh_config night_ready].freeze
  STATES = %w[pending delivered succeeded failed cancelled].freeze

  belongs_to :processing_node
  belongs_to :requested_by, class_name: "User", optional: true
  belongs_to :target, optional: true

  validates :kind, inclusion: { in: KINDS }
  validates :state, inclusion: { in: STATES }

  scope :pending, -> { where(state: "pending") }
  scope :recent_first, -> { order(created_at: :desc) }

  def as_api_json
    { id: id, kind: kind, payload: payload, created_at: created_at.utc.iso8601 }
  end
end
