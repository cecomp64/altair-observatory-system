# A member's imaging goal: one or more targets, with a priority, status and
# processing settings (the astrophotography-database "project").
class Project < ApplicationRecord
  # Altair's defaults (SPEC §5); a project's and a target's settings override them.
  DEFAULT_PROCESSING_SETTINGS = {
    "multi_night" => { "enabled" => true, "mode" => "master_merge" },
    "reference_filter" => nil,
    "drizzle_scale" => 1,
    "keep_calibrated_frames" => true,
    "pin_to_nas" => false,
    "min_lights_per_stack" => 5,
    "max_fwhm_ratio_to_project_median" => 1.6,
    "wbpp_profile" => "default"
  }.freeze

  belongs_to :user
  has_many :targets, dependent: :destroy
  has_many :exposure_plans, through: :targets
  has_many :data_products, dependent: :nullify

  enum :status, { planning: "planning", active: "active", paused: "paused", completed: "completed", archived: "archived" },
    validate: true
  enum :visibility, { private: "private", club: "club" }, prefix: :visibility, validate: true
  enum :completion_basis, { acquired: "acquired", integrated: "integrated" }, prefix: :completion, validate: true

  validates :name, presence: true
  validates :priority, numericality: { only_integer: true }
  validate :processing_settings_is_a_hash

  scope :recent_first, -> { order(updated_at: :desc) }

  # Target Scheduler project name (§4.4).
  def ts_project_name
    "#P#{id} #{name}"
  end

  def primary_target
    targets.find(&:is_primary?) || targets.min_by(&:id)
  end

  def effective_processing_settings
    Project.deep_merge_settings(DEFAULT_PROCESSING_SETTINGS, processing_settings)
  end

  def self.deep_merge_settings(base, override)
    base.deep_stringify_keys.deep_merge((override || {}).deep_stringify_keys) { |_key, old, new| new.nil? ? old : new }
  end

  private

  def processing_settings_is_a_hash
    errors.add(:processing_settings, "must be an object") unless processing_settings.is_a?(Hash)
  end
end
