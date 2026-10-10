class ProcessingJob < ApplicationRecord
  # As in contracts/schemas/processing/job.request.json. Not validated here:
  # the API is additive, so Altair may report values the Hub doesn't know yet.
  KINDS = %w[CALIB_MASTER NIGHT_STACK PROJECT_REFERENCE MERGE].freeze
  STATUSES = %w[queued staging waiting_data running succeeded failed skipped blocked].freeze
  ACTIVE_STATUSES = %w[queued staging waiting_data running].freeze

  belongs_to :processing_node
  belongs_to :target, optional: true

  validates :altair_id, uniqueness: { scope: :processing_node_id }
  validates :kind, :status, presence: true

  def active?
    ACTIVE_STATUSES.include?(status)
  end

  # Seconds spent so far, or in total once finished; nil before it starts.
  def duration
    return unless started_at

    (finished_at || (active? ? Time.current : nil))&.-(started_at)
  end
end
