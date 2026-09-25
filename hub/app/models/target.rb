class Target < ApplicationRecord
  belongs_to :user
  belongs_to :telescope

  has_many :exposure_plans, dependent: :destroy
  has_many :target_files, dependent: :destroy
  has_many :target_events, dependent: :destroy

  accepts_nested_attributes_for :exposure_plans, allow_destroy: true, reject_if: :all_blank

  enum :status, {
    draft: 0,
    submitted: 1,
    active: 2,
    in_progress: 3,
    completed: 4,
    cancelled: 5
  }

  # Statuses the worker should schedule in NINA Target Scheduler.
  SCHEDULABLE_STATUSES = %w[submitted active in_progress].freeze

  validates :name, presence: true
  validates :ra_deg, presence: true, numericality: { greater_than_or_equal_to: 0, less_than: 360 }
  validates :dec_deg, presence: true, numericality: { greater_than_or_equal_to: -90, less_than_or_equal_to: 90 }
  validates :exposure_plans, presence: { message: "must include at least one exposure plan" }, on: :submit

  scope :schedulable, -> { where(status: SCHEDULABLE_STATUSES) }
  scope :for_telescope, ->(telescope) { where(telescope: telescope) }

  def submit!
    update!(status: :submitted, submitted_at: Time.current)
  end

  def total_desired_exposures
    exposure_plans.sum(:desired_count)
  end

  def total_completed_exposures
    exposure_plans.sum(:completed_count)
  end

  def percent_complete
    return 0 if total_desired_exposures.zero?

    ((total_completed_exposures.to_f / total_desired_exposures) * 100).round(1)
  end

  def fully_captured?
    exposure_plans.any? && exposure_plans.all? { |ep| ep.completed_count >= ep.desired_count }
  end

  def ra_hours
    ra_deg / 15.0
  end
end
