class Target < ApplicationRecord
  belongs_to :user
  belongs_to :telescope
  belongs_to :project
  belongs_to :astro_object, optional: true
  belongs_to :optical_train, optional: true

  has_many :exposure_plans, dependent: :destroy
  has_many :data_products, dependent: :destroy
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

  # Targets created without a project (the legacy single-target paths) get
  # one of their own, like the migration backfill does for existing rows.
  before_validation :ensure_project, on: :create
  before_validation :default_user_from_project

  validates :name, presence: true
  validates :ra_deg, presence: true, numericality: { greater_than_or_equal_to: 0, less_than: 360 }
  validates :dec_deg, presence: true, numericality: { greater_than_or_equal_to: -90, less_than_or_equal_to: 90 }
  validates :exposure_plans, presence: { message: "must include at least one exposure plan" }, on: :submit
  validates :min_altitude_deg, numericality: { greater_than_or_equal_to: 0, less_than: 90 }, allow_nil: true
  validate :user_owns_project
  validate :optical_train_belongs_to_telescope

  scope :schedulable, -> { where(status: SCHEDULABLE_STATUSES) }
  scope :for_telescope, ->(telescope) { where(telescope: telescope) }
  scope :not_draft, -> { where.not(status: :draft) }

  def submit!
    update!(status: :submitted, submitted_at: Time.current)
  end

  # NINA / Target Scheduler target name (§4.4). NINA writes it into OBJECT,
  # which is how Altair links frames back to this target.
  def nina_name
    "##{id} #{name}"
  end

  def effective_optical_train
    optical_train || telescope.default_optical_train
  end

  def effective_min_altitude_deg
    (min_altitude_deg || telescope.min_altitude_deg).to_f
  end

  def effective_processing_settings
    Project.deep_merge_settings(project.effective_processing_settings, processing_settings)
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

  # Complete by the project's completion basis (§4.3): acquired frames
  # (Target Scheduler accepted) or frames integrated into masters.
  def fully_captured?
    counter = project&.completion_integrated? ? :integrated_count : :completed_count
    exposure_plans.any? && exposure_plans.all? { |ep| ep.public_send(counter) >= ep.desired_count }
  end

  def ra_hours
    ra_deg / 15.0
  end

  private

  def ensure_project
    return if project.present? || user.blank?

    self.project = Project.new(user: user, name: name.presence || "Untitled", priority: priority || 0,
                               status: draft? ? "planning" : "active")
  end

  def default_user_from_project
    self.user ||= project&.user
  end

  def user_owns_project
    errors.add(:user, "must own the project") if project && user && project.user_id != user.id
  end

  def optical_train_belongs_to_telescope
    return unless optical_train && telescope

    errors.add(:optical_train, "must belong to the target's telescope") if optical_train.telescope_id != telescope.id
  end
end
